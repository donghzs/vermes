"""Regression test for browser_navigate HTTP-status surfacing (B3).

Before the fix, every navigation failure collapsed to the generic
``"Navigation failed"`` string, so a 404/500 (page loaded but errored) was
indistinguishable from a hard failure (DNS / connection refused / timeout).
The fix surfaces any ``status``/``status_code`` the backend provides and
rewrites the error to ``"HTTP <code> error while navigating to <url>"``.
"""

import json

import pytest


def test_navigate_surfaces_http_status_on_failure(monkeypatch):
    from tools.browser_tool import browser_navigate

    monkeypatch.setattr("tools.browser_tool._is_camofox_mode", lambda: False)
    monkeypatch.setattr(
        "tools.browser_tool._run_browser_command",
        lambda *a, **k: {"success": False, "error": "Navigation failed", "status": 404},
    )
    # Skip first-nav recording side effects.
    monkeypatch.setattr(
        "tools.browser_tool._get_session_info",
        lambda *a, **k: {"_first_nav": False},
    )

    result = browser_navigate("https://github.com/NousResearch/hermes-agent")
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert parsed.get("status") == 404
    assert "HTTP 404" in parsed.get("error", "")


def test_navigate_no_status_unchanged(monkeypatch):
    """When the backend exposes no status, behavior is unchanged."""
    from tools.browser_tool import browser_navigate

    monkeypatch.setattr("tools.browser_tool._is_camofox_mode", lambda: False)
    monkeypatch.setattr(
        "tools.browser_tool._run_browser_command",
        lambda *a, **k: {"success": False, "error": "Navigation failed"},
    )
    monkeypatch.setattr(
        "tools.browser_tool._get_session_info",
        lambda *a, **k: {"_first_nav": False},
    )

    result = browser_navigate("https://github.com/NousResearch/hermes-agent")
    parsed = json.loads(result)
    assert parsed["success"] is False
    assert "status" not in parsed
    assert parsed.get("error") == "Navigation failed"
