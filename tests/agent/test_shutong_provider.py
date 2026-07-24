"""书童 shutong 专用适配器测试。

覆盖实证链路：EmpireCMS 登录 → /l77.php 解析 SSO 跳转 → 跟随 302 发现动态 KNS8
镜像 → POST /kns8s/brief/grid 标准知网检索 → 解析 result-table-list。
网络全部 mock（不触真机、不写 ~/.vermes）。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

from agent.literature_custom_store import register_source_from_credential_block
from agent.literature_providers.shutong import ShutongProvider, _parse_shutong_grid

SHUTONG_BLOCK = """卡号：83219570
密码：335779
【使用方法】
复制网址http://3.shutong2.com/ 到浏览器登录即可。"""

# /l77.php 返回的 JS 跳转（含真实 token URL）
_L77_HTML = (
    "<html><head></head><body><script>location.href="
    "'https://api88.wenxian.shop/token?sign_key=abc&rndtoken=1&"
    "sign_token=eyJhbGciOiJIUzI1NiJ9.abc.def'</script></body></html>"
)

# KNS8 检索结果 HTML（基于真机结构裁剪）
_GRID_HTML = """
<div id="briefBox"><table class="result-table-list"><tbody>
<tr><th class="seq">序号</th><th>篇名</th><th>作者</th></tr>
<tr>
  <td class="seq">1</td>
  <td class="name"><a class="fz14" href="https://kns.cnki.net/kcms2/article/abstract?v=X"
      title="深度学习综述">深度学习综述</a></td>
  <td class="author"><a>张三</a><a>李四</a></td>
  <td class="source">人工智能学报</td>
  <td class="date">2020-03-15</td>
  <td class="data">期刊</td>
</tr>
<tr>
  <td class="seq">2</td>
  <td class="name"><a class="fz14" href="/x2" title="神经网络优化">神经网络优化</a></td>
  <td class="author"><a>王五</a></td>
  <td class="source">计算机学报</td>
  <td class="date">2019-11-01</td>
  <td class="data">期刊</td>
</tr>
</tbody></table></div>
"""

_MIRROR = "http://42.192.101.93:4455"


class _FakeResp:
    def __init__(self, text="", url="", status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code


class _FakeClient:
    """按 URL 分派的假 httpx.Client，复刻 shutong 检索链路。"""

    def __init__(self, *, grid_html=_GRID_HTML, grid_status=200, mirror=_MIRROR,
                 l77_html=_L77_HTML, mirror_url=None):
        self.calls = []
        self._grid_html = grid_html
        self._grid_status = grid_status
        self._mirror = mirror
        self._l77_html = l77_html
        self._mirror_url = mirror_url or (mirror + "/kns8/defaultresult/index")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, data=None, headers=None):
        self.calls.append(("POST", url, data))
        if "doaction.php" in url:  # login
            return _FakeResp(text="登录成功!", url=url)
        if "brief/grid" in url:  # search
            return _FakeResp(text=self._grid_html, url=url, status_code=self._grid_status)
        return _FakeResp(text="", url=url)

    def get(self, url, headers=None):
        self.calls.append(("GET", url, None))
        if "l77.php" in url:  # SSO 入口
            return _FakeResp(text=self._l77_html, url=url)
        if "api88" in url or "token" in url:  # token → 跟随 302 到镜像
            return _FakeResp(text="", url=self._mirror_url)
        return _FakeResp(text="", url=url)


def _provider(env_prefix: str, **overrides):
    defn = {
        "id": env_prefix.lower(), "label": "书童图书馆", "base_url": "http://3.shutong2.com",
        "provider_type": "shutong", "auth_scheme": "form",
        "login_url": "http://3.shutong2.com/e/member/doaction.php",
        "login_user_field": "username", "login_password_field": "password",
        "fields": [
            {"key": f"LIT_{env_prefix}_USER", "kind": "user", "label": "账号", "secret": False},
            {"key": f"LIT_{env_prefix}_PASSWORD", "kind": "password", "label": "密码", "secret": True},
            {"key": f"LIT_{env_prefix}_BASE_URL", "kind": "base_url", "label": "网关", "secret": False},
        ],
    }
    defn.update(overrides)
    os.environ[f"LIT_{env_prefix}_USER"] = "83219570"
    os.environ[f"LIT_{env_prefix}_PASSWORD"] = "335779"
    os.environ[f"LIT_{env_prefix}_BASE_URL"] = "http://3.shutong2.com"
    return ShutongProvider(defn)


