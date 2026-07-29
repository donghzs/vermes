"""
LINE Messaging API platform adapter for Vermes.

Runs an aiohttp webhook server that accepts LINE webhook events
(HMAC-SHA256 signature-verified), and relays messages between LINE chats
(1:1, groups, rooms) and the Vermes agent.

Design highlights
-----------------

**Reply token preferred, Push fallback.** LINE's reply token is single-use
and expires ~60s after inbound event. Reply first (free), fall back to
metered Push API when token absent/expired/rejected.

**Slow-LLM postback button.** When the LLM is still running past
``LINE_SLOW_RESPONSE_THRESHOLD`` (default 45s), burn the reply token to send a
Template Buttons bubble — user taps later to fetch answer via fresh reply
token (free). Set threshold to 0 to disable and always Push-fallback.

**Three-allowlist gating.** Separate allowlists for users (U-prefixed),
groups (C-prefixed), rooms (R-prefixed). ``LINE_ALLOW_ALL_USERS=true``
is a dev-only escape hatch.

**Media via public HTTPS.** LINE requires media files at reachable HTTPS URLs
(no binary upload). Registered tempfiles served under
``/line/media/<token>/<filename>`` with traversal guard.
``LINE_PUBLIC_URL`` overrides host:port construction.

**5-message batching.** LINE max 5 message objects per Reply/Push.
Longer responses smart-chunked at 4500 chars (hard limit 5000/bubble).

Port notes
----------

Ported from Vermes Agent upstream plugins/platforms/line/adapter.py (1,652 lines),
a synthesis of 7 community PRs. Adapted for Vermes:
- Builtin platform path (gateway/config.py Platform enum + gateway/run.py
  _create_adapter) instead of plugin registration
- Vermes base gateway conventions
"""

from __future__ import annotations

import asyncio
import base64
import enum
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote as _urlquote

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_image_from_bytes,
)
from gateway.config import Platform

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_LOADING_URL = "https://api.line.me/v2/bot/chat/loading/start"
LINE_CONTENT_URL_FMT = "https://api-data.line.me/v2/bot/message/{message_id}/content"
LINE_BOT_INFO_URL = "https://api.line.me/v2/bot/info"

LINE_PER_BUBBLE_CHARS = 5000
LINE_SAFE_BUBBLE_CHARS = 4500
LINE_MAX_MESSAGES_PER_CALL = 5
LINE_REPLY_TOKEN_TTL_SECONDS = 50

WEBHOOK_BODY_MAX_BYTES = 1_048_576  # 1 MiB
DEFAULT_WEBHOOK_PORT = 8646
DEFAULT_WEBHOOK_PATH = "/line/webhook"
DEFAULT_MEDIA_PATH_PREFIX = "/line/media"

DEFAULT_SLOW_RESPONSE_THRESHOLD = 45.0
DEFAULT_PENDING_REPLY_TEXT = "🤔 Still thinking. Tap below to fetch the answer when it's ready."
DEFAULT_BUTTON_LABEL = "Get answer"
DEFAULT_DELIVERED_TEXT = "Already replied ✅"
DEFAULT_INTERRUPTED_TEXT = "Run was interrupted before completion."

MEDIA_TOKEN_TTL_SECONDS = 1800
LINE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
LINE_AV_MAX_BYTES = 200 * 1024 * 1024

_LINE_MESSAGE_TYPES = {
    "text": MessageType.TEXT,
    "image": MessageType.PHOTO,
    "video": MessageType.VIDEO,
    "audio": MessageType.VOICE,
    "file": MessageType.DOCUMENT,
    "location": MessageType.LOCATION,
    "sticker": MessageType.STICKER,
}

_FALLBACK_PNG_PREVIEW = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100377a7ff20000000049454e"
    "44ae426082"
)


def check_line_requirements() -> bool:
    """Verify LINE adapter dependencies and credentials are available."""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN") and os.getenv("LINE_CHANNEL_SECRET"))


