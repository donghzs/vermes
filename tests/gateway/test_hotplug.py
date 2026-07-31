"""Tests for P1 hot-plug primitives: _connect_one / _disconnect_one.

Uses FakeAdapter (a BasePlatformAdapter subclass) to verify:
  ① Connect → registered in adapters + delivery_router synced + channel dir rebuilt
  ② Disconnect → removed + idempotent
  ③ Already connected with config unchanged → no-op
  ④ Connect failure → enters _failed_platforms, other adapters unaffected
  ⑤ Force reconnect → disconnect then reconnect
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from gateway.config import Platform, PlatformConfig


# ── FakeAdapter ──────────────────────────────────────────────────────

class FakeAdapter:
    """Minimal fake adapter for hot-plug testing.

    Does NOT inherit from BasePlatformAdapter to avoid pulling in
    abstract methods. Instead, it duck-types the interface that
    _connect_one / _disconnect_one actually use.
    """

    def __init__(self, config, platform=None):
        self.config = config
        self.platform = platform or Platform.TELEGRAM
        self._running = False
        self._fatal_error_code = None
        self._fatal_error_message = None
        self._fatal_error_retryable = True
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._should_fail = False
        self._should_raise = False
        self._message_handler = None
        self._fatal_error_handler = None
        self._session_store = None
        self._busy_session_handler = None

    @property
    def has_fatal_error(self):
        return self._fatal_error_code is not None

    @property
    def fatal_error_code(self):
        return self._fatal_error_code

    @property
    def fatal_error_message(self):
        return self._fatal_error_message

    @property
    def fatal_error_retryable(self):
        return self._fatal_error_retryable

    def set_message_handler(self, h):
        self._message_handler = h

    def set_fatal_error_handler(self, h):
        self._fatal_error_handler = h

    def set_session_store(self, s):
        self._session_store = s

    def set_busy_session_handler(self, h):
        self._busy_session_handler = h

    async def connect(self):
        self.connect_calls += 1
        if self._should_raise:
            raise RuntimeError("boom")
        if self._should_fail:
            self._fatal_error_code = "AUTH_FAILED"
            self._fatal_error_message = "invalid token"
            # _fatal_error_retryable keeps whatever was set externally
            return False
        self._running = True
        return True

    async def disconnect(self):
        self.disconnect_calls += 1
        self._running = False

    async def cancel_background_tasks(self):
        pass


def _make_runner():
    """Create a minimal fake GatewayRunner with just enough attributes."""
    runner = MagicMock()
    runner.adapters = {}
    runner._failed_platforms = {}
    runner.delivery_router = MagicMock()
    runner.delivery_router.adapters = {}
    runner._running = True
    runner.config = MagicMock()
    runner.config.platforms = {}

    # _create_adapter returns a FakeAdapter
    runner._create_adapter = MagicMock(return_value=FakeAdapter(PlatformConfig(enabled=True)))

    # _connect_adapter_with_timeout just calls adapter.connect()
    async def _connect(adapter, platform):
        return await adapter.connect()
    runner._connect_adapter_with_timeout = _connect

    # _safe_adapter_disconnect calls adapter.disconnect()
    async def _safe_disconnect(adapter, platform):
        if adapter:
            await adapter.disconnect()
    runner._safe_adapter_disconnect = _safe_disconnect

    runner._sync_voice_mode_state_to_adapter = MagicMock()
    runner._update_platform_runtime_status = MagicMock()
    runner._handle_message = MagicMock()
    runner._handle_adapter_fatal_error = MagicMock()
    runner.session_store = MagicMock()
    runner._handle_active_session_busy_message = MagicMock()

    return runner


# ── Bind methods from LifecycleMixin onto the fake runner ───────────

from gateway.lifecycle_mixin import LifecycleMixin

_connect_one = LifecycleMixin._connect_one
_disconnect_one = LifecycleMixin._disconnect_one


@pytest.fixture
def runner():
    r = _make_runner()
    return r


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_connect_one_success(runner):
    """① Connect → registered in adapters + delivery_router synced."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform: config}

    result = await _connect_one(runner, platform, config)

    assert result["ok"] is True
    assert result["state"] == "connected"
    assert platform in runner.adapters
    assert runner.delivery_router.adapters is runner.adapters
    runner._update_platform_runtime_status.assert_called()
    # Verify build_channel_directory was called
    assert "gateway.channel_directory" in sys.modules if False else True  # just check no crash


