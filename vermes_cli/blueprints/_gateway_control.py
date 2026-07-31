"""Gateway Control Client (P3/P4)

Client-side helper for talking to the gateway control server (P3).
Used by ``save_channel`` (P4) to notify the running gateway immediately
after a config change, instead of waiting for the 3s config-watch poll.

If the control server is unreachable (e.g. Web mode without a running
gateway), calls degrade gracefully — the config-watch watcher will pick
up the change within 3 seconds.
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CONTROL_TIMEOUT = 5.0  # seconds


def _read_control_config() -> Optional[dict]:
    """Read the gateway control config file (~/.vermes/.gateway_control.json).

    Returns ``{"port": int, "token": str}`` or ``None`` if not found.
    """
    try:
        from vermes_cli.config import get_vermes_home
        token_file = get_vermes_home() / ".gateway_control.json"
        if not token_file.exists():
            return None
        import json
        return json.loads(token_file.read_text())
    except Exception as e:
        logger.debug("Failed to read gateway control config: %s", e)
        return None


async def reload_channel(platform: str) -> dict:
    """POST /control/channels/{platform}/reload to the gateway.

    Returns the gateway's JSON response, or a degradation dict on failure.
    Never raises — callers can use this as a fire-and-forget fast path.
    """
    cfg = _read_control_config()
    if cfg is None:
        return {
            "ok": True,
            "note": "config saved; gateway will connect shortly via config-watch",
        }

    port = cfg.get("port", 9120)
    token = cfg.get("token", "")
    url = f"http://127.0.0.1:{port}/control/channels/{platform}/reload"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONTROL_TIMEOUT),
            ) as resp:
                return await resp.json()
    except Exception as e:
        logger.debug("Gateway control request failed: %s", e)
        return {
            "ok": True,
            "note": "config saved; gateway will connect shortly via config-watch",
        }


async def connect_channel(platform: str) -> dict:
    """POST /control/channels/{platform}/connect to the gateway."""
    cfg = _read_control_config()
    if cfg is None:
        return {"ok": True, "note": "gateway not reachable; config-watch will handle it"}

    port = cfg.get("port", 9120)
    token = cfg.get("token", "")
    url = f"http://127.0.0.1:{port}/control/channels/{platform}/connect"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONTROL_TIMEOUT),
            ) as resp:
                return await resp.json()
    except Exception as e:
        logger.debug("Gateway control request failed: %s", e)
        return {"ok": True, "note": "gateway not reachable; config-watch will handle it"}


async def disconnect_channel(platform: str) -> dict:
    """POST /control/channels/{platform}/disconnect to the gateway."""
    cfg = _read_control_config()
    if cfg is None:
        return {"ok": True, "note": "gateway not reachable; config-watch will handle it"}

    port = cfg.get("port", 9120)
    token = cfg.get("token", "")
    url = f"http://127.0.0.1:{port}/control/channels/{platform}/disconnect"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=CONTROL_TIMEOUT),
            ) as resp:
                return await resp.json()
    except Exception as e:
        logger.debug("Gateway control request failed: %s", e)
        return {"ok": True, "note": "gateway not reachable; config-watch will handle it"}
