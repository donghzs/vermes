"""Bug 2 fix: `browser_vision` must delegate screenshot analysis to the unified
vision resolver (`vision_analyze_tool`) instead of the previous env-var-only
path (`_get_vision_model` + raw `call_llm`). The unified resolver tests the
ACTUAL model capability and falls back to the provider's dedicated vision model
and finally Vermes' built-in vbit vision model — so vision works out of the box
even when no user-configured model supports it (the frontend image path already
uses this same resolver, which is why frontend images were recognized but
browser_vision was not).
"""
import asyncio
import json

import pytest


@pytest.fixture
def fake_browser(monkeypatch, tmp_path):
    import tools.browser_tool as bt

    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n fake-screenshot-bytes")

    monkeypatch.setenv("VERMES_HOME", str(tmp_path / "Vermes"))
    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(bt, "_get_browser_engine", lambda: "chrome")
    monkeypatch.setattr(bt, "_should_inject_engine", lambda e: False)
    monkeypatch.setattr(
        bt,
        "_run_browser_command",
        lambda *a, **k: {"success": True, "data": {"path": str(png)}},
    )

    captured = {}

    async def fake_vision_analyze(image_url, user_prompt, model=None, provider=None):
        captured["image_url"] = image_url
        captured["user_prompt"] = user_prompt
        return json.dumps({"success": True, "analysis": "FAKE_ANALYSIS_RESULT"})

    # browser_vision imports vision_analyze_tool lazily from tools.vision_tools,
    # so patch it at the source module.
    monkeypatch.setattr(
        "tools.vision_tools.vision_analyze_tool", fake_vision_analyze
    )
    return captured


def test_browser_vision_delegates_to_vision_analyze(fake_browser):
    import tools.browser_tool as bt

    res = json.loads(bt.browser_vision("what is on the page?"))

    assert res.get("success") is True, f"unexpected: {res}"
    assert res.get("analysis") == "FAKE_ANALYSIS_RESULT"
    # The unified resolver was actually invoked (not the old raw call_llm path).
    assert fake_browser.get("user_prompt"), "vision_analyze_tool was never called"
    assert str(fake_browser.get("image_url", "")).endswith("shot.png")


def test_browser_vision_propagates_vision_failure(fake_browser, monkeypatch):
    import tools.browser_tool as bt

    async def failing_vision(image_url, user_prompt, model=None, provider=None):
        return json.dumps({"success": False, "error": "no vision model anywhere"})

    monkeypatch.setattr("tools.vision_tools.vision_analyze_tool", failing_vision)

    res = json.loads(bt.browser_vision("describe this"))
    assert res.get("success") is False
    assert "no vision model" in res.get("error", "")
