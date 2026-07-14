"""Tests for Zalo OA adapter."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import Platform, PlatformConfig
from gateway.platforms.zalo import (
    ZaloAdapter,
    check_zalo_requirements,
    MAX_TEXT_LENGTH,
)


class TestCheckZaloRequirements:
    def test_no_config(self):
        with patch.dict("os.environ", {}, clear=True):
            assert not check_zalo_requirements()


def _make_zalo_adapter(monkeypatch):
    monkeypatch.setenv("ZALO_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("ZALO_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ZALO_OA_ID", "oa123")
    config = PlatformConfig(enabled=True, extra={})
    return ZaloAdapter(config)


class TestZaloAdapter:
    def test_init(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        assert adapter._access_token == "test-token"
        assert adapter._secret_key == "test-secret"
        assert adapter._webhook_port == 9221

    def test_split_text_short(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        assert adapter._split_text("Hello") == ["Hello"]

    def test_split_text_long(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        long = "A" * 3000
        result = adapter._split_text(long)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= MAX_TEXT_LENGTH

    def test_verify_signature_valid(self, monkeypatch):
        import hashlib
        import hmac
        adapter = _make_zalo_adapter(monkeypatch)
        body = '{"test": true}'
        expected = hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()
        assert adapter._verify_signature(body, expected)

    def test_verify_signature_invalid(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        assert not adapter._verify_signature("body", "wrong-sig")

    def test_verify_signature_disabled(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        adapter._secret_key = ""
        assert adapter._verify_signature("body", "")

    def test_send_not_connected(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        result = asyncio.run(adapter.send("user123", "Hello"))
        assert not result.success
        assert "Not connected" in result.error

    def test_process_text_event(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        data = {
            "event_name": "user_send_text",
            "user_id": "123456",
            "message": {"text": "Hello from Zalo"},
            "message_id": "msg-001",
        }
        with patch.object(adapter, "_get_user_name", return_value="TestUser"):
            asyncio.run(adapter._process_event(data))
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.text == "Hello from Zalo"
        assert event.source.user_id == "123456"
        assert event.source.platform.value == "zalo"

    def test_process_image_event(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        data = {
            "event_name": "user_send_image",
            "user_id": "789",
            "message": {"url": "https://example.com/image.jpg"},
            "message_id": "msg-002",
        }
        with patch.object(adapter, "_get_user_name", return_value="User789"):
            asyncio.run(adapter._process_event(data))
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.message_type.value == "photo"
        assert "https://example.com/image.jpg" in event.media_urls

    def test_process_follow_event(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        data = {"event_name": "follow", "user_id": "999"}
        asyncio.run(adapter._process_event(data))
        handler.assert_not_called()

    def test_enforces_own_access_policy(self, monkeypatch):
        adapter = _make_zalo_adapter(monkeypatch)
        assert adapter.enforces_own_access_policy is True
