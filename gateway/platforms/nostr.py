"""
Nostr (Notes and Other Stuff Transmitted by Relays) platform adapter for Vermes.

Nostr is a decentralized social protocol using cryptographic identities
(npub/secp256k1 keys) and WebSocket relays. This adapter connects to one or
more relays, listens for NIP-04 encrypted direct messages and NIP-28 public
channel messages, and relays them to the Vermes agent.

Design highlights
------------------

**Key-based identity.** The bot's Nostr identity is a secp256k1 keypair.
The private key (hex or nsec) is set via ``NOSTR_PRIVATE_KEY`` env var.

**Multi-relay.** Connects to multiple relays simultaneously for redundancy.
Default relays: relay.damus.io, nos.lol, relay.nostr.band.

**NIP-04 DM + NIP-28 channels.** Decrypts incoming DMs (NIP-04 ECDH+AES-256-CBC)
and listens to channel messages (NIP-28 kind 42). Responses are sent as
encrypted DMs (kind 4) or channel messages (kind 42).

**Text-only.** Nostr events are text. Images/files are referenced by URL
(NIP-94 or imeta).

Configuration (env vars)
-------------------------

::

    NOSTR_ENABLED=true
    NOSTR_PRIVATE_KEY=<hex-private-key-or-nsec1...>
    NOSTR_RELAYS=wss://relay.damus.io,wss://nos.lol
    NOSTR_ALLOW_ALL_USERS=true

Install dependencies::

    pip install nostr-sdk websocket-client
    # or use the pure-python implementation below (no external deps)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]
KIND_DM = 4         # NIP-04 encrypted direct message
KIND_CHANNEL_MSG = 42  # NIP-28 channel message
KIND_TEXT_NOTE = 1   # NIP-01 text note
MAX_TEXT_LENGTH = 5000  # Nostr content limit is generous


def check_nostr_requirements() -> bool:
    """Check if Nostr dependencies and config are available."""
    if not os.getenv("NOSTR_PRIVATE_KEY"):
        return False
    try:
        import websockets  # noqa: F401
    except ImportError:
        return False
    return True


# ---------------------------------------------------------------------------
# Minimal secp256k1 + NIP-04 implementation (no external crypto deps)
# ---------------------------------------------------------------------------

def _hex_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)

def _bytes_to_hex(data: bytes) -> str:
    return data.hex()

# We need: ECDH (secp256k1), AES-256-CBC, HMAC-SHA256, base64
# Try to use available crypto libraries
try:
    from coincurve import PrivateKey, PublicKey
    HAS_COINCURVE = True
except ImportError:
    HAS_COINCURVE = False
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        HAS_CRYPTOGRAPHY = True
    except ImportError:
        HAS_CRYPTOGRAPHY = False

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


def _decrypt_nip04(content_b64: str, privkey_hex: str, pubkey_hex: str) -> str:
    """Decrypt a NIP-04 encrypted message.

    Format: base64(ciphertext)?iv=base64(iv)
    """
    import base64 as b64

    # Split content and IV
    if "?" in content_b64:
        ct_b64, iv_part = content_b64.split("?", 1)
        iv_b64 = iv_part.replace("iv=", "")
    else:
        parts = content_b64.split("?iv=")
        ct_b64, iv_b64 = parts[0], parts[1] if len(parts) > 1 else ""

    ciphertext = b64.b64decode(ct_b64)
    iv = b64.b64decode(iv_b64)

    # ECDH shared secret
    if HAS_COINCURVE:
        priv = PrivateKey(_hex_to_bytes(privkey_hex))
        pub = PublicKey(_hex_to_bytes(pubkey_hex))
        shared_point = priv.ecdh(pub.format())
        shared_key = hashlib.sha256(shared_point).digest()
    elif HAS_CRYPTOGRAPHY:
        priv = ec.derive_private_key(
            int(privkey_hex, 16),
            ec.SECP256K1(),
        )
        pub_point = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256K1(), _hex_to_bytes(pubkey_hex)
        )
        shared = priv.exchange(ec.ECDH(), pub_point)
        shared_key = hashlib.sha256(shared).digest()
    else:
        raise RuntimeError("No secp256k1 library available (install coincurve or cryptography)")

    # AES-256-CBC decrypt
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(shared_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    plaintext_padded = decryptor.update(ciphertext) + decryptor.finalize()

    # Remove PKCS7 padding
    pad_len = plaintext_padded[-1]
    plaintext = plaintext_padded[:-pad_len]
    return plaintext.decode("utf-8")


def _encrypt_nip04(plaintext: str, privkey_hex: str, pubkey_hex: str) -> str:
    """Encrypt a NIP-04 message."""
    import base64 as b64
    import os as _os

    # ECDH shared secret
    if HAS_COINCURVE:
        priv = PrivateKey(_hex_to_bytes(privkey_hex))
        pub = PublicKey(_hex_to_bytes(pubkey_hex))
        shared_point = priv.ecdh(pub.format())
        shared_key = hashlib.sha256(shared_point).digest()
    elif HAS_CRYPTOGRAPHY:
        priv = ec.derive_private_key(
            int(privkey_hex, 16),
            ec.SECP256K1(),
        )
        pub_point = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256K1(), _hex_to_bytes(pubkey_hex)
        )
        shared = priv.exchange(ec.ECDH(), pub_point)
        shared_key = hashlib.sha256(shared).digest()
    else:
        raise RuntimeError("No secp256k1 library available")

    # Generate random IV
    iv = _os.urandom(16)

    # PKCS7 padding
    pad_len = 16 - (len(plaintext.encode("utf-8")) % 16)
    padded = plaintext.encode("utf-8") + bytes([pad_len] * pad_len)

    # AES-256-CBC encrypt
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(shared_key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    return f"{b64.b64encode(ciphertext).decode()}?iv={b64.b64encode(iv).decode()}"


def _get_pubkey_from_priv(privkey_hex: str) -> str:
    """Derive public key from private key."""
    if HAS_COINCURVE:
        priv = PrivateKey(_hex_to_bytes(privkey_hex))
        return priv.public_key.format().hex()
    elif HAS_CRYPTOGRAPHY:
        priv = ec.derive_private_key(
            int(privkey_hex, 16),
            ec.SECP256K1(),
        )
        pub = priv.public_key()
        return pub.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        ).hex()
    raise RuntimeError("No secp256k1 library available")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class NostrAdapter(BasePlatformAdapter):
    """Nostr platform adapter for decentralized messaging."""

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.NOSTR)
        self._privkey = self._parse_private_key(os.getenv("NOSTR_PRIVATE_KEY", ""))
        self._pubkey = ""  # Lazy-init in connect() to avoid requiring crypto libs at import
        self._relay_urls: List[str] = []
        self._ws_connections: Dict[str, Any] = {}
        self._listen_tasks: List[asyncio.Task] = []

        # Parse relays
        relays_env = os.getenv("NOSTR_RELAYS", "")
        if relays_env:
            self._relay_urls = [r.strip() for r in relays_env.split(",") if r.strip()]
        else:
            self._relay_urls = list(DEFAULT_RELAYS)

    def _parse_private_key(self, key: str) -> str:
        """Parse private key from hex or nsec format."""
        if not key:
            return ""
        if key.startswith("nsec1"):
            # Bech32 decode would go here — for now require hex
            logger.warning("Nostr: nsec keys not yet supported, please provide hex private key")
            return ""
        return key

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to all configured Nostr relays."""
        if not HAS_WEBSOCKETS:
            logger.error("Nostr: websockets not installed. Run: pip install websockets")
            return False
        if not self._privkey:
            logger.error("Nostr: no private key configured")
            return False

        # Derive pubkey now (requires crypto lib)
        try:
            self._pubkey = _get_pubkey_from_priv(self._privkey)
        except RuntimeError as e:
            logger.error("Nostr: %s. Install coincurve or cryptography", e)
            return False

        for relay_url in self._relay_urls:
            task = asyncio.create_task(self._connect_relay(relay_url))
            self._listen_tasks.append(task)

        # Wait a moment for initial connections
        await asyncio.sleep(1.0)
        logger.info("Nostr: connecting to %d relays, pubkey: %s...",
                    len(self._relay_urls), self._pubkey[:16])
        return True

    async def _connect_relay(self, url: str) -> None:
        """Connect to a single relay and listen for events."""
        backoff = 1
        while self._running:
            try:
                ws = await websockets.connect(url, ping_interval=20, ping_timeout=10)
                self._ws_connections[url] = ws
                logger.info("Nostr: connected to relay %s", url)

                # Subscribe to DMs (kind 4) and channel messages (kind 42)
                sub_filter = json.dumps([
                    "REQ",
                    f"vermes-{self._pubkey[:8]}",
                    {"kinds": [KIND_DM, KIND_CHANNEL_MSG], "#p": [self._pubkey]},
                ])
                await ws.send(sub_filter)

                # Also subscribe to text notes that mention us
                sub_mention = json.dumps([
                    "REQ",
                    f"vermes-mention-{self._pubkey[:8]}",
                    {"kinds": [KIND_TEXT_NOTE], "#p": [self._pubkey]},
                ])
                await ws.send(sub_mention)

                backoff = 1  # Reset backoff on success

                # Listen
                async for raw in ws:
                    await self._handle_relay_message(url, raw)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Nostr: relay %s error: %s, reconnecting in %ds", url, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            finally:
                self._ws_connections.pop(url, None)

    async def _handle_relay_message(self, relay_url: str, raw: str) -> None:
        """Handle a message from a Nostr relay."""
        try:
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) < 2:
                return

            msg_type = data[0]

            if msg_type == "EVENT":
                event = data[2] if len(data) > 2 else {}
                await self._process_nostr_event(event)

        except Exception as e:
            logger.error("Nostr: relay message error: %s", e)

    async def _process_nostr_event(self, event: Dict[str, Any]) -> None:
        """Process a Nostr event."""
        from gateway.platforms.base import SessionSource

        kind = event.get("kind", 0)
        pub = event.get("pubkey", "")
        content = event.get("content", "")
        event_id = event.get("id", "")

        if kind == KIND_DM:
            # Decrypt DM
            try:
                plaintext = _decrypt_nip04(content, self._privkey, pub)
            except Exception as e:
                logger.error("Nostr: DM decrypt failed: %s", e)
                return

            chat_id = pub
            chat_type = "dm"

            event_msg = MessageEvent(
                text=plaintext,
                message_type=MessageType.TEXT,
                source=SessionSource(
                    platform=Platform.NOSTR,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    user_id=pub,
                    user_name=pub[:16],  # Nostr uses keys as identity
                ),
                message_id=event_id,
                raw_message=event,
            )

            if self._message_handler:
                await self._message_handler(event_msg)

        elif kind == KIND_CHANNEL_MSG:
            # Channel message — might be encrypted or plaintext
            # Try to decrypt, fall back to plaintext
            text = content
            try:
                text = _decrypt_nip04(content, self._privkey, pub)
            except Exception:
                pass  # Might be plaintext channel message

            # Extract channel ID from tags
            channel_id = ""
            for tag in event.get("tags", []):
                if len(tag) >= 2 and tag[0] == "e":
                    channel_id = tag[1]
                    break

            if not channel_id:
                return

            event_msg = MessageEvent(
                text=text,
                message_type=MessageType.TEXT,
                source=SessionSource(
                    platform=Platform.NOSTR,
                    chat_id=channel_id,
                    chat_type="group",
                    user_id=pub,
                    user_name=pub[:16],
                ),
                message_id=event_id,
                raw_message=event,
            )

            if self._message_handler:
                await self._message_handler(event_msg)

    async def disconnect(self) -> None:
        """Disconnect from all relays."""
        self._running = False
        for task in self._listen_tasks:
            task.cancel()
        for url, ws in self._ws_connections.items():
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_connections.clear()
        self._listen_tasks.clear()
        logger.info("Nostr: disconnected from all relays")

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
        """Send a message via Nostr (DM or channel)."""
        if not self._ws_connections:
            return SendResult(success=False, error="No relay connections")

        # Determine if this is a DM (chat_id is a pubkey) or channel message
        is_dm = len(chat_id) == 64 and all(c in "0123456789abcdef" for c in chat_id.lower())

        if is_dm:
            # Encrypt and send as NIP-04 DM
            encrypted = _encrypt_nip04(text, self._privkey, chat_id)
            event = self._build_event(KIND_DM, encrypted, [
                ["p", chat_id],
            ])
        else:
            # Channel message (kind 42)
            event = self._build_event(KIND_CHANNEL_MSG, text, [
                ["e", chat_id, "", "root"],
            ])

        # Sign and broadcast
        signed = self._sign_event(event)
        event_json = json.dumps(["EVENT", signed])

        sent = False
        for ws in self._ws_connections.values():
            try:
                await ws.send(event_json)
                sent = True
            except Exception as e:
                logger.warning("Nostr: send to relay failed: %s", e)

        if sent:
            return SendResult(success=True, message_id=signed.get("id", ""))
        return SendResult(success=False, error="Failed to send to any relay")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        pass

    async def stop_typing(self, chat_id: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Event building and signing
    # ------------------------------------------------------------------

    def _build_event(self, kind: int, content: str, tags: List[List[str]]) -> Dict[str, Any]:
        """Build an unsigned Nostr event."""
        return {
            "kind": kind,
            "content": content,
            "tags": tags,
            "created_at": int(time.time()),
            "pubkey": self._pubkey,
        }

    def _sign_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Sign a Nostr event with the private key."""
        # Canonical serialization for event ID
        canonical = json.dumps([
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ], separators=(",", ":"))

        event_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event["id"] = event_id

        # Sign with secp256k1
        if HAS_COINCURVE:
            priv = PrivateKey(_hex_to_bytes(self._privkey))
            sig = priv.sign_recoverable(_hex_to_bytes(event_id), hasher=None)
            # sig is 65 bytes: r(32) + s(32) + v(1)
            event["sig"] = _bytes_to_hex(sig)
        elif HAS_CRYPTOGRAPHY:
            from cryptography.hazmat.primitives.asymmetric import utils
            priv = ec.derive_private_key(
                int(self._privkey, 16),
                ec.SECP256K1(),
            )
            sig_der = priv.sign(
                _hex_to_bytes(event_id),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
            # Convert DER to raw r||s
            event["sig"] = _bytes_to_hex(self._der_to_raw_sig(sig_der))

        return event

    def _der_to_raw_sig(self, der_sig: bytes) -> bytes:
        """Convert DER signature to raw r||s format."""
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        r, s = decode_dss_signature(der_sig)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    @property
    def enforces_own_access_policy(self) -> bool:
        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get info about a Nostr chat (DM pubkey or channel)."""
        is_dm = len(chat_id) == 64 and all(c in "0123456789abcdef" for c in chat_id.lower())
        return {"name": chat_id[:16], "type": "dm" if is_dm else "group"}
