"""Regression tests for A0-3: browser_back on non-navigable documents (#A0-3).

On JSON / raw-text / PDF pages, ``data:`` / ``about:`` URLs, or when the
history stack is empty, Chrome's DevTools ``back`` returns an opaque protocol
string (e.g. ``Inspected target navigated or closed``). The agent (and its
user) should see a clear, actionable reason instead of a raw CDP error. This
test pins the friendly translation while preserving verbatim passthrough for
genuine failures and the normal success path.
"""

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def _no_camofox():
    with patch("tools.browser_tool._is_camofox_mode", return_value=False):
        yield


def test_non_navigable_page_returns_friendly_error(_no_camofox):
    from tools.browser_tool import browser_back

    with patch(
        "tools.browser_tool._run_browser_command",
        return_value={"success": False, "error": "Inspected target navigated or closed"},
    ):
        out = json.loads(browser_back())

    assert out["success"] is False
    # Friendly, actionable message — not the raw CDP string.
    assert "无法后退" in out["error"]
    assert "JSON" in out["error"]
    assert "browser_navigate" in out["error"]
    # The raw opaque string must not leak through unchanged.
    assert "Inspected target navigated or closed" not in out["error"]


def test_empty_history_returns_friendly_error(_no_camofox):
    from tools.browser_tool import browser_back

    with patch(
        "tools.browser_tool._run_browser_command",
        return_value={"success": False, "error": "no history entry to navigate to"},
    ):
        out = json.loads(browser_back())

    assert out["success"] is False
    assert "无法后退" in out["error"]


def test_genuine_error_passthrough(_no_camofox):
    from tools.browser_tool import browser_back

    with patch(
        "tools.browser_tool._run_browser_command",
        return_value={"success": False, "error": "connection refused: agent-browser down"},
    ):
        out = json.loads(browser_back())

    assert out["success"] is False
    # Genuine failures still surface verbatim.
    assert "connection refused" in out["error"]


def test_success_path_unchanged(_no_camofox):
    from tools.browser_tool import browser_back

    with patch(
        "tools.browser_tool._run_browser_command",
        return_value={"success": True, "data": {"url": "https://example.com/page2"}},
    ):
        out = json.loads(browser_back())

    assert out["success"] is True
    assert out["url"] == "https://example.com/page2"