# ---------------------------------------------------------------------------
# Markdown stripping (URL-preserving)
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_ITAL_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_MD_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_MD_CODE_BLOCK_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)


def strip_markdown_preserving_urls(text: str) -> str:
    """Strip Markdown that LINE can't render, but keep URLs tappable."""
    if not text:
        return text

    def _unfence(m: re.Match) -> str:
        return m.group(1).rstrip("\n")
    text = _MD_CODE_BLOCK_RE.sub(_unfence, text)
    text = _MD_CODE_INLINE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITAL_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("• ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Smart chunking (LINE 5000-char bubble limit)
# ---------------------------------------------------------------------------

def split_for_line(text: str) -> List[str]:
    """Split long text into chunks that fit LINE's per-bubble limit."""
    if len(text) <= LINE_SAFE_BUBBLE_CHARS:
        return [text] if text else []

    chunks: List[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= LINE_SAFE_BUBBLE_CHARS:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, LINE_SAFE_BUBBLE_CHARS)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, LINE_SAFE_BUBBLE_CHARS)
        if split_at == -1:
            split_at = remaining.rfind(". ", 0, LINE_SAFE_BUBBLE_CHARS)
        if split_at == -1 or split_at < LINE_SAFE_BUBBLE_CHARS // 2:
            split_at = LINE_SAFE_BUBBLE_CHARS
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


# ---------------------------------------------------------------------------
# LINE message builders
# ---------------------------------------------------------------------------

def _text_message(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def _image_message(url: str, preview_url: str = "") -> Dict[str, Any]:
    return {
        "type": "image",
        "originalContentUrl": url,
        "previewImageUrl": preview_url or url,
    }


def _audio_message(url: str, duration_ms: int = 0) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"type": "audio", "originalContentUrl": url}
    if duration_ms > 0:
        msg["duration"] = duration_ms
    return msg


def _video_message(url: str, preview_url: str = "") -> Dict[str, Any]:
    return {
        "type": "video",
        "originalContentUrl": url,
        "previewImageUrl": preview_url or _FALLBACK_PNG_PREVIEW,
    }


def build_postback_button_message(
    text: str, button_label: str, request_id: str
) -> Dict[str, Any]:
    """Template Buttons — slow-LLM postback bubble."""
    truncated = text if len(text) <= 160 else text[:157] + "..."
    alt = text if len(text) <= 400 else text[:397] + "..."
    return {
        "type": "template",
        "altText": alt,
        "template": {
            "type": "buttons",
            "text": truncated,
            "actions": [
                {
                    "type": "postback",
                    "label": button_label[:20] or "Get answer",
                    "data": json.dumps(
                        {"action": "show_response", "request_id": request_id}
                    ),
                    "displayText": button_label[:300] or "Get answer",
                }
            ],
        },
    }


_SYSTEM_BYPASS_PREFIXES: Tuple[str, ...] = (
    "⚡ Interrupting",
    "⏳ Queued",
    "⏩ Steered",
    "💾",
)


