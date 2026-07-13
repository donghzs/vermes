"""
IRC (Internet Relay Chat) platform adapter for Vermes.

Supports both standard IRC servers (RFC 1459/2812) and Twitch IRC
(chat.twitch.tv) via the ``IRC_SERVER`` config switch.

Design highlights
------------------

**Single adapter, dual mode.** ``IRC_SERVER=twitch`` switches to Twitch IRC
endpoints (chat.twitch.tv:6697), PASS token format (oauth:...), and Twitch-
specific tags (display-name, user-id, badge parsing). Standard IRC uses
NICK + SASL plain auth.

**Channel = chat.** Each IRC channel (``#foo``) or DM (``nick``) maps to a
Vermes session. The adapter listens for PRIVMSG events and relays them.

**No media.** IRC is text-only. Image/voice/file responses are sent as
URLs in plain text.

**Rate limiting.** Twitch enforces 20 messages per 30 seconds per channel.
Standard IRC typically allows 1-2 messages/second. A simple token-bucket
limiter is included.

Configuration (env vars)
-------------------------

Generic IRC::

    IRC_ENABLED=true
    IRC_SERVER=irc.libera.chat       # default
    IRC_PORT=6697                     # TLS default
    IRC_NICK=vermes-bot
    IRC_PASSWORD=<server-password>    # SASL plain or server PASS
    IRC_CHANNELS=#vermes,#help        # comma-separated
    IRC_ALLOW_ALL_USERS=true          # dev escape hatch

Twitch IRC::

    IRC_ENABLED=true
    IRC_SERVER=twitch
    IRC_NICK=vermes_bot
    IRC_PASSWORD=oauth:xxxxxxxx       # Twitch OAuth token
    IRC_CHANNELS=#streamer123         # channels to join
    IRC_ALLOW_ALL_USERS=true

The ``IRC_SERVER=twitch`` shortcut sets host=chat.twitch.tv, port=6697, and
enables Twitch tag parsing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TWITCH_IRC_HOST = "chat.twitch.tv"
TWITCH_IRC_PORT = 6697
DEFAULT_IRC_PORT = 6697
MAX_IRC_MESSAGE_LENGTH = 512  # RFC 1459 raw line limit
MAX_TEXT_LENGTH = 450  # leave room for protocol overhead
RATE_LIMIT_WINDOW = 30  # seconds
RATE_LIMIT_MAX_MESSAGES = 20  # Twitch: 20/30s per channel


def check_irc_requirements() -> bool:
    """Check if IRC dependencies and config are available."""
    if not os.getenv("IRC_NICK"):
        return False
    return True


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Simple token-bucket rate limiter per channel."""

    def __init__(self, max_messages: int = RATE_LIMIT_MAX_MESSAGES,
                 window: float = RATE_LIMIT_WINDOW):
        self._max = max_messages
        self._window = window
        self._timestamps: Dict[str, List[float]] = {}

    def can_send(self, channel: str) -> bool:
        now = time.monotonic()
        ts = self._timestamps.setdefault(channel, [])
        # Prune old entries
        cutoff = now - self._window
        while ts and ts[0] < cutoff:
            ts.pop(0)
        return len(ts) < self._max

    def record(self, channel: str) -> None:
        self._timestamps.setdefault(channel, []).append(time.monotonic())

    def wait_time(self, channel: str) -> float:
        """Seconds to wait before next send is allowed."""
        now = time.monotonic()
        ts = self._timestamps.get(channel, [])
        cutoff = now - self._window
        while ts and ts[0] < cutoff:
            ts.pop(0)
        if len(ts) < self._max:
            return 0.0
        return ts[0] + self._window - now


# ---------------------------------------------------------------------------
# IRC protocol parsing
# ---------------------------------------------------------------------------

@dataclass
class IrcMessage:
    """Parsed IRC protocol message."""
    prefix: str
    command: str
    params: List[str]
    tags: Dict[str, str]  # IRCv3 tags (Twitch uses these)


def parse_irc_line(line: str) -> Optional[IrcMessage]:
    """Parse a raw IRC line into structured form.

    Format: [:@prefix] command [param] [param]... [:trailing]
    """
    if not line:
        return None

    tags: Dict[str, str] = {}
    pos = 0

    # IRCv3 tags
    if line.startswith("@"):
        space = line.find(" ")
        if space == -1:
            return None
        tag_str = line[1:space]
        for tag in tag_str.split(";"):
            if "=" in tag:
                k, v = tag.split("=", 1)
                tags[k] = v
            else:
                tags[tag] = ""
        pos = space + 1

    # Skip whitespace
    while pos < len(line) and line[pos] == " ":
        pos += 1

    # Prefix
    prefix = ""
    if pos < len(line) and line[pos] == ":":
        space = line.find(" ", pos)
        if space == -1:
            return None
        prefix = line[pos + 1:space]
        pos = space + 1

    # Command
    while pos < len(line) and line[pos] == " ":
        pos += 1
    space = line.find(" ", pos)
    if space == -1:
        command = line[pos:]
        return IrcMessage(prefix=prefix, command=command, params=[], tags=tags)
    command = line[pos:space]
    pos = space + 1

    # Params
    params: List[str] = []
    while pos < len(line):
        while pos < len(line) and line[pos] == " ":
            pos += 1
        if pos >= len(line):
            break
        if line[pos] == ":":
            # Trailing parameter (may contain spaces)
            params.append(line[pos + 1:])
            break
        else:
            space = line.find(" ", pos)
            if space == -1:
                params.append(line[pos:])
                break
            params.append(line[pos:space])
            pos = space + 1

    return IrcMessage(prefix=prefix, command=command, params=params, tags=tags)


