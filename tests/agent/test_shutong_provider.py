"""书童 shutong 专用适配器测试：SSO JWT 获取 + 可配置检索端点。"""
from __future__ import annotations

import os
from unittest import mock

from agent.literature_custom_store import register_source_from_credential_block
from agent.literature_providers.shutong import ShutongProvider

SHUTONG_BLOCK = """卡号：83219570
密码：335779
【使用方法】
复制网址http://3.shutong2.com/ 到浏览器登录即可。"""

_SON = (
    '<html><head><meta content="always" name="referrer"/></head>'
    "<body><script>location.href='https://api88.wenxian.shop/token?"
    "sign_key=abc&rndtoken=1&sign_token=eyJhbGciOiJIUzI1NiJ9.abc.def'</script></body></html>"
)


def _provider(env_prefix: str, **overrides):
    defn = {
        "id": env_prefix.lower(), "label": "test", "base_url": "http://3.shutong2.com",
        "provider_type": "shutong", "auth_scheme": "form",
        "login_url": "http://3.shutong2.com/e/member/doaction.php",
        "login_user_field": "username", "login_password_field": "password",
        "search_url": "http://3.shutong2.com", "method": "GET", "query_param": "q",
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


def test_acquire_sso_token_parses_jwt(monkeypatch):
    prov = _provider("SHUT_T1")
    try:
        monkeypatch.setattr(
            "agent.literature_providers.shutong.http_login_then_get",
            lambda **kw: {"ok": True, "status": 200, "text": _SON},
        )
        r = prov._acquire_sso_token()
        assert r["ok"] is True
        assert r["token"].startswith("eyJ")
        assert "sign_token" in r["token"] or r["token"] == "eyJhbGciOiJIUzI1NiJ9.abc.def"
    finally:
        for k in ("LIT_SHUT_T1_USER", "LIT_SHUT_T1_PASSWORD", "LIT_SHUT_T1_BASE_URL"):
            os.environ.pop(k, None)


def test_acquire_sso_token_no_redirect(monkeypatch):
    prov = _provider("SHUT_T2")
    try:
        monkeypatch.setattr(
            "agent.literature_providers.shutong.http_login_then_get",
            lambda **kw: {"ok": True, "status": 200, "text": "<html>无重定向</html>"},
        )
        r = prov._acquire_sso_token()
        assert r["ok"] is False
        assert "重定向" in r["error"] or "sign_token" in r["error"]
    finally:
        for k in ("LIT_SHUT_T2_USER", "LIT_SHUT_T2_PASSWORD", "LIT_SHUT_T2_BASE_URL"):
            os.environ.pop(k, None)


def test_search_requires_configured_endpoint(monkeypatch):
    # search_url 未配置（== base_url）→ 返回可执行提示，而非静默失败
    prov = _provider("SHUT_T3")
    try:
        monkeypatch.setattr(
            "agent.literature_providers.shutong.http_login_then_get",
            lambda **kw: {"ok": True, "status": 200, "text": _SON},
        )
        res = prov.search("深度学习", limit=10)
        assert res["success"] is False
        assert "检索端点" in res["error"]
    finally:
        for k in ("LIT_SHUT_T3_USER", "LIT_SHUT_T3_PASSWORD", "LIT_SHUT_T3_BASE_URL"):
            os.environ.pop(k, None)


def test_search_calls_configured_endpoint_with_bearer(monkeypatch):
    captured = {}
    prov = _provider("SHUT_T4", search_url="https://api88.wenxian.shop/search")
    try:
        monkeypatch.setattr(
            "agent.literature_providers.shutong.http_login_then_get",
            lambda **kw: {"ok": True, "status": 200, "text": _SON},
        )
        def fake_get(url, params=None, headers=None, timeout=20):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return {"ok": True, "status": 200, "data": {"results": [
                {"title": "深度学习综述", "authors": ["张三"], "year": 2020, "journal": "AI"}
            ]}}
        monkeypatch.setattr("agent.literature_providers.shutong.http_get_json", fake_get)
        res = prov.search("深度学习", limit=10)
        assert res["success"] is True
        papers = res["data"]["papers"]
        assert len(papers) == 1
        assert papers[0]["title"] == "深度学习综述"
        # JWT 作为 Bearer 携带
        assert captured["headers"]["Authorization"].startswith("Bearer eyJ")
        assert captured["params"]["q"] == "深度学习"
    finally:
        for k in ("LIT_SHUT_T4_USER", "LIT_SHUT_T4_PASSWORD", "LIT_SHUT_T4_BASE_URL"):
            os.environ.pop(k, None)


def test_register_detects_shutong_sets_provider_type(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent.literature_custom_store._resolve_store_path",
        lambda: tmp_path / "literature_custom_sources.json",
    )
    res = register_source_from_credential_block(SHUTONG_BLOCK, persist_credentials=False)
    assert res["success"] is True
    assert res["auth_scheme"] == "form"
    import json
    data = json.loads((tmp_path / "literature_custom_sources.json").read_text(encoding="utf-8"))
    assert data[0]["provider_type"] == "shutong"
    assert data[0]["login_extra_fields"].get("enews") == "login"
    assert data[0]["login_extra_fields"].get("ecmsfrom") == "/zhongwenku/"
    assert data[0]["sso_url"].endswith("/l77.php")
