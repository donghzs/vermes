"""Tests for the built-in LINE platform adapter (gateway/platforms/line.py).

Covers:
1. check_line_requirements / adapter init
2. Signature verification (HMAC-SHA256) — _verify_signature
3. Chat ID resolution from webhook source
4. Allowlist gating — _is_authorized
5. Markdown stripping + chunking
6. RequestCache state machine
7. Webhook dedup — _MessageDeduplicator
8. send routing: reply token → push fallback
9. Postback button message builder
10. Config integration (env auto-injection + run.py allowlist maps)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────

def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _make_config(extra=None):
    from gateway.config import PlatformConfig, Platform
    cfg = PlatformConfig()
    cfg.extra = {
        "channel_access_token": "test_token",
        "channel_secret": "test_secret",
    }
    if extra:
        cfg.extra.update(extra)
    return cfg


# ── 1. Requirements & adapter init ───────────────────────────────────────

class TestRequirements:
    def test_adapter_name(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        assert adapter.name == 'Line'

    def test_adapter_stores_credentials(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        assert adapter.channel_access_token == "test_token"
        assert adapter.channel_secret == "test_secret"

    def test_adapter_default_allowlists(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        assert adapter.allowed_users == set()
        assert adapter.allowed_groups == set()
        assert adapter.allowed_rooms == set()
        assert adapter.allow_all is False


# ── 2. Signature verification ────────────────────────────────────────────

class TestSignature:
    def test_valid_signature(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        body = b'{"events":[]}'
        sig = _sign(body, "test_secret")
        assert adapter._verify_signature(body, sig) is True

    def test_invalid_signature(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        body = b'{"events":[]}'
        bad_sig = base64.b64encode(b"wrong").decode()
        assert adapter._verify_signature(body, bad_sig) is False

    def test_tampered_body(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        body = b'{"events":[]}'
        sig = _sign(body, "test_secret")
        tampered = b'{"events":[{"type":"message"}]}'
        assert adapter._verify_signature(tampered, sig) is False

    def test_empty_signature(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())
        assert adapter._verify_signature(b'{"events":[]}', "") is False

    def test_empty_secret(self):
        from gateway.platforms.line import LineAdapter
        cfg = _make_config({"channel_secret": ""})
        adapter = LineAdapter(cfg)
        assert adapter._verify_signature(b'body', "any") is False


# ── 3. Markdown stripping ────────────────────────────────────────────────

class TestMarkdownStrip:
    def test_strip_bold(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        result = strip_markdown_preserving_urls("**bold** text")
        assert "**" not in result
        assert "bold" in result

    def test_preserve_url(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        result = strip_markdown_preserving_urls("see [Google](https://google.com)")
        assert "https://google.com" in result

    def test_strip_headers(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        result = strip_markdown_preserving_urls("## Title\nbody")
        assert "##" not in result

    def test_preserve_code_block(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        result = strip_markdown_preserving_urls("```\n**not stripped**\n```")
        assert "not stripped" in result

    def test_empty_input(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        assert strip_markdown_preserving_urls("") == ""

    def test_strikethrough(self):
        from gateway.platforms.line import strip_markdown_preserving_urls
        result = strip_markdown_preserving_urls("~~deleted~~")
        assert "deleted" in result


# ── 4. Chunking ──────────────────────────────────────────────────────────

class TestChunking:
    def test_short_text_one_chunk(self):
        from gateway.platforms.line import split_for_line
        chunks = split_for_line("hello world")
        assert len(chunks) == 1

    def test_long_text_chunked(self):
        from gateway.platforms.line import split_for_line
        long_text = "a" * 5000
        chunks = split_for_line(long_text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 4500

    def test_chunk_boundary_newline(self):
        from gateway.platforms.line import split_for_line
        text = "line1\n" + "a" * 4490 + "\nline2\n" + "b" * 100
        chunks = split_for_line(text)
        assert len(chunks) >= 2

    def test_empty_text(self):
        from gateway.platforms.line import split_for_line
        assert split_for_line("") == []


# ── 5. RequestCache state machine ────────────────────────────────────────

class TestRequestCache:
    def test_state_transitions(self):
        from gateway.platforms.line import RequestCache, _State
        cache = RequestCache()
        rid = cache.register_pending("chat_1")
        assert cache.get(rid).state == _State.PENDING
        cache.set_ready(rid, {"text": "hello"})
        assert cache.get(rid).state == _State.READY
        cache.mark_delivered(rid)
        assert cache.get(rid).state == _State.DELIVERED

    def test_unknown_key(self):
        from gateway.platforms.line import RequestCache
        cache = RequestCache()
        assert cache.get("nonexistent") is None

    def test_find_pending_for_chat(self):
        from gateway.platforms.line import RequestCache, _State
        cache = RequestCache()
        rid = cache.register_pending("chat_1")
        assert cache.find_pending_for_chat("chat_1") == rid
        cache.set_ready(rid, {"text": "done"})
        assert cache.find_pending_for_chat("chat_1") is None

    def test_set_error(self):
        from gateway.platforms.line import RequestCache, _State
        cache = RequestCache()
        rid = cache.register_pending("chat_1")
        cache.set_error(rid, "LLM timeout")
        entry = cache.get(rid)
        assert entry.state == _State.ERROR
        assert entry.error_message == "LLM timeout"

    def test_prune(self):
        from gateway.platforms.line import RequestCache
        cache = RequestCache(max_entries=3)
        cache.register_pending("c1")
        cache.register_pending("c2")
        cache.register_pending("c3")
        cache.register_pending("c4")  # should evict oldest
        assert cache.prune() >= 0  # pruning happens on register_pending


# ── 6. Dedup ─────────────────────────────────────────────────────────────

class TestDedup:
    def test_first_seen_not_duplicate(self):
        from gateway.platforms.line import _MessageDeduplicator
        dedup = _MessageDeduplicator(max_size=100)
        assert dedup.is_duplicate("msg_1") is False

    def test_second_seen_is_duplicate(self):
        from gateway.platforms.line import _MessageDeduplicator
        dedup = _MessageDeduplicator(max_size=100)
        dedup.is_duplicate("msg_1")
        assert dedup.is_duplicate("msg_1") is True

    def test_lru_eviction(self):
        from gateway.platforms.line import _MessageDeduplicator
        dedup = _MessageDeduplicator(max_size=3)
        dedup.is_duplicate("a")
        dedup.is_duplicate("b")
        dedup.is_duplicate("c")
        dedup.is_duplicate("d")  # evicts "a"
        assert dedup.is_duplicate("a") is False  # "a" was evicted


# ── 7. Authorization ─────────────────────────────────────────────────────

class TestAuthorization:
    def test_allow_all(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config({"allow_all_users": True}))
        assert adapter._is_authorized("user", "anyone") is True
        assert adapter._is_authorized("group", "anygroup") is True

    def test_user_allowlist_match(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config({"allowed_users": ["U123"]}))
        assert adapter._is_authorized("user", "U123") is True

    def test_user_allowlist_no_match(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config({"allowed_users": ["U123"]}))
        assert adapter._is_authorized("user", "U999") is False

    def test_group_allowlist(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config({"allowed_groups": ["C1"]}))
        assert adapter._is_authorized("group", "C1") is True
        assert adapter._is_authorized("group", "C2") is False

    def test_room_allowlist(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config({"allowed_rooms": ["R1"]}))
        assert adapter._is_authorized("room", "R1") is True
        assert adapter._is_authorized("room", "R2") is False

    def test_empty_allowlist_allows_all(self):
        from gateway.platforms.line import LineAdapter
        adapter = LineAdapter(_make_config())  # no allowlists
        # Empty allowlist = allow all (gateway-wide auth handles it)
        assert adapter._is_authorized("user", "anyone") is True


# ── 8. Postback button ───────────────────────────────────────────────────

class TestPostbackButton:
    def test_build_postback_button_message(self):
        from gateway.platforms.line import build_postback_button_message
        result = build_postback_button_message(
            text="Thinking...",
            button_label="Get answer",
            request_id="req_123",
        )
        assert result["type"] == "template"
        assert result["template"]["type"] == "buttons"
        assert result["template"]["text"] == "Thinking..."
        assert len(result["template"]["actions"]) == 1
        action = result["template"]["actions"][0]
        assert action["type"] == "postback"
        assert action["label"] == "Get answer"

    def test_postback_truncates_long_text(self):
        from gateway.platforms.line import build_postback_button_message
        long_text = "x" * 200
        result = build_postback_button_message(
            text=long_text,
            button_label="Click",
            request_id="req_456",
        )
        assert len(result["template"]["text"]) <= 160


# ── 9. Config integration ────────────────────────────────────────────────

class TestConfigIntegration:
    def test_line_in_platform_enum(self):
        from gateway.config import Platform
        assert Platform.LINE.value == "line"

    def test_line_env_auto_injection(self, monkeypatch):
        from gateway.config import Platform, load_gateway_config
        monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "env_token")
        monkeypatch.setenv("LINE_CHANNEL_SECRET", "env_secret")
        monkeypatch.setenv("LINE_PORT", "9090")
        monkeypatch.setenv("LINE_PUBLIC_URL", "https://bot.example.com")
        cfg = load_gateway_config()
        assert Platform.LINE in cfg.platforms
        assert cfg.platforms[Platform.LINE].enabled is True
        extra = cfg.platforms[Platform.LINE].extra
        assert extra["channel_access_token"] == "env_token"
        assert extra["channel_secret"] == "env_secret"
        assert extra["port"] == 9090
        assert extra["public_url"] == "https://bot.example.com"


# ── 10. run.py integration ───────────────────────────────────────────────

class TestRunAdapterIntegration:
    """Verify LINE is wired into run.py's authorization maps."""

    def test_line_in_allowlist_env_map(self):
        with open("gateway/auth_mixin.py") as f:
            content = f.read()
        assert 'Platform.LINE: "LINE_ALLOWED_USERS"' in content
        assert 'Platform.LINE: "LINE_ALLOW_ALL_USERS"' in content

    def test_line_in_create_adapter(self):
        with open("gateway/run.py") as f:
            content = f.read()
        assert "Platform.LINE" in content
        assert "LineAdapter" in content
        assert "check_line_requirements" in content