def _extract_nick(prefix: str) -> str:
    """Extract nick from IRC prefix (nick!user@host)."""
    if "!" in prefix:
        return prefix.split("!", 1)[0]
    return prefix


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class IrcAdapter(BasePlatformAdapter):
    """IRC platform adapter supporting generic IRC and Twitch IRC."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.IRC)
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._rate_limiter = _RateLimiter()
        self._nick = os.getenv("IRC_NICK", "vermes-bot")
        self._password = os.getenv("IRC_PASSWORD", "")
        self._channels: Set[str] = set()
        self._is_twitch = False
        self._server_host = ""
        self._server_port = 0

        # Parse channels from config
        channels_env = os.getenv("IRC_CHANNELS", "")
        for ch in channels_env.split(","):
            ch = ch.strip()
            if ch:
                if not ch.startswith("#"):
                    ch = "#" + ch
                self._channels.add(ch)

        # Determine server type
        server = os.getenv("IRC_SERVER", "").strip().lower()
        if server == "twitch":
            self._is_twitch = True
            self._server_host = TWITCH_IRC_HOST
            self._server_port = TWITCH_IRC_PORT
        else:
            self._server_host = server or "irc.libera.chat"
            self._server_port = int(os.getenv("IRC_PORT", str(DEFAULT_IRC_PORT)))

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to IRC server, authenticate, and join channels."""
        try:
            logger.info("IRC: connecting to %s:%d (twitch=%s)",
                        self._server_host, self._server_port, self._is_twitch)
            self._reader, self._writer = await asyncio.open_connection(
                self._server_host, self._server_port, ssl=True
            )
        except Exception as e:
            logger.error("IRC: connection failed: %s", e)
            self._fatal_error_message = str(e)
            return False

        # Authenticate
        if self._is_twitch:
            # Twitch: PASS oauth:xxx, NICK, then join
            await self._send_raw(f"PASS {self._password}")
            await self._send_raw(f"NICK {self._nick}")
            # Request Twitch-specific capabilities
            await self._send_raw("CAP REQ :twitch.tv/tags")
            await self._send_raw("CAP REQ :twitch.tv/commands")
        else:
            # Standard IRC: SASL plain or server PASS
            if self._password:
                await self._send_raw(f"PASS {self._password}")
            await self._send_raw(f"NICK {self._nick}")
            await self._send_raw(f"USER {self._nick} 0 * :Vermes Bot")

        # Join configured channels
        for channel in self._channels:
            await self._send_raw(f"JOIN {channel}")
            logger.info("IRC: joined %s", channel)

        self._connected = True
        logger.info("IRC: connected as %s on %s", self._nick, self._server_host)

        # Start listen loop
        asyncio.create_task(self._listen_loop())
        return True

    async def disconnect(self) -> None:
        """Gracefully disconnect from IRC."""
        self._connected = False
        if self._writer:
            try:
                await self._send_raw("QUIT :Vermes Bot signing off")
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None
        logger.info("IRC: disconnected")

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """Send a message to an IRC channel or user."""
        if not self._writer:
            return SendResult(success=False, error="Not connected")

        target = chat_id
        # Ensure channel prefix
        if target and not target.startswith("#") and not self._is_pm_target(target):
            target = "#" + target

        # Rate limit check
        wait = self._rate_limiter.wait_time(target)
        if wait > 0:
            await asyncio.sleep(wait)

        # Split long messages
        lines = self._split_text(text)
        last_msg_id: Optional[str] = None

        for line in lines:
            if not line.strip():
                continue
            # Rate limit per channel
            while not self._rate_limiter.can_send(target):
                await asyncio.sleep(0.5)
            self._rate_limiter.record(target)

            await self._send_raw(f"PRIVMSG {target} :{line}")
            last_msg_id = f"{target}:{time.time()}"

        return SendResult(success=True, message_id=last_msg_id)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """IRC has no typing indicator. Twitch uses /me but not for typing."""
        pass

    async def stop_typing(self, chat_id: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Listen loop
    # ------------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Main loop reading from IRC server."""
        while self._connected and self._reader:
            try:
                line = await self._reader.readline()
                if not line:
                    logger.warning("IRC: connection closed by server")
                    self._connected = False
                    break

                line_str = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line_str:
                    continue

                await self._handle_irc_line(line_str)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("IRC: listen loop error: %s", e)

    async def _handle_irc_line(self, line: str) -> None:
        """Parse and dispatch an IRC line."""
        msg = parse_irc_line(line)
        if not msg:
            return

        # PING/PONG keepalive
        if msg.command == "PING":
            await self._send_raw(f"PONG :{msg.params[0] if msg.params else ''}")
            return

        # Respond to server PING with timestamp
        if msg.command == "PING":
            await self._send_raw("PONG")
            return

        # Welcome message — registration successful
        if msg.command == "001":
            logger.info("IRC: welcome received: %s", msg.params[-1] if msg.params else "")
            return

        # Nick in use
        if msg.command == "433":
            logger.error("IRC: nickname %s already in use", self._nick)
            return

        # PRIVMSG — actual chat message
        if msg.command == "PRIVMSG" and len(msg.params) >= 2:
            await self._handle_privmsg(msg)

        # JOIN
        elif msg.command == "JOIN":
            nick = _extract_nick(msg.prefix)
            channel = msg.params[0] if msg.params else ""
            if nick == self._nick:
                logger.info("IRC: confirmed join %s", channel)

        # KICK
        elif msg.command == "KICK" and len(msg.params) >= 2:
            kicked = msg.params[1]
            channel = msg.params[0]
            if kicked == self._nick:
                logger.warning("IRC: kicked from %s, rejoining in 5s", channel)
                await asyncio.sleep(5)
                await self._send_raw(f"JOIN {channel}")

    async def _handle_privmsg(self, msg: IrcMessage) -> None:
        """Handle a PRIVMSG (chat message)."""
        sender = _extract_nick(msg.prefix)
        target = msg.params[0]
        text = msg.params[1]

        # Determine if it's a channel message or DM
        if target.startswith("#"):
            chat_id = target
            chat_type = "group"
            # In channels, only respond if addressed (nick prefix) or highlighted
            addressed = text.lower().startswith(self._nick.lower())
            if not addressed:
                return  # Ignore non-addressed messages in channels
            # Strip the nick prefix
            text = re.sub(rf"^{re.escape(self._nick)}[:,\s]*", "", text, flags=re.IGNORECASE)
        else:
            chat_id = sender
            chat_type = "dm"

        # Twitch: use display-name tag if available
        display_name = msg.tags.get("display-name", sender) if self._is_twitch else sender

        # Build MessageEvent
        from gateway.platforms.base import SessionSource
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=SessionSource(
                platform=Platform.IRC,
                chat_id=chat_id,
                chat_type=chat_type,
                user_id=sender,
                user_name=display_name,
            ),
            message_id=f"{chat_id}:{sender}:{time.time()}",
            raw_message=msg,
        )

        # Dispatch to gateway
        if self._message_handler:
            await self._message_handler(event)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send_raw(self, line: str) -> None:
        """Send a raw IRC line."""
        if not self._writer:
            return
        # Enforce line length
        if len(line) > MAX_IRC_MESSAGE_LENGTH - 2:
            line = line[:MAX_IRC_MESSAGE_LENGTH - 2]
        try:
            self._writer.write((line + "\r\n").encode("utf-8"))
            await self._writer.drain()
        except Exception as e:
            logger.error("IRC: send error: %s", e)
            self._connected = False

    def _split_text(self, text: str) -> List[str]:
        """Split text into IRC-safe chunks."""
        # Strip markdown that IRC can't render
        clean = self._strip_markdown(text)
        if len(clean) <= MAX_TEXT_LENGTH:
            return [clean]

        lines: List[str] = []
        for paragraph in clean.split("\n"):
            if not paragraph.strip():
                continue
            while len(paragraph) > MAX_TEXT_LENGTH:
                # Find a good break point
                break_at = paragraph.rfind(" ", 0, MAX_TEXT_LENGTH)
                if break_at == -1:
                    break_at = MAX_TEXT_LENGTH
                lines.append(paragraph[:break_at].rstrip())
                paragraph = paragraph[break_at:].lstrip()
            if paragraph.strip():
                lines.append(paragraph)
        return lines

    def _strip_markdown(self, text: str) -> str:
        """Remove markdown formatting that IRC can't render."""
        # Remove code blocks
        text = re.sub(r"```[\w]*\n?", "", text)
        # Remove inline code
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # Remove bold
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        # Remove italic
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        # Remove links [text](url) → text (url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        # Remove headers
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove images ![alt](url) → (url)
        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"(\2)", text)
        return text.strip()

    def _is_pm_target(self, target: str) -> bool:
        """Check if target is a user nick (PM) vs channel."""
        return not target.startswith("#")

    @property
    def enforces_own_access_policy(self) -> bool:
        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get info about an IRC channel or user."""
        if chat_id.startswith("#"):
            return {"name": chat_id, "type": "group"}
        return {"name": chat_id, "type": "dm"}
