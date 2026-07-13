"""Tests for Nostr and Synology Chat adapters."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import Platform, PlatformConfig
from gateway.platforms.nostr import (
    NostrAdapter,
    check_nostr_requirements,
    _hex_to_bytes,
    _bytes_to_hex,
)


class TestCheckNostrRequirements:
    def test_no_config(self):
        with patch.dict("os.environ", {}, clear=True):
            assert not check_nostr_requirements()


class TestHexHelpers:
    def test_hex_to_bytes(self):
        assert _hex_to_bytes("48656c6c6f") == b"Hello"

    def test_bytes_to_hex(self):
        assert _bytes_to_hex(b"Hello") == "48656c6c6f"

    def test_roundtrip(self):
        original = b"\x01\x02\x03\xff"
        hex_str = _bytes_to_hex(original)
        assert _hex_to_bytes(hex_str) == original


def _make_nostr_adapter(monkeypatch):
    monkeypatch.setenv("NOSTR_PRIVATE_KEY", "a" * 64)
    monkeypatch.setenv("NOSTR_RELAYS", "wss://relay.test.com")
    config = PlatformConfig(enabled=True, extra={})
    return NostrAdapter(config)


class TestNostrAdapter:
    def test_init(self, monkeypatch):
        adapter = _make_nostr_adapter(monkeypatch)
        assert adapter._privkey == "a" * 64
        assert "wss://relay.test.com" in adapter._relay_urls

    def test_parse_nsec_not_supported(self, monkeypatch):
        adapter = _make_nostr_adapter(monkeypatch)
        result = adapter._parse_private_key("nsec1qwerty")
        assert result == ""

    def test_parse_hex_key(self, monkeypatch):
        adapter = _make_nostr_adapter(monkeypatch)
        result = adapter._parse_private_key("abcdef0123456789" * 4)
        assert result == "abcdef0123456789" * 4

    def test_send_not_connected(self, monkeypatch):
        adapter = _make_nostr_adapter(monkeypatch)
        result = asyncio.run(adapter.send("user123", "Hello"))
        assert not result.success
        assert "No relay" in result.error

    def test_enforces_own_access_policy(self, monkeypatch):
        adapter = _make_nostr_adapter(monkeypatch)
        assert adapter.enforces_own_access_policy is True


def _make_synology_adapter(monkeypatch):
    monkeypatch.setenv("SYNOLOGY_CHAT_INCOMING_URL",
                       "https://nas.example.com/webapi/entry.cgi?token=test")
    monkeypatch.setenv("SYNOLOGY_CHAT_OUTGOING_TOKEN", "outgoing-token")
    config = PlatformConfig(enabled=True, extra={})
    from gateway.platforms.synology_chat import SynologyChatAdapter
    return SynologyChatAdapter(config)


class TestSynologyChatAdapter:
    def test_init(self, monkeypatch):
        adapter = _make_synology_adapter(monkeypatch)
        assert "nas.example.com" in adapter._incoming_url
        assert adapter._outgoing_token == "outgoing-token"

    def test_send_not_connected(self, monkeypatch):
        adapter = _make_synology_adapter(monkeypatch)
        result = asyncio.run(adapter.send("channel1", "Hello"))
        assert not result.success

    def test_split_text(self, monkeypatch):
        from gateway.platforms.synology_chat import MAX_TEXT_LENGTH
        adapter = _make_synology_adapter(monkeypatch)
        assert adapter._split_text("Hello") == ["Hello"]
        long = "A" * (MAX_TEXT_LENGTH + 500)
        result = adapter._split_text(long)
        assert len(result) > 1

    def test_enforces_own_access_policy(self, monkeypatch):
        adapter = _make_synology_adapter(monkeypatch)
        assert adapter.enforces_own_access_policy is True