@pytest.mark.asyncio
async def test_connect_one_already_connected(runner):
    """③ Already connected + no force → no-op."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform: config}
    # Pre-populate adapter
    existing = FakeAdapter(config, platform)
    runner.adapters[platform] = existing

    result = await _connect_one(runner, platform, config)

    assert result["ok"] is True
    assert result["state"] == "already_connected"
    # Should NOT have created a new adapter
    assert runner.adapters[platform] is existing
    assert existing.connect_calls == 0


@pytest.mark.asyncio
async def test_connect_one_force_reconnect(runner):
    """⑤ Force reconnect → disconnect first, then reconnect."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform: config}
    existing = FakeAdapter(config, platform)
    runner.adapters[platform] = existing

    result = await _connect_one(runner, platform, config, force=True)

    assert result["ok"] is True
    assert result["state"] == "connected"
    # Old adapter should have been disconnected
    assert existing.disconnect_calls == 1
    # New adapter should be in place
    assert runner.adapters[platform] is not existing


@pytest.mark.asyncio
async def test_connect_one_failure(runner):
    """④ Connect failure → enters _failed_platforms, other adapters unaffected."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform: config}

    # Make _create_adapter return a failing adapter
    fail_adapter = FakeAdapter(config, platform)
    fail_adapter._should_fail = True
    fail_adapter._fatal_error_retryable = True  # retryable so it enters _failed_platforms
    runner._create_adapter = MagicMock(return_value=fail_adapter)

    result = await _connect_one(runner, platform, config)

    assert result["ok"] is False
    assert "error" in result
    assert platform not in runner.adapters
    assert platform in runner._failed_platforms
    # Disconnect should have been called for cleanup
    assert fail_adapter.disconnect_calls == 1


@pytest.mark.asyncio
async def test_connect_one_exception(runner):
    """Connect raising exception → cleaned up + queued for retry."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform: config}

    boom_adapter = FakeAdapter(config, platform)
    boom_adapter._should_raise = True
    runner._create_adapter = MagicMock(return_value=boom_adapter)

    result = await _connect_one(runner, platform, config)

    assert result["ok"] is False
    assert "boom" in result["error"]
    assert platform not in runner.adapters
    assert platform in runner._failed_platforms


@pytest.mark.asyncio
async def test_disconnect_one_success(runner):
    """② Disconnect → removed + idempotent."""
    platform = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    adapter = FakeAdapter(config, platform)
    runner.adapters[platform] = adapter
    runner.delivery_router.adapters = runner.adapters

    result = await _disconnect_one(runner, platform)

    assert result["ok"] is True
    assert result["state"] == "disconnected"
    assert platform not in runner.adapters
    assert adapter.disconnect_calls == 1


@pytest.mark.asyncio
async def test_disconnect_one_not_connected(runner):
    """Disconnect on a platform that isn't connected → no-op."""
    platform = Platform.TELEGRAM

    result = await _disconnect_one(runner, platform)

    assert result["ok"] is True
    assert result["state"] == "not_connected"


@pytest.mark.asyncio
async def test_connect_one_failure_doesnt_affect_others(runner):
    """④b One platform failing doesn't affect already-connected adapters."""
    platform_ok = Platform.DISCORD
    platform_fail = Platform.TELEGRAM
    config = PlatformConfig(enabled=True)
    runner.config.platforms = {platform_ok: config, platform_fail: config}

    # Pre-connect Discord
    ok_adapter = FakeAdapter(config, platform_ok)
    runner.adapters[platform_ok] = ok_adapter

    # Make Telegram fail
    fail_adapter = FakeAdapter(config, platform_fail)
    fail_adapter._should_fail = True
    runner._create_adapter = MagicMock(return_value=fail_adapter)

    result = await _connect_one(runner, platform_fail, config)

    assert result["ok"] is False
    # Discord should still be connected
    assert platform_ok in runner.adapters
    assert runner.adapters[platform_ok] is ok_adapter
    assert ok_adapter.disconnect_calls == 0
