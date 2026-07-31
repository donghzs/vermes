"""Tests for P3: Control Server.

Verifies that the control server:
- Starts on 127.0.0.1 with token auth
- Handles connect/disconnect/reload endpoints
- Rejects unauthenticated requests
- Degrades gracefully on port conflict
- Cleans up token file on stop
"""

import asyncio
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.control_server import (
    ControlServer,
    _write_control_token,
    _read_control_token,
    _remove_control_token,
    CONTROL_TOKEN_FILE,
)


@pytest.fixture
def tmp_vermes_home(tmp_path, monkeypatch):
    """Redirect get_vermes_home to a temp directory."""
    def _fake_home():
        return tmp_path
    monkeypatch.setattr("vermes_cli.config.get_vermes_home", _fake_home)
    # Also patch the fallback in control_server
    import gateway.control_server as cs
    monkeypatch.setattr(cs, "_get_vermes_home", _fake_home)
    return tmp_path


@pytest.fixture
def fake_gateway():
    """Minimal fake gateway with async _connect_one/_disconnect_one."""
    gw = MagicMock()
    gw.adapters = {}

    connect_results = {}
    disconnect_results = {}

    async def _connect_one(platform, platform_config=None, *, force=False):
        result = connect_results.get(platform, {"ok": True, "state": "connected"})
        if result["ok"]:
            gw.adapters[platform] = MagicMock()
        return result

    async def _disconnect_one(platform):
        gw.adapters.pop(platform, None)
        return disconnect_results.get(platform, {"ok": True, "state": "disconnected"})

    gw._connect_one = _connect_one
    gw._disconnect_one = _disconnect_one
    gw._connect_results = connect_results
    gw._disconnect_results = disconnect_results
    return gw


# ── Token file tests ────────────────────────────────────────────────

def test_write_and_read_token(tmp_vermes_home):
    """Token file is written with correct format and can be read back."""
    token = _write_control_token(9120)
    assert len(token) == 64  # 32 bytes hex

    cfg = _read_control_token()
    assert cfg is not None
    assert cfg["port"] == 9120
    assert cfg["token"] == token


def test_read_token_missing(tmp_vermes_home):
    """Returns None when token file doesn't exist."""
    assert _read_control_token() is None


def test_remove_token(tmp_vermes_home):
    """Token file is removed cleanly."""
    _write_control_token(9120)
    token_file = tmp_vermes_home / CONTROL_TOKEN_FILE
    assert token_file.exists()

    _remove_control_token()
    assert not token_file.exists()


def test_token_file_permissions(tmp_vermes_home):
    """Token file has 0600 permissions (on POSIX)."""
    _write_control_token(9120)
    token_file = tmp_vermes_home / CONTROL_TOKEN_FILE
    if os.name != "nt":
        mode = token_file.stat().st_mode & 0o777
        assert mode == 0o600


# ── ControlServer lifecycle tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_control_server_starts(tmp_vermes_home, fake_gateway):
    """Control server starts and writes token file."""
    cs = ControlServer(fake_gateway, port=0)  # port 0 = random free port
    # We need a real port; find a free one
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cs = ControlServer(fake_gateway, port=port)
    success = await cs.start()
    assert success
    assert cs.token

    # Token file exists
    cfg = _read_control_token()
    assert cfg is not None
    assert cfg["port"] == port

    await cs.stop()
    # Token file removed
    assert _read_control_token() is None


@pytest.mark.asyncio
async def test_control_server_port_conflict(tmp_vermes_home, fake_gateway):
    """If port is already in use, start() returns False (graceful degradation)."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # Keep socket open to block the port

    cs = ControlServer(fake_gateway, port=port)
    success = await cs.start()
    assert success is False
    assert cs._runner is None

    # Token file should be cleaned up
    assert _read_control_token() is None

    sock.close()


# ── HTTP endpoint tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_endpoint_no_auth(tmp_vermes_home, fake_gateway):
    """Health endpoint works without auth (127.0.0.1 only)."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from gateway.config import Platform
    fake_gateway.adapters = {Platform.TELEGRAM: MagicMock()}

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.get("/control/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert "telegram" in data["adapters"]

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_connect_endpoint_with_auth(tmp_vermes_home, fake_gateway):
    """Connect endpoint works with valid token."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from gateway.config import Platform
    fake_gateway.adapters = {}

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()
    token = cs.token

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/telegram/connect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert Platform.TELEGRAM in fake_gateway.adapters

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_connect_endpoint_no_auth(tmp_vermes_home, fake_gateway):
    """Connect endpoint rejects requests without auth."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post("/control/channels/telegram/connect")
    assert resp.status == 401

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_connect_endpoint_wrong_token(tmp_vermes_home, fake_gateway):
    """Connect endpoint rejects wrong token."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/telegram/connect",
        headers={"Authorization": "Bearer wrongtoken123"},
    )
    assert resp.status == 401

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_disconnect_endpoint(tmp_vermes_home, fake_gateway):
    """Disconnect endpoint removes adapter."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from gateway.config import Platform
    fake_gateway.adapters = {Platform.TELEGRAM: MagicMock()}

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()
    token = cs.token

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/telegram/disconnect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert Platform.TELEGRAM not in fake_gateway.adapters

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_reload_endpoint(tmp_vermes_home, fake_gateway):
    """Reload endpoint calls _connect_one with force=True."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from gateway.config import Platform

    force_seen = []

    async def _connect_one_spy(platform, platform_config=None, *, force=False):
        force_seen.append(force)
        return {"ok": True, "state": "connected"}

    fake_gateway._connect_one = _connect_one_spy
    fake_gateway.adapters = {}

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()
    token = cs.token

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/telegram/reload",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is True
    assert force_seen == [True]  # force=True was passed

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_unknown_platform(tmp_vermes_home, fake_gateway):
    """Unknown platform name returns error."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()
    token = cs.token

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/nonexistent_platform/connect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is False
    assert "unknown platform" in data["error"]

    await client.close()
    await cs.stop()


@pytest.mark.asyncio
async def test_connect_failure_returned(tmp_vermes_home, fake_gateway):
    """When _connect_one fails, the error is returned to the caller."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    from gateway.config import Platform

    async def _failing_connect(platform, platform_config=None, *, force=False):
        return {"ok": False, "error": "Telegram token invalid: 401"}

    fake_gateway._connect_one = _failing_connect
    fake_gateway.adapters = {}

    cs = ControlServer(fake_gateway, port=port)
    await cs.start()
    token = cs.token

    app = cs._build_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    resp = await client.post(
        "/control/channels/telegram/connect",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["ok"] is False
    assert "401" in data["error"]

    await client.close()
    await cs.stop()
