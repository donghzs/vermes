"""Tests for P2: config-watch watcher.

Verifies that changes to config.yaml trigger the right connect/disconnect
actions via the hot-plug primitives.
"""

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from gateway.config import Platform, PlatformConfig
from gateway.lifecycle_mixin import LifecycleMixin


class FakeAdapter:
    """Minimal fake adapter for config-watch testing."""

    def __init__(self, config, platform=None):
        self.config = config
        self.platform = platform or Platform.TELEGRAM

    async def disconnect(self):
        pass


def _make_runner():
    """Create a minimal fake GatewayRunner for config-watch tests.

    Uses AsyncMock for _connect_one / _disconnect_one so that
    _handle_config_change (which awaits them) works correctly.
    The real implementations are tested in test_hotplug.py; here we
    only verify that _handle_config_change dispatches to the right
    primitive with the right platform.
    """
    runner = MagicMock()
    runner.adapters = {}
    runner._failed_platforms = {}
    runner.delivery_router = MagicMock()
    runner.delivery_router.adapters = {}
    runner._running = True
    runner.config = MagicMock()
    runner.config.platforms = {}

    # Track connect/disconnect calls for assertions
    connect_calls = []
    disconnect_calls = []

    async def _fake_connect_one(platform, platform_config=None, *, force=False):
        adapter = FakeAdapter(PlatformConfig(enabled=True), platform)
        runner.adapters[platform] = adapter
        runner.delivery_router.adapters = runner.adapters
        connect_calls.append(platform)
        return {"ok": True, "state": "connected"}

    async def _fake_disconnect_one(platform):
        adapter = runner.adapters.pop(platform, None)
        if adapter:
            await adapter.disconnect()
            disconnect_calls.append(platform)
        runner.delivery_router.adapters = runner.adapters
        return {"ok": True, "state": "disconnected"}

    runner._connect_one = _fake_connect_one
    runner._disconnect_one = _fake_disconnect_one
    runner._connect_calls = connect_calls
    runner._disconnect_calls = disconnect_calls

    runner._sync_voice_mode_state_to_adapter = MagicMock()
    runner._update_platform_runtime_status = MagicMock()

    return runner


_connect_one = LifecycleMixin._connect_one
_disconnect_one = LifecycleMixin._disconnect_one
_handle_config_change = LifecycleMixin._handle_config_change
_read_config_platforms = LifecycleMixin._read_config_platforms


@pytest.fixture
def runner():
    return _make_runner()


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_config_watch_connects_new_platform(runner, tmp_path):
    """A new enabled platform in config.yaml gets connected."""
    # Write a temp config.yaml
    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "platforms:\n"
        "  telegram:\n"
        "    enabled: true\n"
        "    token: test123\n"
    )

    runner._read_config_platforms = lambda: {
        "telegram": {"enabled": True, "token": "test123"}
    }

    # Initially no adapters connected
    assert not runner.adapters

    await _handle_config_change(runner)

    assert Platform.TELEGRAM in runner.adapters
    assert runner._connect_calls == [Platform.TELEGRAM]


@pytest.mark.asyncio
async def test_config_watch_disconnects_removed_platform(runner):
    """A platform removed from config gets disconnected."""
    platform = Platform.TELEGRAM
    adapter = FakeAdapter(PlatformConfig(enabled=True), platform)
    runner.adapters[platform] = adapter

    # Config now has no platforms
    runner._read_config_platforms = lambda: {}

    await _handle_config_change(runner)

    assert platform not in runner.adapters
    assert runner._disconnect_calls == [Platform.TELEGRAM]


@pytest.mark.asyncio
async def test_config_watch_disconnects_disabled_platform(runner):
    """A platform set to enabled:false gets disconnected."""
    platform = Platform.TELEGRAM
    adapter = FakeAdapter(PlatformConfig(enabled=True), platform)
    runner.adapters[platform] = adapter

    runner._read_config_platforms = lambda: {
        "telegram": {"enabled": False}
    }

    await _handle_config_change(runner)

    assert platform not in runner.adapters
    assert runner._disconnect_calls == [Platform.TELEGRAM]


@pytest.mark.asyncio
async def test_config_watch_no_change_no_action(runner):
    """No config change → no connect/disconnect."""
    platform = Platform.TELEGRAM
    adapter = FakeAdapter(PlatformConfig(enabled=True), platform)
    runner.adapters[platform] = adapter

    runner._read_config_platforms = lambda: {
        "telegram": {"enabled": True, "token": "test123"}
    }

    await _handle_config_change(runner)

    # Still connected, no extra calls
    assert platform in runner.adapters
    assert runner._connect_calls == []
    assert runner._disconnect_calls == []


@pytest.mark.asyncio
async def test_config_watch_multiple_platforms(runner):
    """Multiple platforms: connect new + disconnect removed simultaneously."""
    # Discord already connected
    discord_adapter = FakeAdapter(PlatformConfig(enabled=True), Platform.DISCORD)
    runner.adapters[Platform.DISCORD] = discord_adapter

    # Config has telegram (new) + discord (existing), but not whatsapp (removed)
    # Actually let's also have whatsapp connected but not in config
    whatsapp_adapter = FakeAdapter(PlatformConfig(enabled=True), Platform.WHATSAPP)
    runner.adapters[Platform.WHATSAPP] = whatsapp_adapter

    runner._read_config_platforms = lambda: {
        "telegram": {"enabled": True, "token": "test"},
        "discord": {"enabled": True, "token": "old"},
    }

    await _handle_config_change(runner)

    # Telegram connected
    assert Platform.TELEGRAM in runner.adapters
    # Discord still connected
    assert Platform.DISCORD in runner.adapters
    # WhatsApp disconnected
    assert Platform.WHATSAPP not in runner.adapters
    assert Platform.WHATSAPP in runner._disconnect_calls


def test_is_hotplug_enabled_default(runner):
    """Hot-plug is enabled by default."""
    # When load_config raises, should default to True
    with patch("vermes_cli.config.load_config", side_effect=Exception("no config")):
        result = LifecycleMixin._is_hotplug_enabled(runner)
    assert result is True


def test_is_hotplug_enabled_disabled(runner):
    """Hot-plug can be disabled via config."""
    with patch("vermes_cli.config.load_config", return_value={
        "gateway": {"hotplug": {"enabled": False}}
    }):
        result = LifecycleMixin._is_hotplug_enabled(runner)
    assert result is False