def _is_system_bypass(content: str) -> bool:
    if not content:
        return False
    return any(content.startswith(p) for p in _SYSTEM_BYPASS_PREFIXES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _csv_set(value: str) -> Set[str]:
    if not value:
        return set()
    return {x.strip() for x in value.split(",") if x.strip()}


def _truthy_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Slow-LLM postback cache
# ---------------------------------------------------------------------------

class _State(enum.Enum):
    PENDING = 1
    READY = 2
    DELIVERED = 3
    ERROR = 4


@dataclass
class _CacheEntry:
    state: _State
    chat_id: str
    payload: Any = None
    error_message: str = ""
    created_at: float = time.time()


class RequestCache:
    """In-memory cache for slow-LLM postback responses."""

    def __init__(self, max_entries: int = 500) -> None:
        self._entries: Dict[str, _CacheEntry] = {}
        self._max = max_entries

    def register_pending(self, chat_id: str) -> str:
        self.prune()
        rid = uuid.uuid4().hex[:16]
        self._entries[rid] = _CacheEntry(state=_State.PENDING, chat_id=chat_id)
        return rid

    def get(self, request_id: str) -> Optional[_CacheEntry]:
        return self._entries.get(request_id)

    def set_ready(self, request_id: str, payload: Any) -> None:
        if entry := self._entries.get(request_id):
            entry.state = _State.READY
            entry.payload = payload

    def set_error(self, request_id: str, message: str) -> None:
        if entry := self._entries.get(request_id):
            entry.state = _State.ERROR
            entry.error_message = message

    def mark_delivered(self, request_id: str) -> None:
        if entry := self._entries.get(request_id):
            entry.state = _State.DELIVERED

    def find_pending_for_chat(self, chat_id: str) -> Optional[str]:
        for rid, entry in self._entries.items():
            if entry.chat_id == chat_id and entry.state == _State.PENDING:
                return rid
        return None

    def prune(self) -> int:
        removed = 0
        if len(self._entries) > self._max:
            stale = sorted(self._entries.items(), key=lambda x: x[1].created_at)
            for rid, _ in stale[: len(self._entries) - self._max]:
                self._entries.pop(rid, None)
                removed += 1
        return removed


# ---------------------------------------------------------------------------
# Message deduplicator
# ---------------------------------------------------------------------------

class _MessageDeduplicator:
    """Prevent duplicate webhook events from being processed."""

    def __init__(self, max_size: int = 1000) -> None:
        self._seen: Set[str] = set()
        self._order: List[str] = []
        self._max = max_size

    def is_duplicate(self, event_id: str) -> bool:
        if event_id in self._seen:
            return True
        self._seen.add(event_id)
        self._order.append(event_id)
        if len(self._order) > self._max:
            old = self._order.pop(0)
            self._seen.discard(old)
        return False


# ---------------------------------------------------------------------------
# LINE API client
# ---------------------------------------------------------------------------

class _LineClient:
    """Minimal wrapper over LINE Messaging API endpoints."""

    def __init__(self, channel_access_token: str, *, timeout: float = 15.0) -> None:
        self._token = channel_access_token
        self._timeout = timeout
        self._session: Any = None

    async def _ensure_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def reply(self, reply_token: str, messages: List[Dict]) -> Dict:
        session = await self._ensure_session()
        async with session.post(
            LINE_REPLY_URL,
            headers=self._headers,
            json={"replyToken": reply_token, "messages": messages},
        ) as resp:
            return await resp.json()

    async def push(self, chat_id: str, messages: List[Dict]) -> Dict:
        session = await self._ensure_session()
        async with session.post(
            LINE_PUSH_URL,
            headers=self._headers,
            json={"to": chat_id, "messages": messages},
        ) as resp:
            return await resp.json()

    async def loading(self, chat_id: str) -> bool:
        """Trigger LINE's chat loading animation (30-sec auto-dismiss)."""
        session = await self._ensure_session()
        try:
            async with session.post(
                LINE_LOADING_URL,
                headers=self._headers,
                json={"chatId": chat_id},
            ) as resp:
                return resp.status == 202
        except Exception:
            return False

    async def get_content(self, message_id: str) -> Optional[bytes]:
        session = await self._ensure_session()
        url = LINE_CONTENT_URL_FMT.format(message_id=message_id)
        headers = {"Authorization": f"Bearer {self._token}"}
        async with session.get(url, headers=headers) as resp:
            if resp.status == 200:
                return await resp.read()
        return None

    async def get_bot_user_id(self) -> Optional[str]:
        session = await self._ensure_session()
        async with session.get(LINE_BOT_INFO_URL, headers=self._headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("userId")
        return None

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None


# ---------------------------------------------------------------------------
# LINE Adapter
# ---------------------------------------------------------------------------

class LineAdapter(BasePlatformAdapter):
    """LINE Messaging API gateway adapter for Vermes."""

    def __init__(self, config, **kwargs):
        platform = Platform("line")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        self.channel_access_token = (
            os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
            or extra.get("channel_access_token", "")
        )
        self.channel_secret = (
            os.getenv("LINE_CHANNEL_SECRET")
            or extra.get("channel_secret", "")
        )

        self.webhook_host = os.getenv("LINE_HOST") or extra.get("host", "0.0.0.0")
        try:
            self.webhook_port = int(
                os.getenv("LINE_PORT") or extra.get("port", DEFAULT_WEBHOOK_PORT)
            )
        except (TypeError, ValueError):
            self.webhook_port = DEFAULT_WEBHOOK_PORT
        self.webhook_path = extra.get("webhook_path", DEFAULT_WEBHOOK_PATH)

        self.public_base_url = (
            os.getenv("LINE_PUBLIC_URL")
            or extra.get("public_url", "")
            or ""
        ).rstrip("/")

        self.allow_all = _truthy_env(
            "LINE_ALLOW_ALL_USERS", bool(extra.get("allow_all_users", False))
        )
        self.allowed_users = _csv_set(
            os.getenv("LINE_ALLOWED_USERS", "")
        ) | set(extra.get("allowed_users", []))
        self.allowed_groups = _csv_set(
            os.getenv("LINE_ALLOWED_GROUPS", "")
        ) | set(extra.get("allowed_groups", []))
        self.allowed_rooms = _csv_set(
            os.getenv("LINE_ALLOWED_ROOMS", "")
        ) | set(extra.get("allowed_rooms", []))

        try:
            self.slow_response_threshold = float(
                os.getenv("LINE_SLOW_RESPONSE_THRESHOLD")
                or extra.get("slow_response_threshold", DEFAULT_SLOW_RESPONSE_THRESHOLD)
            )
        except (TypeError, ValueError):
            self.slow_response_threshold = DEFAULT_SLOW_RESPONSE_THRESHOLD

        self.pending_text = os.getenv("LINE_PENDING_TEXT") or extra.get(
            "pending_text", DEFAULT_PENDING_REPLY_TEXT
        )
        self.button_label = os.getenv("LINE_BUTTON_LABEL") or extra.get(
            "button_label", DEFAULT_BUTTON_LABEL
        )
        self.delivered_text = os.getenv("LINE_DELIVERED_TEXT") or extra.get(
            "delivered_text", DEFAULT_DELIVERED_TEXT
        )
        self.interrupted_text = os.getenv("LINE_INTERRUPTED_TEXT") or extra.get(
            "interrupted_text", DEFAULT_INTERRUPTED_TEXT
        )

        self._client: Optional[_LineClient] = None
        self._app = None
        self._runner = None
        self._site = None
        self._reply_tokens: Dict[str, Tuple[str, float]] = {}
        self._cache = RequestCache()
        self._dedup = _MessageDeduplicator()
        self._bot_user_id: Optional[str] = None
        self._lock_key: Optional[str] = None

        self._media_tokens: Dict[str, Tuple[str, float]] = {}
        self._media_temp_paths: Set[str] = set()
        self._media_ttl = MEDIA_TOKEN_TTL_SECONDS
        self._pending_buttons: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        if not self.channel_access_token or not self.channel_secret:
            self._set_fatal_error(
                "config_missing",
                "LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET must be set",
                retryable=False,
            )
            return False

        try:
            from gateway.status import acquire_scoped_lock
            tok_hash = hashlib.sha256(self.channel_access_token.encode()).hexdigest()[:16]
            if not acquire_scoped_lock("line", tok_hash):
                self._set_fatal_error(
                    "lock_conflict",
                    "LINE channel already in use by another profile",
                    retryable=False,
                )
                return False
            self._lock_key = tok_hash
        except ImportError:
            self._lock_key = None

        self._client = _LineClient(self.channel_access_token)

        try:
            self._bot_user_id = await self._client.get_bot_user_id()
        except Exception as exc:
            logger.debug("LINE: get_bot_user_id failed: %s", exc)
            self._bot_user_id = None

        try:
            from aiohttp import web
        except ImportError:
            self._set_fatal_error(
                "missing_dep",
                "aiohttp is required — install with `pip install aiohttp`",
                retryable=False,
            )
            return False

        self._app = web.Application(client_max_size=WEBHOOK_BODY_MAX_BYTES)
        self._app.router.add_post(self.webhook_path, self._handle_webhook)
        self._app.router.add_get(f"{self.webhook_path}/health", self._handle_health)
        self._app.router.add_get(
            f"{DEFAULT_MEDIA_PATH_PREFIX}/{{token}}/{{filename}}",
            self._handle_media,
        )

        self._runner = web.AppRunner(self._app)
        try:
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.webhook_host, self.webhook_port)
            await self._site.start()
        except OSError as exc:
            self._set_fatal_error(
                "bind_failed",
                f"Could not bind LINE webhook on {self.webhook_host}:{self.webhook_port}: {exc}",
                retryable=True,
            )
            return False

        self._mark_connected()
        logger.info(
            "LINE: webhook listening on %s:%s%s%s",
            self.webhook_host,
            self.webhook_port,
            self.webhook_path,
            f" (public: {self.public_base_url})" if self.public_base_url else "",
        )
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

        if self._site is not None:
            try:
                await self._site.stop()
            except Exception:
                pass
            self._site = None
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
            self._runner = None
        self._app = None

        for path in list(self._media_temp_paths):
            try:
                os.unlink(path)
            except OSError:
                pass
        self._media_temp_paths.clear()
        self._media_tokens.clear()

        if self._lock_key:
            try:
                from gateway.status import release_scoped_lock
                release_scoped_lock("line", self._lock_key)
            except Exception:
                pass
            self._lock_key = None

        if self._client:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Webhook handlers
    # ------------------------------------------------------------------

    async def _handle_health(self, request) -> Any:
        from aiohttp import web
        return web.json_response({"status": "ok", "connected": self._running})

    async def _handle_media(self, request) -> Any:
        from aiohttp import web
        token = request.match_info.get("token", "")
        filename = request.match_info.get("filename", "")

        entry = self._media_tokens.get(token)
        if not entry or time.time() > entry[1]:
            return web.Response(status=404, text="Media token expired or invalid")

        file_path = entry[0]
        if os.path.basename(file_path) != os.path.basename(filename):
            return web.Response(status=403, text="Filename mismatch")

        if not os.path.isfile(file_path):
            return web.Response(status=404, text="File not found")

        content_type, _ = mimetypes.guess_type(file_path)
        headers = {"Cache-Control": f"public, max-age={MEDIA_TOKEN_TTL_SECONDS}"}
        return web.FileResponse(file_path, headers=headers)

    async def _handle_webhook(self, request) -> Any:
        from aiohttp import web

        body = await request.read()
        if len(body) > WEBHOOK_BODY_MAX_BYTES:
            return web.Response(status=413, text="Payload too large")

        signature = request.headers.get("X-Line-Signature", "")
        if not self._verify_signature(body, signature):
            logger.warning("LINE: webhook signature verification failed")
            return web.Response(status=401, text="Invalid signature")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        events = data.get("events", [])
        if not events:
            return web.Response(status=200, text="OK")

        for event in events:
            asyncio.create_task(self._process_event(event))

        return web.Response(status=200, text="OK")

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        if not self.channel_secret or not signature:
            return False
        expected = base64.b64encode(
            hmac.new(
                self.channel_secret.encode(), body, hashlib.sha256
            ).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)

    async def _process_event(self, raw_event: Dict[str, Any]) -> None:
        event_type = raw_event.get("type", "")
        source = raw_event.get("source", {})
        message = raw_event.get("message", {})

        event_id = raw_event.get("webhookEventId", "")
        if event_id and self._dedup.is_duplicate(event_id):
            return

        if event_type == "postback":
            await self._handle_postback(raw_event)
            return

        if event_type not in ("message", "unsend"):
            return

        if event_type == "unsend":
            return

        user_id = source.get("userId", "")
        chat_type = source.get("type", "")  # user / group / room
        chat_id = source.get("userId") or source.get("groupId") or source.get("roomId", "")

        # Filter out our own messages
        if user_id == self._bot_user_id:
            return

        # Allowlist gating
        if not self._is_authorized(chat_type, chat_id):
            logger.debug("LINE: unauthorized sender %s in %s %s", user_id, chat_type, chat_id)
            return

        # Stash reply token
        reply_token = raw_event.get("replyToken", "")
        if reply_token and chat_id:
            self._reply_tokens[chat_id] = (reply_token, time.time() + LINE_REPLY_TOKEN_TTL_SECONDS)

        msg_type = _LINE_MESSAGE_TYPES.get(message.get("type", "text"), MessageType.TEXT)
        msg_id = message.get("id", "")
        text = message.get("text", "")

        if msg_type == MessageType.TEXT and not text:
            return

        event = MessageEvent(
            chat_id=chat_id,
            text=text,
            message_type=msg_type,
            source={
                "platform": "line",
                "user_id": user_id,
                "chat_type": {"user": "dm", "group": "group", "room": "channel"}.get(
                    chat_type, "dm"
                ),
                "message_id": msg_id or event_id,
            },
            metadata={
                "attach_reply": True if reply_token else False,
            },
        )

        await self.handle_message(event)

    async def _handle_postback(self, raw_event: Dict[str, Any]) -> None:
        postback = raw_event.get("postback", {})
        data_str = postback.get("data", "{}")
        source = raw_event.get("source", {})
        chat_id = source.get("userId") or source.get("groupId") or source.get("roomId", "")

        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            return

        if data.get("action") != "show_response":
            return

        request_id = data.get("request_id", "")
        entry = self._cache.get(request_id)
        if not entry:
            await self._send_text_chunks(chat_id, self.delivered_text, force_push=True)
            return

        if entry.state == _State.READY and entry.payload:
            await self._send_text_chunks(chat_id, str(entry.payload), force_push=True)
            self._cache.mark_delivered(request_id)
            self._pending_buttons.pop(chat_id, None)
        elif entry.state == _State.ERROR:
            await self._send_text_chunks(
                chat_id,
                f"❌ {entry.error_message}" if entry.error_message else self.interrupted_text,
                force_push=True,
            )
        else:
            await self._send_text_chunks(chat_id, self.pending_text, force_push=True)

    def _is_authorized(self, chat_type: str, chat_id: str) -> bool:
        if self.allow_all:
            return True
        if chat_type == "user":
            return not self.allowed_users or chat_id in self.allowed_users
        elif chat_type == "group":
            return not self.allowed_groups or chat_id in self.allowed_groups
        elif chat_type == "room":
            return not self.allowed_rooms or chat_id in self.allowed_rooms
        return True  # unknown type: allow (fallback to gateway-wide auth)

    # ------------------------------------------------------------------
    # Send
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="LINE adapter not connected")

        if _is_system_bypass(content):
            return await self._send_text_chunks(chat_id, content, force_push=False)

        pending_rid = self._pending_buttons.get(chat_id)
        if pending_rid:
            self._cache.set_ready(pending_rid, content)
            return SendResult(success=True, message_id=pending_rid)

        return await self._send_text_chunks(chat_id, content, force_push=False)

    async def _send_text_chunks(
        self, chat_id: str, content: str, *, force_push: bool
    ) -> SendResult:
        if not self._client:
            return SendResult(success=False, error="LINE adapter not connected")

        chunks = split_for_line(strip_markdown_preserving_urls(content))
        if not chunks:
            return SendResult(success=True, message_id=None)
        messages = [_text_message(c) for c in chunks][:LINE_MAX_MESSAGES_PER_CALL]

        token, used_reply = self._consume_reply_token(chat_id)
        if used_reply and not force_push:
            try:
                await self._client.reply(token, messages)
                return SendResult(success=True, message_id=token)
            except Exception as exc:
                logger.info("LINE: reply token rejected (%s); falling back to push", exc)

        try:
            await self._client.push(chat_id, messages)
            return SendResult(success=True, message_id=None)
        except Exception as exc:
            logger.error("LINE: push send failed: %s", exc)
            return SendResult(success=False, error=str(exc))

    def _consume_reply_token(self, chat_id: str) -> Tuple[str, bool]:
        entry = self._reply_tokens.pop(chat_id, None)
        if not entry:
            return "", False
        token, expires_at = entry
        if not token or time.time() >= expires_at:
            return "", False
        return token, True

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if self._client and chat_id:
            await self._client.loading(chat_id)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        prefix = (chat_id or "")[:1]
        chat_type = {"U": "dm", "C": "group", "R": "channel"}.get(prefix, "dm")
        return {"name": chat_id or "", "type": chat_type}

    def format_message(self, content: str) -> str:
        return strip_markdown_preserving_urls(content)

    # ------------------------------------------------------------------
    # Media helpers
    # ------------------------------------------------------------------

    def _register_media(self, file_path: str, *, cleanup: bool = False) -> str:
        token = secrets.token_urlsafe(24)
        expiry = time.time() + self._media_ttl
        self._media_tokens[token] = (file_path, expiry)
        if cleanup:
            self._media_temp_paths.add(file_path)
        return token

    def _media_url(self, token: str, filename: str) -> str:
        base = self.public_base_url
        if not base:
            host = self.webhook_host
            if host in ("0.0.0.0", "::"):
                host = "127.0.0.1"
            base = f"http://{host}:{self.webhook_port}"
        safe_name = _urlquote(filename, safe="")
        return f"{base}{DEFAULT_MEDIA_PATH_PREFIX}/{token}/{safe_name}"

    async def send_image_file(
        self, chat_id: str, file_path: str, caption: Optional[str] = None
    ) -> SendResult:
        if not self._client or not os.path.isfile(file_path):
            return SendResult(success=False, error="File not found or adapter not connected")
        fname = os.path.basename(file_path)
        token = self._register_media(file_path)
        url = self._media_url(token, fname)
        try:
            resp = await self._client.push(chat_id, [_image_message(url)])
            return SendResult(success=True, message_id=resp.get("sentMessages", [{}])[0].get("id", ""))
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_voice(self, chat_id: str, file_path: str) -> SendResult:
        return await self._send_media(chat_id, file_path, _audio_message)

    async def send_video(
        self, chat_id: str, file_path: str, caption: Optional[str] = None
    ) -> SendResult:
        return await self._send_media(chat_id, file_path, _video_message)

    async def send_document(
        self, chat_id: str, file_path: str, caption: Optional[str] = None
    ) -> SendResult:
        # LINE has no generic file type — send as text link pointing to media URL
        if not self._client or not os.path.isfile(file_path):
            return SendResult(success=False, error="File not found or adapter not connected")
        fname = os.path.basename(file_path)
        token = self._register_media(file_path)
        url = self._media_url(token, fname)
        label = caption or fname
        return await self._send_text_chunks(chat_id, f"📎 {label}\n{url}", force_push=True)

    async def _send_media(
        self,
        chat_id: str,
        file_path: str,
        msg_builder,
    ) -> SendResult:
        if not self._client or not os.path.isfile(file_path):
            return SendResult(success=False, error="File not found or adapter not connected")
        fname = os.path.basename(file_path)
        token = self._register_media(file_path)
        url = self._media_url(token, fname)
        try:
            resp = await self._client.push(chat_id, [msg_builder(url)])
            return SendResult(success=True, message_id=resp.get("sentMessages", [{}])[0].get("id", ""))
        except Exception as exc:
            return SendResult(success=False, error=str(exc))
