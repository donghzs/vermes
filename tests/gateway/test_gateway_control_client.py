"""Tests for P4: _gateway_control client.

Verifies that the client:
- Reads the control token file correctly
- Sends POST requests with auth header
- Degrades gracefully when control server is unreachable
- Returns a sensible fallback dict when token file is missing
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from vermes_cli.blueprints._gateway_control import (
    reload_channel,
    connect_channel,
    disconnect_channel,
    _read_control_config,
)


@pytest.fixture
def tmp_vermes_home(tmp_path, monkeypatch):
    """Redirect get_vermes_home to a temp directory."""
    def _fake_home():
        return tmp_path
    monkeypatch.setattr("vermes_cli.config.get_vermes_home", _fake_home)
    return tmp_path


# ── _read_control_config tests ──────────────────────────────────────

def test_read_config_missing(tmp_vermes_home):
    """Returns None when no token file exists."""
    assert _read_control_config() is None


def test_read_config_valid(tmp_vermes_home):
    """Reads a valid token file."""
    token_file = tmp_vermes_home / ".gateway_control.json"
    token_file.write_text(json.dumps({"port": 9120, "token": "abc123"}))
    cfg = _read_control_config()
    assert cfg == {"port": 9120, "token": "abc123"}


def test_read_config_corrupt(tmp_vermes_home):
    """Returns None on corrupt JSON."""
    token_file = tmp_vermes_home / ".gateway_control.json"
    token_file.write_text("not json{{{")
    assert _read_control_config() is None


# ── reload_channel degradation tests ────────────────────────────────

@pytest.mark.asyncio
async def test_reload_channel_no_token_file(tmp_vermes_home):
    """When no token file exists, returns graceful degradation dict."""
    result = await reload_channel("telegram")
    assert result["ok"] is True
    assert "note" in result
    assert "config-watch" in result["note"]


@pytest.mark.asyncio
async def test_connect_channel_no_token_file(tmp_vermes_home):
    """connect_channel degrades gracefully without token file."""
    result = await connect_channel("telegram")
    assert result["ok"] is True
    assert "note" in result


@pytest.mark.asyncio
async def test_disconnect_channel_no_token_file(tmp_vermes_home):
    """disconnect_channel degrades gracefully without token file."""
    result = await disconnect_channel("telegram")
    assert result["ok"] is True
    assert "note" in result


@pytest.mark.asyncio
async def test_reload_channel_connection_refused(tmp_vermes_home):
    """When gateway is unreachable, returns degradation dict."""
    # Write a token file pointing to a port nothing is listening on
    token_file = tmp_vermes_home / ".gateway_control.json"
    token_file.write_text(json.dumps({"port": 19999, "token": "testtoken"}))

    result = await reload_channel("telegram")
    assert result["ok"] is True
    assert "note" in result
    assert "config-watch" in result["note"]


@pytest.mark.asyncio
async def test_reload_channel_success(tmp_vermes_home):
    """When gateway responds, returns the gateway's response."""
    token_file = tmp_vermes_home / ".gateway_control.json"
    token_file.write_text(json.dumps({"port": 9120, "token": "secret"}))

    # Mock the aiohttp ClientSession context manager chain
    class FakeResponse:
        def __init__(self, data):
            self._data = data
        async def json(self):
            return self._data
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class FakeSession:
        def __init__(self):
            self.post_args = None
        def post(self, url, **kwargs):
            self.post_args = (url, kwargs)
            return FakeResponse({"ok": True, "state": "connected"})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    fake_session = FakeSession()
    with patch("vermes_cli.blueprints._gateway_control.aiohttp.ClientSession", return_value=fake_session):
        result = await reload_channel("telegram")

    assert result == {"ok": True, "state": "connected"}

    # Verify auth header was sent
    url, kwargs = fake_session.post_args
    headers = kwargs.get("headers", {})
    assert headers.get("Authorization") == "Bearer secret"
    assert "/control/channels/telegram/reload" in url


@pytest.mark.asyncio
async def test_reload_channel_gateway_error(tmp_vermes_home):
    """When gateway returns an error, it's passed through."""
    token_file = tmp_vermes_home / ".gateway_control.json"
    token_file.write_text(json.dumps({"port": 9120, "token": "secret"}))

    class FakeResponse:
        def __init__(self, data):
            self._data = data
        async def json(self):
            return self._data
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class FakeSession:
        def post(self, url, **kwargs):
            return FakeResponse({"ok": False, "error": "Telegram token invalid: 401"})
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    with patch("vermes_cli.blueprints._gateway_control.aiohttp.ClientSession", return_value=FakeSession()):
        result = await reload_channel("telegram")

    assert result["ok"] is False
    assert "401" in result["error"]
