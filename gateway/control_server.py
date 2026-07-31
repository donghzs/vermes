"""Gateway Control Server (P3)

A lightweight aiohttp server bound to 127.0.0.1 that exposes runtime
channel hot-plug control endpoints.  This is the "fast path" that
``save_channel`` (P4) uses to notify the running gateway immediately
after a config change, instead of waiting for the 3s config-watch poll.

Endpoints:
    POST /control/channels/{platform}/connect
    POST /control/channels/{platform}/disconnect
    POST /control/channels/{platform}/reload
    GET  /control/health

Security:
    - Binds to 127.0.0.1 ONLY (never 0.0.0.0)
    - Shared-secret token stored in ~/.vermes/.gateway_control.json
    - Token generated at gateway startup, written to file with mode 0600
    - Same trust boundary as shared state.db / config.yaml
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from aiohttp import web

if TYPE_CHECKING:
    from gateway.lifecycle_mixin import LifecycleMixin

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9120
CONTROL_TOKEN_FILE = ".gateway_control.json"


def _get_vermes_home() -> Path:
    """Return the Vermes home directory (~/.vermes)."""
    try:
        from vermes_cli.config import get_vermes_home
        return get_vermes_home()
    except Exception:
        return Path.home() / ".vermes"


def _write_control_token(port: int) -> str:
    """Generate a random token and write the control config file.

    Returns the token string.  The file is written with mode 0600.
    """
    token = secrets.token_hex(32)
    home = _get_vermes_home()
    home.mkdir(parents=True, exist_ok=True)
    token_file = home / CONTROL_TOKEN_FILE
    payload = {"port": port, "token": token}
    token_file.write_text(json.dumps(payload))
    try:
        token_file.chmod(0o600)
    except OSError:
        pass  # Windows doesn't support chmod
    return token


def _read_control_token() -> Optional[dict]:
    """Read the control config file.  Returns None if not found."""
    try:
        token_file = _get_vermes_home() / CONTROL_TOKEN_FILE
        if not token_file.exists():
            return None
        return json.loads(token_file.read_text())
    except Exception as e:
        logger.debug("Failed to read control token: %s", e)
        return None


def _remove_control_token() -> None:
    """Remove the control token file (called on shutdown)."""
    try:
        token_file = _get_vermes_home() / CONTROL_TOKEN_FILE
        token_file.unlink(missing_ok=True)
    except Exception:
        pass


class ControlServer:
    """Runs the gateway control HTTP server on 127.0.0.1.

    Created and started by LifecycleMixin during gateway startup (when
    hot-plug is enabled).  Stopped during gateway shutdown.
    """

    def __init__(self, gateway: "LifecycleMixin", port: int = DEFAULT_PORT):
        self._gateway = gateway
        self._port = port
        self._token: str = ""
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    def _check_auth(self, request: web.Request) -> bool:
        """Validate the Bearer token in the request header."""
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return auth[7:] == self._token

    def _unauthorized(self) -> web.Response:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

    async def _handle_connect(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return self._unauthorized()
        platform_name = request.match_info["platform"]
        try:
            from gateway.config import Platform
            platform = Platform(platform_name)
        except (ValueError, KeyError):
            return web.json_response(
                {"ok": False, "error": f"unknown platform: {platform_name}"}
            )
        result = await self._gateway._connect_one(platform)
        return web.json_response(result)

    async def _handle_disconnect(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return self._unauthorized()
        platform_name = request.match_info["platform"]
        try:
            from gateway.config import Platform
            platform = Platform(platform_name)
        except (ValueError, KeyError):
            return web.json_response(
                {"ok": False, "error": f"unknown platform: {platform_name}"}
            )
        result = await self._gateway._disconnect_one(platform)
        return web.json_response(result)

    async def _handle_reload(self, request: web.Request) -> web.Response:
        """Reload = disconnect + reconnect (force=True)."""
        if not self._check_auth(request):
            return self._unauthorized()
        platform_name = request.match_info["platform"]
        try:
            from gateway.config import Platform
            platform = Platform(platform_name)
        except (ValueError, KeyError):
            return web.json_response(
                {"ok": False, "error": f"unknown platform: {platform_name}"}
            )
        result = await self._gateway._connect_one(platform, force=True)
        return web.json_response(result)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health endpoint — no auth required (only bound to 127.0.0.1)."""
        return web.json_response({
            "ok": True,
            "adapters": [p.value for p in self._gateway.adapters.keys()],
        })

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(
            "/control/channels/{platform}/connect", self._handle_connect
        )
        app.router.add_post(
            "/control/channels/{platform}/disconnect", self._handle_disconnect
        )
        app.router.add_post(
            "/control/channels/{platform}/reload", self._handle_reload
        )
        app.router.add_get("/control/health", self._handle_health)
        return app

    async def start(self) -> bool:
        """Start the control server.  Returns True on success."""
        self._token = _write_control_token(self._port)
        app = self._build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        try:
            await self._site.start()
            logger.info(
                "Control server listening on 127.0.0.1:%d (hot-plug fast path)",
                self._port,
            )
            return True
        except OSError as e:
            if e.errno in (48, 98):  # EADDRINUSE
                logger.warning(
                    "Control server port %d in use, hot-plug fast path disabled "
                    "(config-watch will still work)",
                    self._port,
                )
                await self._runner.cleanup()
                self._runner = None
                self._site = None
                _remove_control_token()
                return False
            raise

    async def stop(self) -> None:
        """Stop the control server and clean up."""
        if self._site:
            await self._site.stop()
            self._site = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        _remove_control_token()
        logger.info("Control server stopped")
