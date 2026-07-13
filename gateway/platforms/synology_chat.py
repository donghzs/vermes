"""
Synology Chat platform adapter for Vermes.

Synology Chat is the built-in chat service on Synology NAS devices.
It uses a simple HTTP webhook API with token-based authentication.

Design highlights
------------------

**Webhook-based inbound.** Synology Chat sends incoming messages to a
configured webhook URL (outgoing webhook). No signature verification —
the token in the URL serves as authentication.

**HTTP POST outbound.** Messages are sent via POST to the Synology Chat
incoming webhook URL with a ``payload`` JSON field.

**Text + file support.** Synology Chat supports text messages and file
attachments via URL.

**Simple protocol.** No complex auth flow — just webhook URLs with tokens.

Configuration (env vars)
-------------------------

::

    SYNOLOGY_CHAT_ENABLED=true
    SYNOLOGY_CHAT_INCOMING_URL=https://nas.example.com/webapi/entry.cgi?api=SYNO.Chat.External&method=incoming&version=1&token=<incoming-token>
    SYNOLOGY_CHAT_OUTGOING_TOKEN=<outgoing-webhook-token>  # for URL verification
    SYNOLOGY_CHAT_WEBHOOK_PORT=9222
    SYNOLOGY_CHAT_WEBHOOK_PATH=/synology/webhook
    SYNOLOGY_CHAT_ALLOW_ALL_USERS=true

Setup:
1. Open Synology Chat → Settings → Integrations
2. Create a new incoming webhook (get the incoming URL with token)
3. Create a new outgoing webhook pointing to your server URL
4. Set the env vars above
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.config import Platform, PlatformConfig

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_PORT = 9222
DEFAULT_WEBHOOK_PATH = "/synology/webhook"
MAX_TEXT_LENGTH = 2000


def check_synology_chat_requirements() -> bool:
    """Check if Synology Chat dependencies and config are available."""
    if not os.getenv("SYNOLOGY_CHAT_INCOMING_URL"):
        return False
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return True


class SynologyChatAdapter(BasePlatformAdapter):
    """Synology Chat platform adapter."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SYNOLOGY_CHAT)
        self._incoming_url = os.getenv("SYNOLOGY_CHAT_INCOMING_URL", "")
        self._outgoing_token = os.getenv("SYNOLOGY_CHAT_OUTGOING_TOKEN", "")
        self._webhook_port = int(os.getenv("SYNOLOGY_CHAT_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT)))
        self._webhook_path = os.getenv("SYNOLOGY_CHAT_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH)
        self._http_session: Optional[Any] = None
        self._web_runner: Optional[Any] = None

    async def connect(self) -> bool:
        """Start webhook server for incoming Synology Chat messages."""
        try:
            import aiohttp
            from aiohttp import web
        except ImportError:
            logger.error("Synology Chat: aiohttp not installed")
            return False

        self._http_session = aiohttp.ClientSession()

        app = web.Application()
        app.router.add_post(self._webhook_path, self._handle_webhook)

        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, "0.0.0.0", self._webhook_port)
        await site.start()

        logger.info("Synology Chat: webhook server on port %d, path %s",
                    self._webhook_port, self._webhook_path)
        return True

    async def disconnect(self) -> None:
        if self._web_runner:
            await self._web_runner.cleanup()
        if self._http_session:
            await self._http_session.close()
        self._http_session = None
        self._web_runner = None
        logger.info("Synology Chat: disconnected")

    # ------------------------------------------------------------------
    # Webhook handler
    # ------------------------------------------------------------------

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        """Handle incoming Synology Chat webhook."""
        try:
            # Synology sends form data
            data = await request.post()
            if not data:
                body = await request.read()
                data = json.loads(body.decode("utf-8"))

            # Verify token if configured
            if self._outgoing_token:
                token = data.get("token", "")
                if token != self._outgoing_token:
                    logger.warning("Synology Chat: invalid webhook token")
                    return web.Response(status=403, text="Invalid token")

            from gateway.platforms.base import SessionSource

            user_id = str(data.get("user_id", ""))
            username = data.get("username", user_id)
            text = data.get("text", "")
            message_id = f"synochat:{user_id}:{time.time()}"

            # Channel ID may be present
            channel_id = str(data.get("channel_id", user_id))
            chat_type = "dm" if data.get("channel_type") == "private" else "group"

            if not text:
                return web.Response(text='{"status":"ok"}')

            event = MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                source=SessionSource(
                    platform=Platform.SYNOLOGY_CHAT,
                    chat_id=channel_id,
                    chat_type=chat_type,
                    user_id=user_id,
                    user_name=username,
                ),
                message_id=message_id,
                raw_message=dict(data),
            )

            if self._message_handler:
                await self._message_handler(event)

            return web.Response(text='{"status":"ok"}', content_type="application/json")

        except Exception as e:
            logger.error("Synology Chat: webhook error: %s", e, exc_info=True)
            return web.Response(status=500, text="Internal error")

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
        """Send a message via Synology Chat incoming webhook."""
        if not self._http_session:
            return SendResult(success=False, error="Not connected")

        # Synology Chat uses a single incoming webhook URL — it posts to the channel
        # the webhook was created for. If chat_id routing is needed, multiple
        # webhooks would be required.
        chunks = self._split_text(text)
        last_msg_id: Optional[str] = None

        for chunk in chunks:
            if not chunk.strip():
                continue

            payload = {"text": chunk}

            try:
                import aiohttp
                async with self._http_session.post(
                    self._incoming_url,
                    json={"payload": json.dumps(payload)},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                    if not result.get("success", True):
                        error_msg = result.get("error", "Unknown error")
                        logger.error("Synology Chat: send error: %s", error_msg)
                        return SendResult(success=False, error=error_msg, raw_response=result)
                    last_msg_id = f"synochat:{chat_id}:{time.time()}"
            except Exception as e:
                logger.error("Synology Chat: send exception: %s", e)
                return SendResult(success=False, error=str(e), retryable=True)

        return SendResult(success=True, message_id=last_msg_id)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass

    async def stop_typing(self, chat_id: str) -> None:
        pass

    def _split_text(self, text: str) -> List[str]:
        if len(text) <= MAX_TEXT_LENGTH:
            return [text]
        chunks: List[str] = []
        for paragraph in text.split("\n"):
            if not paragraph.strip():
                continue
            while len(paragraph) > MAX_TEXT_LENGTH:
                break_at = paragraph.rfind(" ", 0, MAX_TEXT_LENGTH)
                if break_at == -1:
                    break_at = MAX_TEXT_LENGTH
                chunks.append(paragraph[:break_at])
                paragraph = paragraph[break_at:]
            if paragraph.strip():
                chunks.append(paragraph)
        return chunks

    @property
    def enforces_own_access_policy(self) -> bool:
        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get info about a Synology Chat channel."""
        return {"name": chat_id, "type": "group"}
