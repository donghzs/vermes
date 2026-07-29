"""Blueprint: Status（服务器状态、Gateway 管理、日志、停止生成）

Endpoints:
- GET  /api/status              — 服务器状态概览
- POST /api/gateway/restart     — 重启 Gateway
- POST /api/hermes/update       — 触发 Vermes 自更新
- GET  /api/actions/{name}/status — 查询后台任务状态
- POST /api/shutdown            — 关闭后端服务器
- POST /api/stop-generation     — 停止正在生成的流
- GET  /api/logs                — 日志查看
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request

from vermes_cli import __version__, __release_date__
from vermes_cli.config import (
    check_config_version,
    get_config_path,
    get_env_path,
    get_hermes_home,
)

_log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# ---------------------------------------------------------------------------
# Gateway health env vars (deprecated, see original web_server.py comments)
# ---------------------------------------------------------------------------
_GATEWAY_HEALTH_URL = os.getenv("GATEWAY_HEALTH_URL")
try:
    _GATEWAY_HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "3"))
except (ValueError, TypeError):
    _log.warning(
        "Invalid GATEWAY_HEALTH_TIMEOUT value %r — using default 3.0s",
        os.getenv("GATEWAY_HEALTH_TIMEOUT"),
    )
    _GATEWAY_HEALTH_TIMEOUT = 3.0


# ---------------------------------------------------------------------------
# Gateway helpers
# ---------------------------------------------------------------------------

def _get_gateway_pid():
    try:
        from gateway.status import get_running_pid
        return get_running_pid()
    except ImportError:
        return None


def _read_gateway_status():
    try:
        from gateway.status import read_runtime_status
        return read_runtime_status()
    except ImportError:
        return None


def _probe_gateway_health() -> tuple[bool, dict | None]:
    """Probe the gateway via its HTTP health endpoint (cross-container)."""
    if not _GATEWAY_HEALTH_URL:
        return False, None

    base = _GATEWAY_HEALTH_URL.rstrip("/")
    if base.endswith("/health/detailed"):
        base = base[: -len("/health/detailed")]
    elif base.endswith("/health"):
        base = base[: -len("/health")]

    for path in (f"{base}/health/detailed", f"{base}/health"):
        try:
            req = urllib.request.Request(path, method="GET")
            with urllib.request.urlopen(req, timeout=_GATEWAY_HEALTH_TIMEOUT) as resp:
                if resp.status == 200:
                    body = json.loads(resp.read())
                    return True, body
        except Exception:
            continue
    return False, None


# ---------------------------------------------------------------------------
# Action spawning (gateway restart, vermes update)
# ---------------------------------------------------------------------------

_ACTION_LOG_DIR: Path = get_hermes_home() / "logs"

_ACTION_LOG_FILES: Dict[str, str] = {
    "gateway-restart": "gateway-restart.log",
    "hermes-update": "hermes-update.log",
}

_ACTION_PROCS: Dict[str, subprocess.Popen] = {}


def _spawn_vermes_action(subcommand: List[str], name: str) -> subprocess.Popen:
    """Spawn ``hermes <subcommand>`` detached and record the Popen handle."""
    log_file_name = _ACTION_LOG_FILES[name]
    _ACTION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _ACTION_LOG_DIR / log_file_name
    log_file = open(log_path, "ab", buffering=0)
    log_file.write(
        f"\n=== {name} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode()
    )

    cmd = [sys.executable, "-m", "vermes_cli.main", *subcommand]

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "HERMES_NONINTERACTIVE": "1"},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    _ACTION_PROCS[name] = proc
    return proc


def _tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path``."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def get_status():
    current_ver, latest_ver = check_config_version()

    gateway_pid = _get_gateway_pid()
    gateway_running = gateway_pid is not None
    remote_health_body: dict | None = None

    if not gateway_running and _GATEWAY_HEALTH_URL:
        loop = asyncio.get_running_loop()
        alive, remote_health_body = await loop.run_in_executor(
            None, _probe_gateway_health
        )
        if alive:
            gateway_running = True
            if remote_health_body:
                gateway_pid = remote_health_body.get("pid")

    gateway_state = None
    gateway_platforms: dict = {}
    gateway_exit_reason = None
    gateway_updated_at = None
    configured_gateway_platforms: set[str] | None = None
    try:
        from gateway.config import load_gateway_config

        gateway_config = load_gateway_config()
        configured_gateway_platforms = {
            platform.value for platform in gateway_config.get_connected_platforms()
        }
    except Exception:
        configured_gateway_platforms = None

    runtime = _read_gateway_status()
    if runtime is None and remote_health_body and remote_health_body.get("gateway_state"):
        runtime = remote_health_body

    if runtime:
        gateway_state = runtime.get("gateway_state")
        gateway_platforms = runtime.get("platforms") or {}
        if configured_gateway_platforms is not None:
            gateway_platforms = {
                key: value
                for key, value in gateway_platforms.items()
                if key in configured_gateway_platforms
            }
        gateway_exit_reason = runtime.get("exit_reason")
        gateway_updated_at = runtime.get("updated_at")
        if not gateway_running:
            gateway_state = gateway_state if gateway_state in {"stopped", "startup_failed"} else "stopped"
            gateway_platforms = {}
        elif gateway_running and remote_health_body is not None:
            if gateway_state in {None, "stopped"}:
                gateway_state = "running"

    if gateway_running and gateway_state is None and remote_health_body is not None:
        gateway_state = "running"

    active_sessions = 0
    try:
        from vermes_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=50)
            now = time.time()
            active_sessions = sum(
                1 for s in sessions
                if s.get("ended_at") is None
                and (now - s.get("last_active", s.get("started_at", 0))) < 300
            )
        finally:
            db.close()
    except Exception:
        pass

    return {
        "version": __version__,
        "release_date": __release_date__,
        "hermes_home": str(get_hermes_home()),
        "config_path": str(get_config_path()),
        "env_path": str(get_env_path()),
        "config_version": current_ver,
        "latest_config_version": latest_ver,
        "gateway_running": gateway_running,
        "gateway_pid": gateway_pid,
        "gateway_health_url": _GATEWAY_HEALTH_URL,
        "gateway_state": gateway_state,
        "gateway_platforms": gateway_platforms,
        "gateway_exit_reason": gateway_exit_reason,
        "gateway_updated_at": gateway_updated_at,
        "active_sessions": active_sessions,
    }


async def restart_gateway():
    """Kick off a ``vermes gateway restart`` in the background."""
    try:
        proc = _spawn_vermes_action(["gateway", "restart"], "gateway-restart")
    except Exception as exc:
        _log.exception("Failed to spawn gateway restart")
        raise HTTPException(status_code=500, detail=f"Failed to restart gateway: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "gateway-restart",
    }


async def update_hermes():
    """Kick off ``vermes update`` in the background."""
    try:
        proc = _spawn_vermes_action(["update"], "vermes-update")
    except Exception as exc:
        _log.exception("Failed to spawn vermes update")
        raise HTTPException(status_code=500, detail=f"Failed to start update: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "hermes-update",
    }


async def get_action_status(name: str, lines: int = 200):
    """Tail an action log and report whether the process is still running."""
    log_file_name = _ACTION_LOG_FILES.get(name)
    if log_file_name is None:
        raise HTTPException(status_code=404, detail=f"Unknown action: {name}")

    log_path = _ACTION_LOG_DIR / log_file_name
    tail = _tail_lines(log_path, min(max(lines, 1), 2000))

    proc = _ACTION_PROCS.get(name)
    if proc is None:
        running = False
        exit_code: Optional[int] = None
        pid: Optional[int] = None
    else:
        exit_code = proc.poll()
        running = exit_code is None
        pid = proc.pid

    return {
        "name": name,
        "running": running,
        "exit_code": exit_code,
        "pid": pid,
        "lines": tail,
    }


async def shutdown_server():
    """关闭后端服务器（前端关闭标签页时调用）。"""
    try:
        from vermes_cli.shutdown_signal import shutdown_event
        shutdown_event.set()
        return {"ok": True}
    except Exception:
        return {"ok": False}


async def stop_generation(request: Request):
    """Stop a running stream. Frontend calls this when user clicks stop."""
    from vermes_cli.blueprints.state import _active_streams
    from vermes_cli.web_server import _require_token

    _require_token(request)
    body = await request.json()
    stream_id = body.get("stream_id")
    if not stream_id:
        return {"ok": False, "message": "stream_id required"}
    cancel_event = _active_streams.get(stream_id)
    if cancel_event is None:
        return {"ok": False, "message": "stream not found or already finished"}
    cancel_event.set()
    _log.info(f"[Stream] Stop requested for stream {stream_id}")
    return {"ok": True, "stream_id": stream_id}


async def get_logs(
    file: str = "agent",
    lines: int = 100,
    level: Optional[str] = None,
    component: Optional[str] = None,
    search: Optional[str] = None,
):
    from vermes_cli.logs import _read_tail, LOG_FILES

    log_name = LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"Unknown log file: {file}")
    log_path = get_hermes_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from vermes_logging import COMPONENT_PREFIXES
    except ImportError:
        COMPONENT_PREFIXES = {}

    min_level = level if level and level.upper() != "ALL" else None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown component: {component}. "
                       f"Available: {', '.join(sorted(COMPONENT_PREFIXES))}",
            )
    else:
        comp_prefixes = None

    has_filters = bool(min_level or comp_prefixes or search)
    result = _read_tail(
        log_path, min(lines, 500) if not search else 2000,
        has_filters=has_filters,
        min_level=min_level,
        component_prefixes=comp_prefixes,
    )
    if search:
        needle = search.lower()
        result = [l for l in result if needle in l.lower()][-min(lines, 500):]
    return {"file": file, "lines": result}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_to(app):
    """Register status/gateway/logs/stop-generation routes on the FastAPI app."""
    app.add_api_route("/api/status", get_status, methods=["GET"], name="get_status")
    app.add_api_route(
        "/api/gateway/restart",
        restart_gateway,
        methods=["POST"],
        name="restart_gateway",
    )
    app.add_api_route(
        "/api/hermes/update",
        update_hermes,
        methods=["POST"],
        name="update_hermes",
    )
    app.add_api_route(
        "/api/actions/{name}/status",
        get_action_status,
        methods=["GET"],
        name="get_action_status",
    )
    app.add_api_route(
        "/api/shutdown",
        shutdown_server,
        methods=["POST"],
        name="shutdown_server",
    )
    app.add_api_route(
        "/api/stop-generation",
        stop_generation,
        methods=["POST"],
        name="stop_generation",
    )
    app.add_api_route(
        "/api/logs",
        get_logs,
        methods=["GET"],
        name="get_logs",
    )
