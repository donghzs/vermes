"""文献云图书馆 wenx / ccki 同族适配器测试。

覆盖与 shutong 的差异点：
- SSO 入口 ``/cs00.php`` **直接 302** 到 KNS8 镜像（direct_302 模式）
- 知网频道常需购买群组开通：未开通时入口停在门户域，给出友好错误（channel_gate）
- 会话缓存：同一 provider 实例多次查询复用登录态，不反复登录（防封号）
网络全部 mock。
"""
from __future__ import annotations

import json
import os

from agent.literature_providers.wenx import WenxProvider
from agent.literature_registry import _is_wenx_source

_PORTAL = "https://lib.wenx.top"
_MIRROR = "http://180.76.102.59:9299"

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
</tbody></table></div>
"""


class _FakeResp:
    def __init__(self, text="", url="", status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code


class _FakeClient:
    """按 URL 分派的假 httpx.Client，复刻 wenx direct_302 检索链路。"""

    def __init__(self, *, grid_html=_GRID_HTML, grid_status=200, mirror=_MIRROR,
                 sso_redirect=None):
        self.calls = []
        # 模拟 httpx cookies 容器，供会话缓存逻辑存取
        self.cookies = object()
        self._grid_html = grid_html
        self._grid_status = grid_status
        self._mirror = mirror
        # 入口最终落点：默认直接跳到镜像；channel_gate 场景停在门户购买页
        self._sso_url = sso_redirect or (mirror + "/kns8/defaultresult/index")

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
        if "cs00.php" in url:  # 直接 302 到镜像/购买页
            return _FakeResp(text="", url=self._sso_url)
        return _FakeResp(text="", url=url)


def _provider(env_prefix: str, **overrides):
    defn = {
        "id": env_prefix.lower(), "label": "文献云图书馆", "base_url": _PORTAL,
        "provider_type": "wenx", "auth_scheme": "form",
        "login_url": _PORTAL + "/e/member/doaction.php",
        "login_user_field": "username", "login_password_field": "password",
        "fields": [
            {"key": f"LIT_{env_prefix}_USER", "kind": "user", "label": "账号", "secret": False},
            {"key": f"LIT_{env_prefix}_PASSWORD", "kind": "password", "label": "密码", "secret": True},
            {"key": f"LIT_{env_prefix}_BASE_URL", "kind": "base_url", "label": "网关", "secret": False},
        ],
    }
    defn.update(overrides)
    os.environ[f"LIT_{env_prefix}_USER"] = "1555333207"
    os.environ[f"LIT_{env_prefix}_PASSWORD"] = "522120"
    os.environ[f"LIT_{env_prefix}_BASE_URL"] = _PORTAL
    return WenxProvider(defn)


def _cleanup(env_prefix):
    for suf in ("USER", "PASSWORD", "BASE_URL"):
        os.environ.pop(f"LIT_{env_prefix}_{suf}", None)


# ── config inheritance ───────────────────────────────────────────────────────

def test_config_inherits_direct_302():
    prov = _provider("WENX_C1")
    try:
        assert prov._sso_path == "/cs00.php"
        assert prov._sso_mode == "direct_302"
        assert prov._channel_gate is True
        assert prov._source_tag == "wenx"
        assert prov._login_user_field == "username"
    finally:
        _cleanup("WENX_C1")


def test_is_wenx_source_detection():
    assert _is_wenx_source({"provider_type": "wenx"}) is True
    assert _is_wenx_source({"provider_type": "ccki"}) is True
    assert _is_wenx_source({"id": "s_3_lib_wenx_top"}) is True
    assert _is_wenx_source({"label": "ccki 文献"}) is True
    assert _is_wenx_source({"provider_type": "shutong"}) is False
    assert _is_wenx_source({"id": "generic_open"}) is False


# ── full search flow (direct_302 mock) ────────────────────────────────────────

def test_search_full_flow_direct_302(monkeypatch):
    prov = _provider("WENX_S1")
    fake = _FakeClient()
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        res = prov.search("人工智能", limit=10)
        assert res["success"] is True
        data = res["data"]
        assert data["count"] == 1
        assert data["papers"][0]["title"] == "深度学习综述"
        assert data["papers"][0]["source"] == "wenx"
        posted = [c for c in fake.calls if c[0] == "POST" and "brief/grid" in c[1]]
        assert posted and posted[0][1] == _MIRROR + "/kns8s/brief/grid"
        assert "人工智能" in posted[0][2]["QueryJson"]
    finally:
        _cleanup("WENX_S1")


def test_channel_gate_returns_friendly_error(monkeypatch):
    prov = _provider("WENX_S2")
    # 未开通：入口停在门户域的购买群组页
    fake = _FakeClient(sso_redirect=_PORTAL + "/e/member/buygroup/")
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        res = prov.search("人工智能", limit=5)
        assert res["success"] is False
        assert res.get("channel_gate") is True
        assert "开通" in res["error"]
    finally:
        _cleanup("WENX_S2")


def test_session_cache_reuses_mirror(monkeypatch):
    prov = _provider("WENX_S3")
    fake = _FakeClient()
    try:
        monkeypatch.setattr("httpx.Client", lambda *a, **k: fake)
        # 第一次查询：建立会话 + 检索
        prov.search("人工智能", limit=5)
        # 第二次查询：应复用缓存登录态，不再触发登录 POST
        prov.search("神经网络", limit=5)
        logins = [c for c in fake.calls if c[0] == "POST" and "doaction.php" in c[1]]
        assert len(logins) == 1, f"期望只登录一次，实际 {len(logins)} 次"
    finally:
        _cleanup("WENX_S3")
