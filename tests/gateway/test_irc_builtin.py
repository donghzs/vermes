"""Tests for IRC adapter (covers generic IRC and Twitch IRC)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import Platform, PlatformConfig
from gateway.platforms.irc import (
    IrcAdapter,
    IrcMessage,
    _RateLimiter,
    parse_irc_line,
    _extract_nick,
    check_irc_requirements,
)


class TestParseIrcLine:
    def test_simple_privmsg(self):
        line = ":nick!user@host PRIVMSG #channel :Hello world"
        msg = parse_irc_line(line)
        assert msg is not None
        assert msg.prefix == "nick!user@host"
        assert msg.command == "PRIVMSG"
        assert msg.params[0] == "#channel"
        assert msg.params[1] == "Hello world"

    def test_ping(self):
        msg = parse_irc_line("PING :server.example.com")
        assert msg.command == "PING"
        assert msg.params[0] == "server.example.com"

    def test_with_tags(self):
        line = "@display-name=Streamer;color=#FF0000 :nick!nick@nick.tmi.twitch.tv PRIVMSG #channel :Hello"
        msg = parse_irc_line(line)
        assert msg is not None
        assert msg.tags["display-name"] == "Streamer"
        assert msg.tags["color"] == "#FF0000"
        assert msg.command == "PRIVMSG"
        assert msg.params[1] == "Hello"

    def test_empty_line(self):
        assert parse_irc_line("") is None

    def test_join(self):
        msg = parse_irc_line(":nick!user@host JOIN #channel")
        assert msg.command == "JOIN"
        assert msg.params[0] == "#channel"

    def test_numeric_reply(self):
        msg = parse_irc_line(":server 001 nick :Welcome to the network")
        assert msg.command == "001"
        assert msg.params[-1] == "Welcome to the network"


class TestExtractNick:
    def test_with_user_host(self):
        assert _extract_nick("nick!user@host") == "nick"

    def test_without_user_host(self):
        assert _extract_nick("server.example.com") == "server.example.com"


class TestRateLimiter:
    def test_can_send_under_limit(self):
        rl = _RateLimiter(max_messages=5, window=30)
        assert rl.can_send("#test")
        for _ in range(5):
            rl.record("#test")
        assert not rl.can_send("#test")

    def test_different_channels_independent(self):
        rl = _RateLimiter(max_messages=2, window=30)
        rl.record("#ch1")
        rl.record("#ch1")
        assert not rl.can_send("#ch1")
        assert rl.can_send("#ch2")

    def test_wait_time_zero_when_allowed(self):
        rl = _RateLimiter(max_messages=5, window=30)
        assert rl.wait_time("#test") == 0.0


class TestCheckIrcRequirements:
    def test_no_nick(self):
        with patch.dict("os.environ", {}, clear=True):
            assert not check_irc_requirements()

    def test_with_nick(self):
        with patch.dict("os.environ", {"IRC_NICK": "testbot"}):
            assert check_irc_requirements()


def _make_irc_adapter(monkeypatch):
    monkeypatch.setenv("IRC_NICK", "testbot")
    monkeypatch.setenv("IRC_SERVER", "irc.libera.chat")
    monkeypatch.setenv("IRC_CHANNELS", "#test,#help")
    monkeypatch.setenv("IRC_PASSWORD", "secret")
    config = PlatformConfig(enabled=True, extra={})
    return IrcAdapter(config)


def _make_twitch_adapter(monkeypatch):
    monkeypatch.setenv("IRC_NICK", "vermes_bot")
    monkeypatch.setenv("IRC_SERVER", "twitch")
    monkeypatch.setenv("IRC_PASSWORD", "oauth:abc123")
    monkeypatch.setenv("IRC_CHANNELS", "#streamer")
    config = PlatformConfig(enabled=True, extra={})
    a = IrcAdapter(config)
    return a


class TestIrcAdapter:
    def test_init_generic_irc(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        assert adapter._nick == "testbot"
        assert adapter._is_twitch is False
        assert adapter._server_host == "irc.libera.chat"
        assert "#test" in adapter._channels
        assert "#help" in adapter._channels

    def test_init_twitch(self, monkeypatch):
        adapter = _make_twitch_adapter(monkeypatch)
        assert adapter._is_twitch is True
        assert adapter._server_host == "chat.twitch.tv"
        assert adapter._server_port == 6697
        assert "#streamer" in adapter._channels

    def test_split_text_short(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        assert adapter._split_text("Hello world") == ["Hello world"]

    def test_split_text_long(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        long_text = "A" * 1000
        result = adapter._split_text(long_text)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 450

    def test_strip_markdown(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        text = "**bold** and `code` and [link](http://example.com)"
        result = adapter._strip_markdown(text)
        assert "**" not in result
        assert "`" not in result
        assert "link" in result
        assert "http://example.com" in result

    def test_is_pm_target(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        assert adapter._is_pm_target("usernick")
        assert not adapter._is_pm_target("#channel")

    def test_send_not_connected(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        result = asyncio.run(adapter.send("#test", "Hello"))
        assert not result.success
        assert "Not connected" in result.error

    def test_handle_privmsg_channel_addressed(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        msg = IrcMessage(prefix="user!u@h", command="PRIVMSG",
                         params=["#test", "testbot: hello there"], tags={})
        asyncio.run(adapter._handle_privmsg(msg))
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.text == "hello there"
        assert event.source.chat_id == "#test"
        assert event.source.chat_type == "group"

    def test_handle_privmsg_channel_not_addressed(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        msg = IrcMessage(prefix="user!u@h", command="PRIVMSG",
                         params=["#test", "just chatting"], tags={})
        asyncio.run(adapter._handle_privmsg(msg))
        handler.assert_not_called()

    def test_handle_privmsg_dm(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        msg = IrcMessage(prefix="user!u@h", command="PRIVMSG",
                         params=["testbot", "private message"], tags={})
        asyncio.run(adapter._handle_privmsg(msg))
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.text == "private message"
        assert event.source.chat_type == "dm"
        assert event.source.user_id == "user"

    def test_handle_privmsg_twitch_tags(self, monkeypatch):
        adapter = _make_twitch_adapter(monkeypatch)
        handler = AsyncMock()
        adapter._message_handler = handler
        adapter._nick = "vermes_bot"
        msg = IrcMessage(prefix="viewer!viewer@viewer.tmi.twitch.tv",
                         command="PRIVMSG", params=["#streamer", "vermes_bot: hi"],
                         tags={"display-name": "ViewerName", "user-id": "12345"})
        asyncio.run(adapter._handle_privmsg(msg))
        handler.assert_called_once()
        event = handler.call_args[0][0]
        assert event.source.user_name == "ViewerName"

    def test_ping_pong(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        adapter._writer = MagicMock()
        adapter._writer.write = MagicMock()
        adapter._writer.drain = AsyncMock()
        asyncio.run(adapter._handle_irc_line("PING :server.example.com"))
        written = adapter._writer.write.call_args[0][0]
        assert b"PONG" in written

    def test_enforces_own_access_policy(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        assert adapter.enforces_own_access_policy is True

    def test_get_chat_info_channel(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        info = asyncio.run(adapter.get_chat_info("#test"))
        assert info["type"] == "group"

    def test_get_chat_info_dm(self, monkeypatch):
        adapter = _make_irc_adapter(monkeypatch)
        info = asyncio.run(adapter.get_chat_info("usernick"))
        assert info["type"] == "dm"