def _cleanup(env_prefix):
    for suf in ("USER", "PASSWORD", "BASE_URL"):
        os.environ.pop(f"LIT_{env_prefix}_{suf}", None)


# ── pure units (no network) ─────────────────────────────────────────────────

def test_extract_redirect_prefers_token_url():
    u = ShutongProvider._extract_redirect(_L77_HTML)
    assert u.startswith("https://api88.wenxian.shop/token")
    assert "sign_token=" in u


def test_extract_redirect_no_match_returns_empty():
    assert ShutongProvider._extract_redirect("<html>无跳转</html>") == ""


def test_build_query_json_uses_subject_field():
    prov = _provider("SHUT_Q1")
    try:
        qj = json.loads(prov._build_query_json("人工智能"))
        item = qj["QNode"]["QGroup"][0]["Items"][0]
        assert item["Field"] == "SU"
        assert item["Value"] == "人工智能"
        assert qj["Resource"] == "CROSSDB"
    finally:
        _cleanup("SHUT_Q1")


def test_parse_grid_extracts_papers():
    papers = _parse_shutong_grid(_GRID_HTML, limit=10)
    assert len(papers) == 2
    assert papers[0]["title"] == "深度学习综述"
    assert papers[0]["authors"] == ["张三", "李四"]
    assert papers[0]["venue"] == "人工智能学报"
    assert papers[0]["year"] == "2020"
    assert papers[0]["type"] == "期刊"
    assert papers[0]["url"].startswith("https://kns.cnki.net")
    assert papers[0]["source"] == "shutong"
    # 表头行被跳过
    assert all(p["title"] not in ("序号", "篇名") for p in papers)


def test_parse_grid_respects_limit():
    assert len(_parse_shutong_grid(_GRID_HTML, limit=1)) == 1


# ── full search flow (mocked httpx) ─────────────────────────────────────────

def test_search_full_flow_returns_papers(monkeypatch):
    prov = _provider("SHUT_S1")
    fake = _FakeClient()
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        res = prov.search("人工智能", limit=10)
        assert res["success"] is True
        data = res["data"]
        assert data["count"] == 2
        assert data["papers"][0]["title"] == "深度学习综述"
        # 检索确实打到动态发现的镜像上
        posted = [c for c in fake.calls if c[0] == "POST" and "brief/grid" in c[1]]
        assert posted and posted[0][1] == _MIRROR + "/kns8s/brief/grid"
        # QueryJson 携带查询词
        assert "人工智能" in posted[0][2]["QueryJson"]
    finally:
        _cleanup("SHUT_S1")


def test_search_captcha_gate_returns_error(monkeypatch):
    prov = _provider("SHUT_S2")
    # 镜像把检索重定向到验证码页
    fake = _FakeClient(grid_html="<html>captchaType=blockPuzzle</html>",
                       mirror_url=_MIRROR + "/kns8/defaultresult/index")
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        res = prov.search("人工智能", limit=5)
        assert res["success"] is False
        assert "验证码" in res["error"]
    finally:
        _cleanup("SHUT_S2")


def test_search_no_sso_redirect_returns_error(monkeypatch):
    prov = _provider("SHUT_S3")
    fake = _FakeClient(l77_html="<html>登录失败，无跳转</html>")
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        res = prov.search("人工智能", limit=5)
        assert res["success"] is False
        assert "SSO" in res["error"] or "跳转" in res["error"]
    finally:
        _cleanup("SHUT_S3")


# ── registration detection ──────────────────────────────────────────────────

def test_register_detects_shutong_sets_provider_type(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.literature_custom_store._resolve_store_path",
        lambda: tmp_path / "literature_custom_sources.json",
    )
    res = register_source_from_credential_block(SHUTONG_BLOCK, persist_credentials=False)
    assert res["success"] is True
    assert res["auth_scheme"] == "form"
    data = json.loads((tmp_path / "literature_custom_sources.json").read_text(encoding="utf-8"))
    assert data[0]["provider_type"] == "shutong"
    assert data[0]["login_extra_fields"].get("enews") == "login"
    assert data[0]["login_extra_fields"].get("ecmsfrom") == "/zhongwenku/"
    assert data[0]["sso_url"].endswith("/l77.php")
