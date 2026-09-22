"""契约测试：request_logging_middleware 默认关 + token 脱敏大小写不敏感。

对应日志 2.2GB 膨胀修复（L1 token 脱敏 + L2 请求日志默认关/轮询排除）。
"""

import pytest


def test_sensitive_headers_set_is_case_insensitive():
    """_SENSITIVE_HEADERS 应覆盖 Starlette 转小写后的键名。"""
    from vermes_cli.web_server import _SENSITIVE_HEADERS

    # Starlette Headers 的 dict() 会把键转成全小写
    assert "x-vermes-session-token" in _SENSITIVE_HEADERS
    assert "authorization" in _SENSITIVE_HEADERS
    # 大小写不敏感：无论传入什么大小写，lower 后都能命中
    lowered = {k.lower() for k in _SENSITIVE_HEADERS}
    assert "x-vermes-session-token" in lowered
    assert "x-vermes-session-token".lower() in lowered
    assert "x-Vermes-session-token".lower() in lowered


def test_sensitive_body_keys_cover_common_secrets():
    """_SENSITIVE_BODY_KEYS 应覆盖 api_key/token/secret 等常见字段。"""
    from vermes_cli.web_server import _SENSITIVE_BODY_KEYS

    for k in ("api_key", "token", "secret", "password", "access_token",
              "refresh_token", "client_secret"):
        assert k in _SENSITIVE_BODY_KEYS


def test_poll_paths_excluded():
    """轮询路径应被排除（不再逐请求刷日志）。"""
    from vermes_cli.web_server import _HTTP_DEBUG_POLL_PATHS

    assert "/api/sessions" in _HTTP_DEBUG_POLL_PATHS
    assert "/api/evolution" in _HTTP_DEBUG_POLL_PATHS
    assert "/api/memory" in _HTTP_DEBUG_POLL_PATHS
    assert "/api/health" in _HTTP_DEBUG_POLL_PATHS


def test_http_debug_disabled_by_default(monkeypatch):
    """默认（无 env、无 config）应关闭 http_debug。"""
    from vermes_cli.web_server import _http_debug_enabled

    monkeypatch.delenv("VERMES_HTTP_DEBUG", raising=False)
    # config 默认 http_debug=False → 关闭
    assert _http_debug_enabled() is False


def test_http_debug_enabled_via_env(monkeypatch):
    """VERMES_HTTP_DEBUG=1 应开启。"""
    from vermes_cli.web_server import _http_debug_enabled

    monkeypatch.setenv("VERMES_HTTP_DEBUG", "1")
    assert _http_debug_enabled() is True
    monkeypatch.setenv("VERMES_HTTP_DEBUG", "0")
    assert _http_debug_enabled() is False
