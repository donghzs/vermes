"""
Zalo Official Account (OA) platform adapter for Vermes.

Zalo is Vietnam's largest messaging platform with 79M+ monthly active users.
This adapter connects to Zalo's OA API to send/receive messages via webhook.

Design highlights
------------------

**Webhook-based inbound.** Zalo sends user messages to our webhook endpoint.
We verify the request using HMAC-SHA256 with the OA secret key.

**REST API outbound.** Messages are sent via POST to Zalo's OA API
(``https://openapi.zalo.me/v2.0/oa/message``) using the access token.

**Text + attachment support.** Zalo OA supports text, image (upload or URL),
and file attachments. This adapter sends text by default and falls back to
URL-based image/file delivery.

**User ID tracking.** Zalo uses numeric user IDs. Each user interaction
is tracked by their Zalo user ID for session continuity.

Configuration (env vars)
-------------------------

::

    ZALO_ENABLED=true
    ZALO_OA_ID=<your-oa-id>
    ZALO_ACCESS_TOKEN=<access-token-from-zalo-business>
    ZALO_SECRET_KEY=<oa-secret-for-webhook-verification>
    ZALO_WEBHOOK_PORT=9221               # optional, default 9221
    ZALO_WEBHOOK_PATH=/zalo/webhook      # optional
    ZALO_ALLOW_ALL_USERS=true            # dev escape hatch

Getting credentials:
1. Visit https://oa.zalo.me/ and create an Official Account
2. In Settings → API, get the OA Access Token and Secret Key
3. Configure the webhook URL to point to your server's public URL + webhook path
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

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

ZALO_API_BASE = "https://openapi.zalo.me/v2.0/oa"
ZALO_SEND_MESSAGE_URL = f"{ZALO_API_BASE}/message"
ZALO_GET_USER_URL = f"{ZALO_API_BASE}/getprofile"
ZALO_UPLOAD_IMAGE_URL = "https://openapi.zalo.me/v2.0/oa/upload/image"
DEFAULT_WEBHOOK_PORT = 9221
DEFAULT_WEBHOOK_PATH = "/zalo/webhook"
MAX_TEXT_LENGTH = 2000  # Zalo text message limit


def check_zalo_requirements() -> bool:
    """Check if Zalo dependencies and config are available."""
    if not os.getenv("ZALO_ACCESS_TOKEN"):
        return False
    if not os.getenv("ZALO_SECRET_KEY"):
        return False
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return True


class ZaloAdapter(BasePlatformAdapter):
    """Zalo Official Account platform adapter."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.ZALO)
        self._access_token = os.getenv("ZALO_ACCESS_TOKEN", "")
        self._secret_key = os.getenv("ZALO_SECRET_KEY", "")
        self._oa_id = os.getenv("ZALO_OA_ID", "")
        self._webhook_port = int(os.getenv("ZALO_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT)))
        self._webhook_path = os.getenv("ZALO_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH)
        self._http_session: Optional[Any] = None
        self._web_app: Optional[Any] = None
        self._web_runner: Optional[Any] = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Start the webhook server for receiving Zalo events."""
        try:
            import aiohttp
            from aiohttp import web
        except ImportError:
            logger.error("Zalo: aiohttp not installed")
            return False

        self._http_session = aiohttp.ClientSession()

        # Set up webhook server
        app = web.Application()
        app.router.add_post(self._webhook_path, self._handle_webhook)
        app.router.add_get(self._webhook_path, self._handle_webhook_verify)

        self._web_app = app
        self._web_runner = web.AppRunner(app)
        await self._web_runner.setup()
        site = web.TCPSite(self._web_runner, "0.0.0.0", self._webhook_port)
        await site.start()

        logger.info("Zalo: webhook server listening on port %d, path %s",
                    self._webhook_port, self._webhook_path)
        return True

    async def disconnect(self) -> None:
        """Shutdown webhook server and HTTP session."""
        if self._web_runner:
            await self._web_runner.cleanup()
        if self._http_session:
            await self._http_session.close()
        self._http_session = None
        self._web_runner = None
        logger.info("Zalo: disconnected")

    # ------------------------------------------------------------------
    # Webhook handler
    # ------------------------------------------------------------------

    async def _handle_webhook_verify(self, request: "web.Request") -> "web.Response":
        """Handle verification GET requests from Zalo."""
        # Zalo may send a verification challenge
        challenge = request.query.get("hub.challenge", "")
        if challenge:
            return web.Response(text=challenge)
        return web.Response(text="OK")

    async def _handle_webhook(self, request: "web.Request") -> "web.Response":
        """Handle incoming Zalo webhook events."""
        try:
            body = await request.read()
            raw = body.decode("utf-8")

            # Verify signature if secret is set
            if self._secret_key:
                signature = request.headers.get("X-Zalo-Signature", "")
                if not self._verify_signature(raw, signature):
                    logger.warning("Zalo: invalid webhook signature")
                    return web.Response(status=403, text="Invalid signature")

            data = json.loads(raw)
            logger.debug("Zalo: webhook event: %s", json.dumps(data, ensure_ascii=False)[:500])

            # Process event
            await self._process_event(data)

            return web.Response(text='{"status":"ok"}', content_type="application/json")

        except Exception as e:
            logger.error("Zalo: webhook error: %s", e, exc_info=True)
            return web.Response(status=500, text="Internal error")

    def _verify_signature(self, body: str, signature: str) -> bool:
        """Verify HMAC-SHA256 signature of the webhook payload."""
        if not self._secret_key or not signature:
            return True  # Skip if not configured (dev mode)
        expected = hmac.new(
            self._secret_key.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def _process_event(self, data: Dict[str, Any]) -> None:
        """Process a Zalo webhook event."""
        event_name = data.get("event_name", "")

        if event_name == "user_send_text":
            await self._handle_text_message(data)
        elif event_name == "user_send_image":
            await self._handle_image_message(data)
        elif event_name == "follow":
            logger.info("Zalo: user followed OA: %s", data.get("user_id", ""))
        elif event_name == "unfollow":
            logger.info("Zalo: user unfollowed OA: %s", data.get("user_id", ""))
        else:
            logger.debug("Zalo: unhandled event: %s", event_name)

    async def _handle_text_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming text message from Zalo user."""
        from gateway.platforms.base import SessionSource

        user_id = str(data.get("user_id", data.get("sender", {}).get("id", "")))
        message_text = data.get("message", {}).get("text", "")
        message_id = data.get("message_id", f"zalo:{user_id}:{time.time()}")

        if not user_id or not message_text:
            return

        # Get user profile for display name
        display_name = await self._get_user_name(user_id)

        event = MessageEvent(
            text=message_text,
            message_type=MessageType.TEXT,
            source=SessionSource(
                platform=Platform.ZALO,
                chat_id=user_id,
                chat_type="dm",
                user_id=user_id,
                user_name=display_name or user_id,
            ),
            message_id=message_id,
            raw_message=data,
        )

        if self._message_handler:
            await self._message_handler(event)

    async def _handle_image_message(self, data: Dict[str, Any]) -> None:
        """Handle incoming image message from Zalo user."""
        from gateway.platforms.base import SessionSource

        user_id = str(data.get("user_id", ""))
        message_data = data.get("message", {})
        image_url = message_data.get("url", message_data.get("thumbnail", ""))
        message_id = data.get("message_id", f"zalo:{user_id}:{time.time()}")

        if not user_id:
            return

        display_name = await self._get_user_name(user_id)

        event = MessageEvent(
            text="[Image]",
            message_type=MessageType.PHOTO,
            source=SessionSource(
                platform=Platform.ZALO,
                chat_id=user_id,
                chat_type="dm",
                user_id=user_id,
                user_name=display_name or user_id,
            ),
            message_id=message_id,
            media_urls=[image_url] if image_url else [],
            media_types=["image"] if image_url else [],
            raw_message=data,
        )

        if self._message_handler:
            await self._message_handler(event)

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
        """Send a text message to a Zalo user via OA API."""
        if not self._http_session:
            return SendResult(success=False, error="Not connected")

        # Split long messages
        chunks = self._split_text(text)
        last_msg_id: Optional[str] = None

        for chunk in chunks:
            if not chunk.strip():
                continue

            payload = {
                "recipient": {"user_id": chat_id},
                "message": {"text": chunk},
            }

            try:
                headers = {
                    "access_token": self._access_token,
                    "Content-Type": "application/json",
                }
                async with self._http_session.post(
                    ZALO_SEND_MESSAGE_URL,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                    if result.get("error") and result["error"].get("code") != 0:
                        error_msg = result["error"].get("message", "Unknown error")
                        logger.error("Zalo: send error: %s", error_msg)
                        return SendResult(success=False, error=error_msg, raw_response=result)
                    last_msg_id = result.get("data", {}).get("message_id", f"zalo:{chat_id}:{time.time()}")
            except Exception as e:
                logger.error("Zalo: send exception: %s", e)
                return SendResult(success=False, error=str(e), retryable=True)

        return SendResult(success=True, message_id=last_msg_id)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image to a Zalo user."""
        if not self._http_session:
            return SendResult(success=False, error="Not connected")

        payload = {
            "recipient": {"user_id": chat_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": image_url},
                }
            },
        }

        if caption:
            # Zalo doesn't support caption on image, send separately
            pass

        try:
            headers = {
                "access_token": self._access_token,
                "Content-Type": "application/json",
            }
            import aiohttp
            async with self._http_session.post(
                ZALO_SEND_MESSAGE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                msg_id = result.get("data", {}).get("message_id", f"zalo:{chat_id}:{time.time()}")
                success = not (result.get("error") and result["error"].get("code") != 0)
                return SendResult(
                    success=success,
                    message_id=msg_id,
                    raw_response=result,
                    error=result.get("error", {}).get("message") if not success else None,
                )
        except Exception as e:
            return SendResult(success=False, error=str(e), retryable=True)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Zalo OA doesn't support typing indicators."""
        pass

    async def stop_typing(self, chat_id: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_user_name(self, user_id: str) -> Optional[str]:
        """Fetch user display name from Zalo API."""
        if not self._http_session:
            return None

        try:
            headers = {"access_token": self._access_token}
            params = {"user_id": user_id}
            import aiohttp
            async with self._http_session.get(
                ZALO_GET_USER_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                return data.get("data", {}).get("display_name") or data.get("data", {}).get("name")
        except Exception as e:
            logger.debug("Zalo: get user name failed: %s", e)
            return None

    def _split_text(self, text: str) -> List[str]:
        """Split text into Zalo-safe chunks."""
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
                chunks.append(paragraph[:break_at].rstrip())
                paragraph = paragraph[break_at:].lstrip()
            if paragraph.strip():
                chunks.append(paragraph)
        return chunks

    @property
    def enforces_own_access_policy(self) -> bool:
        return True

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        """Send a voice message via Zalo OA API.

        Zalo OA supports sending voice messages via the \"send voice\" API.
        Requires uploading the audio file first, then sending with attachment.
        """
        if not self._connected:
            return SendResult(success=False, error="Not connected")
        # Zalo OA voice API requires file upload to their server first.
        # Fall back to text with audio path for now.
        text = f"🔊 Voice: {audio_path}"
        if caption:
            text = f"{caption}\n{text}"
        return await self.send(chat_id=chat_id, content=text, reply_to=reply_to, metadata=metadata)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get info about a Zalo user chat."""
        name = await self._get_user_name(chat_id)
        return {"name": name or chat_id, "type": "dm"}
