"""
Vermes — Web UI server.

Provides a FastAPI backend serving the Vite/React frontend and REST API
endpoints for managing configuration, environment variables, and sessions.

Usage:
    python -m vermes_cli.main web          # Start on http://127.0.0.1:9119
    python -m vermes_cli.main web --port 8080
"""

import asyncio
import hmac
import importlib.util
import json
import logging

logger = logging.getLogger(__name__)
import os
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vermes_cli import __version__
import vermes_cli.vermes_log  # noqa: F401 — 安装 stdout 双写 + logging 文件输出
from vermes_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    get_config_path,
    get_vermes_home,
    load_config,
    load_env,
    save_config,
    save_env_value,
    remove_env_value,
    redact_key,
)
try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError:
    # First try lazy-installing the dashboard extras. Only the user actually
    # running `Vermes dashboard` needs fastapi+uvicorn; lazy install keeps
    # them out of every other install path. After install, re-import.
    try:
        from tools.lazy_deps import ensure as _lazy_ensure
        _lazy_ensure("tool.dashboard", prompt=False)
        from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except Exception:
        raise SystemExit(
            "Web UI requires fastapi and uvicorn.\n"
            f"Install with: {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'"
        )

if "VERMES_WEB_DIST" in os.environ:
    WEB_DIST = Path(os.environ["VERMES_WEB_DIST"])
elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # PyInstaller COLLECT/onedir: data dest='vermes_cli/web_dist' → {MEIPASS}/vermes_cli/web_dist
    WEB_DIST = Path(sys._MEIPASS) / "vermes_cli" / "web_dist"
else:
    WEB_DIST = Path(__file__).parent / "web_dist"
_log = logging.getLogger(__name__)

# ── G1 启动完整性哨兵（docs/design-startup-integrity-guards-final.md）──
# 必须在任何 SessionDB() 实例化之前跑完（审计修正 C：SessionDB.__init__ 缺库
# 即静默建空库，会把 missing_with_profile 洗白成 0 字节新库）。此处位于所有
# blueprint 注册之前，是本进程最早的可注入点。probe 自身用 mode=ro 只读连接，
# 零磁盘写入。判坏 → vermes_state 进入 lockdown（方案 a：SessionDB raise 而非
# 新建），后端保持存活仅服务 /health 与静态资源。
try:
    import vermes_state as _vermes_state_probe
    _startup_integrity = _vermes_state_probe.startup_integrity_probe()
    if _startup_integrity.get("state_db") in ("corrupt", "missing_with_profile"):
        _log.error(
            "[G1] state.db integrity verdict=%s path=%s detail=%s — "
            "write path LOCKED DOWN (plan a); no data has been modified",
            _startup_integrity.get("state_db"),
            _startup_integrity.get("db_path"),
            _startup_integrity.get("detail"),
        )
    else:
        _log.info(
            "[G1] state.db integrity verdict=%s path=%s",
            _startup_integrity.get("state_db"),
            _startup_integrity.get("db_path"),
        )
except Exception as _probe_exc:  # probe 自身失败绝不阻断启动（fail-open 仅限探测本身）
    _startup_integrity = {"state_db": "probe_error", "detail": str(_probe_exc)}
    _log.warning("[G1] integrity probe failed (non-fatal): %s", _probe_exc)

app = FastAPI(title="Vermes", version=__version__)


# ---------------------------------------------------------------------------
# Global exception handler — catch unhandled errors to prevent backend crash
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions and return 500 without leaking internals.

    Exception details are logged server-side only. The client receives a
    generic message — no type names, stack traces, or API key fragments
    reach the external response.
    """
    import traceback
    _log.error(
        "[Vermes] Unhandled exception on %s %s: %s: %s",
        request.method, request.url.path, type(exc).__name__, exc,
    )
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": "Internal server error",
                "type": "server_error",
                "code": 500,
            }
        },
    )

# ---------------------------------------------------------------------------
# Session token for protecting sensitive endpoints (reveal).
# Persisted to ~/.vermes/.session_token so it survives server restarts.
# Injected into the SPA HTML so only the legitimate web UI can use it.
# ---------------------------------------------------------------------------
import os as _os
_SESSION_TOKEN_FILE = _os.path.expanduser("~/.vermes/.session_token")

def _load_or_create_session_token() -> str:
    """Load persisted session token, or create a new one if none exists."""
    try:
        _os.makedirs(_os.path.dirname(_SESSION_TOKEN_FILE), exist_ok=True)
        if _os.path.exists(_SESSION_TOKEN_FILE):
            with open(_SESSION_TOKEN_FILE, "r") as f:
                tok = f.read().strip()
                if tok and len(tok) >= 16:
                    return tok
    except Exception:
        pass
    # Generate new token
    tok = secrets.token_urlsafe(32)
    try:
        with open(_SESSION_TOKEN_FILE, "w") as f:
            f.write(tok)
        _os.chmod(_SESSION_TOKEN_FILE, 0o600)
    except Exception:
        pass
    return tok

_SESSION_TOKEN = _load_or_create_session_token()
_SESSION_HEADER_NAME = "X-Vermes-Session-Token"

# In-browser Chat tab (/chat, /api/pty, …).  Off unless ``Vermes dashboard --tui``
# or VERMES_DASHBOARD_TUI=1.  Set from :func:`start_server`.
_DASHBOARD_EMBEDDED_CHAT_ENABLED = False


# CORS: restrict to localhost origins only.  The web UI is intended to run
# locally; binding to 0.0.0.0 with allow_origins=["*"] would let any website
# read/modify config and secrets.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Request logging middleware — capture all incoming requests for API discovery.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log all incoming requests for debugging / API discovery."""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    logger.info(f"\n{'='*80}")
    logger.info(f"[{ts}] REQUEST: {request.method} {request.url.path}")
    logger.info(f"  Query: {dict(request.query_params)}")
    logger.info(f"  Client: {request.client.host if request.client else 'unknown'}")
    # Log headers (redact sensitive ones)
    headers = dict(request.headers)
    if "authorization" in headers:
        headers["authorization"] = "***"
    if "x-Vermes-session-token" in headers:
        headers["x-Vermes-session-token"] = "***"
    logger.info(f"  Headers: {headers}")
    # Read and log body for POST/PUT/PATCH
    body = None
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body = await request.json()
            # Redact sensitive fields
            if isinstance(body, dict):
                body_log = body.copy()
                for key in ("api_key", "password", "token", "secret", "key", "value"):
                    if key in body_log and isinstance(body_log[key], str) and len(body_log[key]) > 4:
                        body_log[key] = body_log[key][:2] + "***" + body_log[key][-2:]
                    elif key in body_log:
                        body_log[key] = "***"
                # Skip large base64 attachments in logs — show summary only
                if "attachments" in body_log and isinstance(body_log["attachments"], list):
                    att_summary = []
                    for att in body_log["attachments"]:
                        if isinstance(att, dict):
                            att_copy = {k: v for k, v in att.items() if k != "data"}
                            att_copy["data"] = f"<{len(att.get('data', ''))} chars base64>"
                            att_summary.append(att_copy)
                    body_log["attachments"] = att_summary
                body_json = json.dumps(body_log, ensure_ascii=False)
                logger.info(f"  Body: {body_json[:500]}{'... (truncated)' if len(body_json) > 500 else ''}")
        except Exception:
            logger.info(f"  Body: <could not parse JSON>")
    response = await call_next(request)
    logger.info(f"  Response status: {response.status_code}")
    logger.info(f"{'='*80}\n")
    return response


# ---------------------------------------------------------------------------
# Endpoints that do NOT require the session token.  Everything else under
# WebSocket 活跃连接池（多客户端支持）
_active_chat_ws: set = set()

# ── 全渠道实时同步（桌面控制台）──────────────────────────────
# 借鉴 OpenSquilla 的 EventBridge：消息落库即写事件，桌面端尾随事件表
# （channel_sync_events）而非每轮全量扫描 state.db 的渠道会话。
# gateway 在 append_message 时对 channel 会话写入事件行；本进程仅取
# id > 水位的增量行（0.5s 尾随），向所有已连接桌面 WS 广播轻量通知。
# 成本从 O(全会话×消息) 降为 O(新增事件)，延迟 ≤0.5s；零模型调用、零额外 token。
_channel_sync_state: Dict[str, Any] = {
    "last_event_id": None,  # channel_sync_events 水位（已消费最大 id）
    "unread": {},           # session_id -> 未读条数
    "seeded": False,        # 首轮仅建水位、不计未读（避免冷启动误报）
    "task": None,           # 后台尾随任务
}


async def _channel_sync_broadcast(payload: Dict[str, Any]) -> None:
    """向所有已连接的桌面 WS 客户端广播；断开的自动剔除。"""
    dead = set()
    for ws in list(_active_chat_ws):
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            dead.add(ws)
    _active_chat_ws.difference_update(dead)


async def _channel_sync_tick() -> None:
    """一次增量：仅尾随 channel_sync_events 的新增事件行，零全量扫描。"""
    from vermes_state import SessionDB

    try:
        db = SessionDB()
    except Exception:
        return
    try:
        # 首轮：把水位竖到当前最大事件 id，避免回放历史造成误报未读
        if _channel_sync_state["last_event_id"] is None:
            _channel_sync_state["last_event_id"] = db.get_max_channel_sync_event_id()
            _channel_sync_state["seeded"] = True
            return

        last = _channel_sync_state["last_event_id"]
        rows = db.get_channel_sync_events_since(last)
        if not rows:
            return

        # 同一会话的多个新事件聚合成一条更新，避免一 tick 内重复推送
        agg: Dict[str, Dict[str, Any]] = {}
        max_seen = last
        for r in rows:
            max_seen = r["id"]
            sid = r["session_id"]
            if not sid:
                continue
            _channel_sync_state["unread"][sid] = (
                _channel_sync_state["unread"].get(sid, 0) + 1
            )
            bucket = agg.setdefault(sid, {"count": 0, "latest": r})
            bucket["count"] += 1
            bucket["latest"] = r  # 保留最新一条作为预览

        _channel_sync_state["last_event_id"] = max_seen
        for sid, b in agg.items():
            r = b["latest"]
            await _channel_sync_broadcast({
                "type": "channel_update",
                "session_id": sid,
                "source": r["source"],
                "unread": _channel_sync_state["unread"][sid],
                "new_count": b["count"],
                "latest": {
                    "role": r["role"],
                    "content": (r["content_preview"] or "")[:160],
                    "timestamp": r["ts"],
                },
            })
    finally:
        db.close()


async def _channel_sync_loop() -> None:
    """后台增量尾随循环：每 0.5s 消费 channel_sync_events 新行并广播；
    并按小时对事件表做过期 prune，防无限增长。
    无桌面 WS 客户端时立即退出，不空转；下次连接由 chat_ws 惰性重启
    （start guard 检测到 task.done() 即重建）。"""
    _last_prune = 0.0  # 初始 0 → 首轮即跑一次，立刻兜住历史存量库
    while True:
        # 无桌面客户端 → 退出，避免空转。事件表的常规 prune 安全网由
        # gateway 端 append_message 的体积兜底 prune 提供，无需常驻。
        if not _active_chat_ws:
            return
        try:
            await _channel_sync_tick()
        except Exception:
            logger.exception("channel sync tick error")
        # 定期 prune：每小时清一次过期事件（事件表只是实时投递辅助，过期即删；
        # 历史会话清理由独立的记忆/摘要压缩负责，与本表无关）。删除不动最高 id，
        # 冷启动水位快照仍正确。首次启动 60s 后即触发（_last_prune 初始为 0）。
        try:
            _now = time.time()
            if _now - _last_prune >= 3600:
                _last_prune = _now
                from vermes_state import SessionDB
                _pdb = SessionDB()
                try:
                    _pdb.prune_channel_sync_events()
                finally:
                    _pdb.close()
        except Exception:
            logger.exception("channel sync prune error")
        await asyncio.sleep(0.5)

# ---------------------------------------------------------------------------
# _PUBLIC_API_PATHS — endpoints accessible without auth
# ---------------------------------------------------------------------------
_PUBLIC_API_PATHS: frozenset = frozenset({
    # Core status & onboarding
    "/api/status",
    "/api/onboarding",
    # Config (read/write for settings page)
    "/api/config",
    "/api/config/defaults",
    "/api/config/cloud-models",
    "/api/config/schema",
    "/api/config/raw",
    # Model management
    "/api/model/info",
    "/api/model/options",
    "/api/model/auxiliary",
    "/api/model/set",
    "/api/model/discover",
    # Chat
    "/api/chat/completions",
    "/api/chat/models",
    # Provider management (settings page)
    "/api/providers/templates",
    "/api/provider/add",
    "/api/provider/verify",
    "/api/provider/sync-models",
    # Trial token
    "/api/claim",
    # Quota system
    "/api/quota/check",
    "/api/quota/spend",
    "/api/quota/referral/code",
    "/api/quota/referral/bind",
    # WeChat login proxy
    "/api/wechat/qrurl",
    "/api/wechat/poll",
    # 自更新系统
    "/api/update/download",
    "/api/update/apply",
    "/api/update/progress",
    "/api/update/backups",
    "/api/update/rollback",
    # Env vars (settings page needs to read/save keys — token-gated)
    # Removed from public paths: /api/env now requires session token.
    # SPA frontend auto-injects token, so normal usage is unaffected.
    # Dashboard UI
    "/api/dashboard/themes",
    "/api/dashboard/plugins",
    "/api/dashboard/plugins/rescan",
    "/api/dashboard/plugins/hub",
    # Module system
    "/api/modules",    # Analytics (optional)
    "/api/analytics/usage",
    "/api/analytics/models",
    # Vermes GUI 消息持久化
    "/api/gui/messages",
    "/api/gui/sessions",
    # 存储用量（只读，无敏感信息）
    "/api/storage/usage",
    # Agent REST API（外部系统调用）
    "/api/agent/run",
    # Agent 更新检查
    "/api/agent/check",
    "/api/agent/update",
    # 进化系统状态（Sidebar 指示器 + 每日简报 + 成就 + DAG）
    "/api/evolution/status",
    "/api/memory/status",
    "/api/evolution/achievements",
    "/api/evolution/dag",
    # 进化系统自改写（EvolutionPanel 回滚/撤销/历史）
    "/api/evolution/self_modify_history",
    "/api/evolution/retract",
    "/api/evolution/self_modify_rollback",
    # 待审提案（EvolutionPanel 列表 + apply/reject/retract 子路由，前缀匹配）
    "/api/evolution/proposals",
    # 变更记录（EvolutionPanel L1 自动调整通知 + read/unread_count 子路由，前缀匹配）
    "/api/changes",
    # Phase 3 变体隔离（EvolutionPanel list/diff/rollback/pin/unpin/delete 子路由，前缀匹配）
    "/api/evolution/processors",
    # 涌现系统（EvolutionPanel 状态/技能列表/技能确认-拒绝）
    "/api/emergence/status",
    "/api/emergence/skills",
    "/api/emergence/skill",
    # RAG 知识库（前端 Settings 页文件管理 + 搜索）
    "/api/rag/documents",
    "/api/rag/delete/{doc_id}",
    "/api/rag/search",
    # MCP Server 管理（前端 Settings 页配置）
    "/api/mcp/servers",
    "/api/mcp/servers/{name}",
    "/api/mcp/test",
    # 后台子 Agent 状态查询
    "/api/delegate/status/{task_id}",
    # 停止生成（前端 SSE 中断）
    "/api/stop-generation",
    # 会话管理（前端切换/删除会话）
    "/api/sessions",
    # 缓存性能指标
    "/api/cache/metrics",
    # ScholarForge 论文写作（独立模块）
    "/api/scholar",
    # mfgcad 3D 建模（桌面本地工具，session 数据非敏感）
    "/api/mfgcad",
    # Studio 创作空间（直连大模型的试点；SPA 带用户 Key 调用，与 /api/provider/add 同源设计）
    "/api/studio",
    # 文献源注册表（只返回元数据：label/category/字段描述，不含凭证值）
    "/api/registered-services",
    "/api/literature-custom-sources",
})


def _has_valid_session_token(request: Request) -> bool:
    """True if the request carries a valid dashboard session token.

    The dedicated session header avoids collisions with reverse proxies that
    already use ``Authorization`` (for example Caddy ``basic_auth``). We still
    accept the legacy Bearer path for backward compatibility with older
    dashboard bundles.
    """
    session_header = request.headers.get(_SESSION_HEADER_NAME, "")
    if session_header and hmac.compare_digest(
        session_header.encode(),
        _SESSION_TOKEN.encode(),
    ):
        return True

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {_SESSION_TOKEN}"
    return hmac.compare_digest(auth.encode(), expected.encode())


def _require_token(request: Request) -> None:
    """Validate the ephemeral session token.  Raises 401 on mismatch."""
    if not _has_valid_session_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


# Accepted Host header values for loopback binds. DNS rebinding attacks
# point a victim browser at an attacker-controlled hostname (evil.test)
# which resolves to 127.0.0.1 after a TTL flip — bypassing same-origin
# checks because the browser now considers evil.test and our dashboard
# "same origin". Validating the Host header at the app layer rejects any
# request whose Host isn't one we bound for. See GHSA-ppp5-vxwm-4cf7.
_LOOPBACK_HOST_VALUES: frozenset = frozenset({
    "localhost", "127.0.0.1", "::1",
})


def should_require_auth(host: str, allow_public: bool = False) -> bool:
    """Return True iff the dashboard auth gate must be active.

    Truth table:
      host == loopback        → False (no auth — local-only, trusted operator)
      host != loopback        → True  (gate engages — OAuth or password required)

    "Loopback" is 127.0.0.1, localhost, ::1. RFC1918 / CGNAT / link-local are
    deliberately treated as PUBLIC — a hostile device on the same LAN is exactly
    the threat model the gate is designed for.

    ``allow_public`` (the legacy ``--insecure`` escape hatch) NO LONGER disables
    the gate. It is accepted for backward-compat with old launch scripts and
    desktop shells but is ignored: a non-loopback bind ALWAYS requires an auth
    provider (OAuth or the bundled password provider). This closes the
    unauthenticated-public-dashboard hole behind the June 2026 ``Vermes-0day``
    MCP-persistence campaign, where ``--insecure --host 0.0.0.0`` left the
    config/MCP/agent surface open to internet scanners.
    """
    return host not in _LOOPBACK_HOST_VALUES
def _is_accepted_host(host_header: str, bound_host: str) -> bool:
    """True if the Host header targets the interface we bound to.

    Accepts:
    - Exact bound host (with or without port suffix)
    - Loopback aliases when bound to loopback
    - Any host when bound to 0.0.0.0 (explicit opt-in to non-loopback,
      no protection possible at this layer)
    """
    if not host_header:
        return False
    # Strip port suffix. IPv6 addresses use bracket notation:
    #   [::1]         — no port
    #   [::1]:9119    — with port
    # Plain hosts/v4:
    #   localhost:9119
    #   127.0.0.1:9119
    h = host_header.strip()
    if h.startswith("["):
        # IPv6 bracketed — port (if any) follows "]:"
        close = h.find("]")
        if close != -1:
            host_only = h[1:close]  # strip brackets
        else:
            host_only = h.strip("[]")
    else:
        host_only = h.rsplit(":", 1)[0] if ":" in h else h
    host_only = host_only.lower()

    # 0.0.0.0 bind means operator explicitly opted into all-interfaces
    # (requires --insecure per web_server.start_server). No Host-layer
    # defence can protect that mode; rely on operator network controls.
    if bound_host in {"0.0.0.0", "::"}:
        return True

    # Loopback bind: accept the loopback names
    bound_lc = bound_host.lower()
    if bound_lc in _LOOPBACK_HOST_VALUES:
        return host_only in _LOOPBACK_HOST_VALUES

    # Explicit non-loopback bind: require exact host match
    return host_only == bound_lc


@app.middleware("http")
async def host_header_middleware(request: Request, call_next):
    """Reject requests whose Host header doesn't match the bound interface.

    Defends against DNS rebinding: a victim browser on a localhost
    dashboard is tricked into fetching from an attacker hostname that
    TTL-flips to 127.0.0.1. CORS and same-origin checks don't help —
    the browser now treats the attacker origin as same-origin with the
    dashboard. Host-header validation at the app layer catches it.

    See GHSA-ppp5-vxwm-4cf7.
    """
    # Store the bound host on app.state so this middleware can read it —
    # set by start_server() at listen time.
    bound_host = getattr(app.state, "bound_host", None)
    if bound_host:
        host_header = request.headers.get("host", "")
        if not _is_accepted_host(host_header, bound_host):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Invalid Host header. Dashboard requests must use "
                        "the hostname the server was bound to."
                    ),
                },
            )
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require the session token on all /api/ routes except the public list."""
    path = request.url.path
    if path.startswith("/api/"):
        is_public = path in _PUBLIC_API_PATHS or any(path.startswith(p + "/") for p in _PUBLIC_API_PATHS)
        if not is_public and not _has_valid_session_token(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Config schema — auto-generated from DEFAULT_CONFIG
# ---------------------------------------------------------------------------

# Manual overrides for fields that need select options or custom types
_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "model": {
        "type": "string",
        "description": "Default model (e.g. anthropic/claude-sonnet-4.6)",
        "category": "general",
    },
    "model_context_length": {
        "type": "number",
        "description": "Context window override (0 = auto-detect from model metadata)",
        "category": "general",
    },
    "terminal.backend": {
        "type": "select",
        "description": "Terminal execution backend",
        "options": ["local", "docker", "ssh", "modal", "daytona", "vercel_sandbox", "singularity"],
    },
    "terminal.vercel_runtime": {
        "type": "select",
        "description": "Vercel Sandbox runtime",
        "options": ["node24", "node22", "python3.13"],  # sync with _SUPPORTED_VERCEL_RUNTIMES in terminal_tool.py
    },
    "terminal.modal_mode": {
        "type": "select",
        "description": "Modal sandbox mode",
        "options": ["sandbox", "function"],
    },
    "tts.provider": {
        "type": "select",
        "description": "Text-to-speech provider",
        "options": ["edge", "elevenlabs", "openai", "neutts"],
    },
    "stt.provider": {
        "type": "select",
        "description": "Speech-to-text provider",
        # "mistral" temporarily removed — mistralai PyPI package quarantined
        # (malicious 2.4.6 release on 2026-05-12). Restore once available.
        "options": ["local", "openai"],
    },
    "display.skin": {
        "type": "select",
        "description": "CLI visual theme",
        "options": ["default", "ares", "mono", "slate"],
    },
    "dashboard.theme": {
        "type": "select",
        "description": "Web dashboard visual theme",
        "options": ["default", "midnight", "ember", "mono", "cyberpunk", "rose"],
    },
    "display.resume_display": {
        "type": "select",
        "description": "How resumed sessions display history",
        "options": ["minimal", "full", "off"],
    },
    "display.busy_input_mode": {
        "type": "select",
        "description": "Input behavior while agent is running",
        "options": ["interrupt", "queue", "steer"],
    },
    "memory.provider": {
        "type": "select",
        "description": "Memory provider plugin",
        "options": ["builtin", "honcho"],
    },
    "approvals.mode": {
        "type": "select",
        "description": "Dangerous command approval mode",
        "options": ["ask", "yolo", "deny"],
    },
    "context.engine": {
        "type": "select",
        "description": "Context management engine",
        "options": ["default", "custom"],
    },
    "human_delay.mode": {
        "type": "select",
        "description": "Simulated typing delay mode",
        "options": ["off", "typing", "fixed"],
    },
    "logging.level": {
        "type": "select",
        "description": "Log level for agent.log",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.service_tier": {
        "type": "select",
        "description": "API service tier (OpenAI/Anthropic)",
        "options": ["", "auto", "default", "flex"],
    },
    "delegation.reasoning_effort": {
        "type": "select",
        "description": "Reasoning effort for delegated subagents",
        "options": ["", "low", "medium", "high"],
    },
}

# Categories with fewer fields get merged into "general" to avoid tab sprawl.
_CATEGORY_MERGE: Dict[str, str] = {
    "privacy": "security",
    "context": "agent",
    "skills": "agent",
    "cron": "agent",
    "network": "agent",
    "checkpoints": "agent",
    "approvals": "security",
    "human_delay": "display",
    "dashboard": "display",
    "code_execution": "agent",
    "prompt_caching": "agent",
    "goals": "agent",
    # Only `telegram.reactions` currently lives under telegram — fold it in
    # with the other messaging-platform config (discord) so it isn't an
    # orphan tab of one field.
    "telegram": "discord",
}

# Display order for tabs — unlisted categories sort alphabetically after these.
_CATEGORY_ORDER = [
    "general", "agent", "terminal", "display", "delegation",
    "memory", "compression", "security", "browser", "voice",
    "tts", "stt", "logging", "discord", "auxiliary",
]


def _infer_type(value: Any) -> str:
    """Infer a UI field type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _build_schema_from_config(
    config: Dict[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Walk DEFAULT_CONFIG and produce a flat dot-path → field schema dict."""
    schema: Dict[str, Dict[str, Any]] = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        # Skip internal / version keys
        if full_key in {"_config_version",}:
            continue

        # Category is the first path component for nested keys, or "general"
        # for top-level scalar fields (model, toolsets, timezone, etc.).
        if prefix:
            category = prefix.split(".")[0]
        elif isinstance(value, dict):
            category = key
        else:
            category = "general"

        if isinstance(value, dict):
            # Recurse into nested dicts
            schema.update(_build_schema_from_config(value, full_key))
        else:
            entry: Dict[str, Any] = {
                "type": _infer_type(value),
                "description": full_key.replace(".", " → ").replace("_", " ").title(),
                "category": category,
            }
            # Apply manual overrides
            if full_key in _SCHEMA_OVERRIDES:
                entry.update(_SCHEMA_OVERRIDES[full_key])
            # Merge small categories
            entry["category"] = _CATEGORY_MERGE.get(entry["category"], entry["category"])
            schema[full_key] = entry
    return schema


CONFIG_SCHEMA = _build_schema_from_config(DEFAULT_CONFIG)

# Inject virtual fields that don't live in DEFAULT_CONFIG but are surfaced
# by the normalize/denormalize cycle.  Insert model_context_length right after
# the "model" key so it renders adjacent in the frontend.
_mcl_entry = _SCHEMA_OVERRIDES["model_context_length"]
_ordered_schema: Dict[str, Dict[str, Any]] = {}
for _k, _v in CONFIG_SCHEMA.items():
    _ordered_schema[_k] = _v
    if _k == "model":
        _ordered_schema["model_context_length"] = _mcl_entry
CONFIG_SCHEMA = _ordered_schema


class ConfigUpdate(BaseModel):
    config: dict


class EnvVarUpdate(BaseModel):
    key: str
    value: str


class EnvVarDelete(BaseModel):
    key: str


class EnvVarReveal(BaseModel):
    key: str


class ModelAssignment(BaseModel):
    """Payload for POST /api/model/set — assign a provider/model to a slot.

    scope="main"        → writes model.provider + model.default
    scope="auxiliary"   → writes auxiliary.<task>.provider + auxiliary.<task>.model
    scope="auxiliary" with task=""  → applied to every auxiliary.* slot
    scope="auxiliary" with task="__reset__"  → resets every slot to provider="auto"
    """
    scope: str
    provider: str
    model: str
    task: str = ""
    max_tokens: Optional[int] = None  # 可选：设置 model.max_tokens（输出上限）

def _normalize_config_for_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config for the web UI.

    Vermes supports ``model`` as either a bare string (``"anthropic/claude-sonnet-4"``)
    or a dict (``{default: ..., provider: ..., base_url: ...}``).  The schema is built
    from DEFAULT_CONFIG where ``model`` is a string, but user configs often have the
    dict form.  Normalize to the string form so the frontend schema matches.

    Also surfaces ``model_context_length`` as a top-level field so the web UI can
    display and edit it.  A value of 0 means "auto-detect".
    """
    config = dict(config)  # shallow copy
    model_val = config.get("model")
    if isinstance(model_val, dict):
        # Extract context_length before flattening the dict
        ctx_len = model_val.get("context_length", 0)
        config["model"] = model_val.get("default", model_val.get("name", ""))
        config["model_context_length"] = ctx_len if isinstance(ctx_len, int) else 0
    else:
        config["model_context_length"] = 0
    return config


async def get_config():
    config = _normalize_config_for_web(load_config())
    # Strip internal keys that the frontend shouldn't see or send back
    return {k: v for k, v in config.items() if not k.startswith("_")}


async def get_defaults():
    return DEFAULT_CONFIG


async def get_schema():
    return {"fields": CONFIG_SCHEMA, "category_order": _CATEGORY_ORDER}


_EMPTY_MODEL_INFO: dict = {
    "model": "",
    "provider": "",
    "auto_context_length": 0,
    "config_context_length": 0,
    "effective_context_length": 0,
    "capabilities": {},
}


def get_model_info():
    """Return resolved model metadata for the currently configured model.

    Calls the same context-length resolution chain the agent uses, so the
    frontend can display "Auto-detected: 200K" alongside the override field.
    Also returns model capabilities (vision, reasoning, tools) when available.
    """
    try:
        cfg = load_config()
        model_cfg = cfg.get("model", "")

        # Extract model name and provider from the config
        if isinstance(model_cfg, dict):
            model_name = model_cfg.get("default", model_cfg.get("name", ""))
            provider = model_cfg.get("provider", "")
            base_url = model_cfg.get("base_url", "")
            config_ctx = model_cfg.get("context_length")
            config_max_tokens = model_cfg.get("max_tokens")
        else:
            model_name = str(model_cfg) if model_cfg else ""
            provider = ""
            base_url = ""
            config_ctx = None
            config_max_tokens = None

        if not model_name:
            return dict(_EMPTY_MODEL_INFO, provider=provider)

        # Resolve auto-detected context length (pass config_ctx=None to get
        # purely auto-detected value, then separately report the override)
        try:
            from agent.model_metadata import get_model_context_length
            auto_ctx = get_model_context_length(
                model=model_name,
                base_url=base_url,
                provider=provider,
                config_context_length=None,  # ignore override — we want auto value
            )
        except Exception:
            auto_ctx = 0

        config_ctx_int = 0
        if isinstance(config_ctx, int) and config_ctx > 0:
            config_ctx_int = config_ctx

        # Effective is what the agent actually uses
        effective_ctx = config_ctx_int if config_ctx_int > 0 else auto_ctx

        # Try to get model capabilities from models.dev
        caps = {}
        try:
            from agent.models_dev import get_model_capabilities
            mc = get_model_capabilities(provider=provider, model=model_name)
            if mc is not None:
                caps = {
                    "supports_tools": mc.supports_tools,
                    "supports_vision": mc.supports_vision,
                    "supports_reasoning": mc.supports_reasoning,
                    "context_window": mc.context_window,
                    "max_output_tokens": mc.max_output_tokens,
                    "model_family": mc.model_family,
                }
        except Exception:
            pass

        return {
            "model": model_name,
            "provider": provider,
            "auto_context_length": auto_ctx,
            "config_context_length": config_ctx_int,
            "effective_context_length": effective_ctx,
            "config_max_tokens": config_max_tokens,
            "capabilities": caps,
        }
    except Exception:
        _log.exception("GET /api/model/info failed")
        return dict(_EMPTY_MODEL_INFO)


# ---------------------------------------------------------------------------
# Model assignment — pick provider+model for main slot or auxiliary slots.
# Mirrors the model.options JSON-RPC from tui_gateway but uses REST so the
# Models page (which has no chat PTY open) can drive it.
# ---------------------------------------------------------------------------

# Canonical auxiliary task slots. Keep in sync with DEFAULT_CONFIG["auxiliary"]
# in vermes_cli/config.py — listed here for deterministic ordering in the UI.
_AUX_TASK_SLOTS: Tuple[str, ...] = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
    "curator",
)


def get_model_options():
    """Return authenticated providers + their curated model lists.

    REST equivalent of the ``model.options`` JSON-RPC on tui_gateway, so the
    dashboard Models page can render the picker without a live chat session.
    The response shape matches ``model.options`` 1:1 so ``ModelPickerDialog``
    can share the same types.
    """
    try:
        from vermes_cli.inventory import build_models_payload, load_picker_context

        return build_models_payload(load_picker_context(), max_models=50)
    except Exception:
        _log.exception("GET /api/model/options failed")
        raise HTTPException(status_code=500, detail="Failed to list model options")


def get_auxiliary_models():
    """Return current auxiliary task assignments.

    Shape:
      {
        "tasks": [
          {"task": "vision", "provider": "auto", "model": "", "base_url": ""},
          ...
        ],
        "main": {"provider": "openrouter", "model": "anthropic/claude-opus-4.7"},
      }
    """
    try:
        cfg = load_config()
        aux_cfg = cfg.get("auxiliary", {})
        if not isinstance(aux_cfg, dict):
            aux_cfg = {}

        tasks = []
        for slot in _AUX_TASK_SLOTS:
            slot_cfg = aux_cfg.get(slot, {}) if isinstance(aux_cfg.get(slot), dict) else {}
            tasks.append({
                "task": slot,
                "provider": str(slot_cfg.get("provider", "auto") or "auto"),
                "model": str(slot_cfg.get("model", "") or ""),
                "base_url": str(slot_cfg.get("base_url", "") or ""),
            })

        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            main = {
                "provider": str(model_cfg.get("provider", "") or ""),
                "model": str(model_cfg.get("default", model_cfg.get("name", "")) or ""),
            }
        else:
            main = {"provider": "", "model": str(model_cfg) if model_cfg else ""}

        return {"tasks": tasks, "main": main}
    except Exception:
        _log.exception("GET /api/model/auxiliary failed")
        raise HTTPException(status_code=500, detail="Failed to read auxiliary config")


async def set_model_assignment(body: ModelAssignment):
    """Assign a model to the main slot or an auxiliary task slot.

    Writes to ``VERMES_HOME/config.yaml`` — applies to **new** sessions only.
    The currently running chat PTY (if any) is not affected; use the
    ``/model`` slash command inside a chat to hot-swap that specific session.
    """
    scope = (body.scope or "").strip().lower()
    provider = (body.provider or "").strip()
    model = (body.model or "").strip()
    task = (body.task or "").strip().lower()

    if scope not in {"main", "auxiliary"}:
        raise HTTPException(status_code=400, detail="scope must be 'main' or 'auxiliary'")

    try:
        cfg = load_config()

        if scope == "main":
            if not provider or not model:
                raise HTTPException(status_code=400, detail="provider and model required for main")
            model_cfg = cfg.get("model", {})
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            model_cfg["provider"] = provider
            model_cfg["default"] = model
            # max_tokens 可选：设置输出上限（None=不修改，0=清除让模型自己决定）
            if body.max_tokens is not None:
                if body.max_tokens > 0:
                    model_cfg["max_tokens"] = body.max_tokens
                else:
                    model_cfg.pop("max_tokens", None)
            # Clear stale base_url so the resolver picks the provider's own default.
            if "base_url" in model_cfg and model_cfg.get("base_url"):
                model_cfg["base_url"] = ""
            # Also clear hardcoded context_length override — new model may have
            # a different context window.
            if "context_length" in model_cfg:
                model_cfg.pop("context_length", None)
            cfg["model"] = model_cfg
            save_config(cfg)
            return {"ok": True, "scope": "main", "provider": provider, "model": model}

        # scope == "auxiliary"
        aux = cfg.get("auxiliary")
        if not isinstance(aux, dict):
            aux = {}

        if task == "__reset__":
            # Reset every slot to provider="auto", model="" — keeps other fields intact.
            for slot in _AUX_TASK_SLOTS:
                slot_cfg = aux.get(slot)
                if not isinstance(slot_cfg, dict):
                    slot_cfg = {}
                slot_cfg["provider"] = "auto"
                slot_cfg["model"] = ""
                aux[slot] = slot_cfg
            cfg["auxiliary"] = aux
            save_config(cfg)
            return {"ok": True, "scope": "auxiliary", "reset": True}

        if not provider:
            raise HTTPException(status_code=400, detail="provider required for auxiliary")

        targets = [task] if task else list(_AUX_TASK_SLOTS)
        for slot in targets:
            if slot not in _AUX_TASK_SLOTS:
                raise HTTPException(status_code=400, detail=f"unknown auxiliary task: {slot}")
            slot_cfg = aux.get(slot)
            if not isinstance(slot_cfg, dict):
                slot_cfg = {}
            slot_cfg["provider"] = provider
            slot_cfg["model"] = model
            aux[slot] = slot_cfg

        cfg["auxiliary"] = aux
        save_config(cfg)
        return {
            "ok": True,
            "scope": "auxiliary",
            "tasks": targets,
            "provider": provider,
            "model": model,
        }
    except HTTPException:
        raise
    except Exception:
        _log.exception("POST /api/model/set failed")
        raise HTTPException(status_code=500, detail="Failed to save model assignment")

def _denormalize_config_from_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse _normalize_config_for_web before saving.

    Reconstructs ``model`` as a dict by reading the current on-disk config
    to recover model subkeys (provider, base_url, api_mode, etc.) that were
    stripped from the GET response.  The frontend only sees model as a flat
    string; the rest is preserved transparently.

    Also handles ``model_context_length`` — writes it back into the model dict
    as ``context_length``.  A value of 0 or absent means "auto-detect" (omitted
    from the dict so get_model_context_length() uses its normal resolution).
    """
    config = dict(config)
    # Remove any _model_meta that might have leaked in (shouldn't happen
    # with the stripped GET response, but be defensive)
    config.pop("_model_meta", None)

    # Extract and remove model_context_length before processing model
    ctx_override = config.pop("model_context_length", 0)
    if not isinstance(ctx_override, int):
        try:
            ctx_override = int(ctx_override)
        except (TypeError, ValueError):
            ctx_override = 0

    model_val = config.get("model")
    if isinstance(model_val, str) and model_val:
        # Read the current disk config to recover model subkeys
        try:
            disk_config = load_config()
            disk_model = disk_config.get("model")
            if isinstance(disk_model, dict):
                # Preserve all subkeys, update default with the new value
                disk_model["default"] = model_val
                # Write context_length into the model dict (0 = remove/auto)
                if ctx_override > 0:
                    disk_model["context_length"] = ctx_override
                else:
                    disk_model.pop("context_length", None)
                config["model"] = disk_model
            # Model was previously a bare string — upgrade to dict if
            # user is setting a context_length override
            elif ctx_override > 0:
                config["model"] = {
                    "default": model_val,
                    "context_length": ctx_override,
                }
        except Exception:
            pass  # can't read disk config — just use the string form
    return config


async def update_config(body: ConfigUpdate):
    try:
        save_config(_denormalize_config_from_web(body.config))
        return {"ok": True}
    except Exception:
        _log.exception("PUT /api/config failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def get_env_vars():
    env_on_disk = load_env()
    result = {}
    for var_name, info in OPTIONAL_ENV_VARS.items():
        value = env_on_disk.get(var_name)
        result[var_name] = {
            "is_set": bool(value),
            "redacted_value": redact_key(value) if value else None,
            "description": info.get("description", ""),
            "url": info.get("url"),
            "category": info.get("category", ""),
            "is_password": info.get("password", False),
            "tools": info.get("tools", []),
            "advanced": info.get("advanced", False),
        }
    return result


# Allowed keys for /api/env PUT – prevent arbitrary .env writes
_ENV_WRITE_ALLOWED_KEYS: frozenset = frozenset({
    "DEFAULT_MODEL", "DEFAULT_PROVIDER", "THEME", "LANGUAGE",
    # 主流 provider
    "VBIT_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
    "QWEN_API_KEY", "ZHIPU_API_KEY", "MISTRAL_API_KEY",
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    # 国产 provider
    "XIAOMI_API_KEY", "DOUBAO_API_KEY", "MOONSHOT_API_KEY",
    "BAICHUAN_API_KEY", "YI_API_KEY", "SPARK_API_KEY",
    "SILICONFLOW_API_KEY", "Baidu_API_KEY", "BAIDU_API_KEY",
    "XINGHUO_API_KEY", "STEPFUN_API_KEY", "MINIMAX_API_KEY",
    "ANT_LING_API_KEY",
    # 国际 provider
    "GEMINI_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY",
    "COHERE_API_KEY",
    # 自定义
    "CUSTOM_API_KEY",
    # 文献源 env keys（ScholarForge literature providers）
    "CNKI_API_KEY", "CNKI_GATEWAY_URL", "CNKI_USERNAME", "CNKI_PASSWORD",
    "WANFANG_API_KEY", "WANFANG_USER", "WANFANG_PASSWORD",
    "VIP_API_KEY", "VIP_GATEWAY_URL", "VIP_USERNAME", "VIP_PASSWORD",
    "SCOPUS_API_KEY", "SCOPUS_INST_TOKEN",
    "IEEE_API_KEY", "WOS_API_KEY", "SCIENCEDIRECT_API_KEY",
    "SPRINGER_API_KEY", "S2_API_KEY", "CORE_API_KEY",
    "EBSCO_USER_ID", "EBSCO_PASSWORD", "EBSCO_PROFILE",
})

async def set_env_var(body: EnvVarUpdate, request: Request):
    # /api/env was removed from _PUBLIC_API_PATHS — now requires session token.
    # All env endpoints (GET/PUT/DELETE/reveal) are token-gated.
    if body.key not in _ENV_WRITE_ALLOWED_KEYS:
        raise HTTPException(status_code=403, detail=f"Key '{body.key}' is not allowed")
    try:
        _log.info(f"[ENV] Updated {body.key}")
        save_env_value(body.key, body.value)
        return {"ok": True, "key": body.key}
    except Exception:
        _log.exception("PUT /api/env failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def reveal_env_var(body: EnvVarReveal, request: Request):
    """Return the real (unredacted) value of a single env var.

    Protected by:
    - Ephemeral session token (generated per server start, injected into SPA)
    - Rate limiting (max 5 reveals per 30s window)
    - Audit logging
    """
    # --- Token check ---
    _require_token(request)

    # --- Rate limit ---
    now = time.time()
    cutoff = now - _REVEAL_WINDOW_SECONDS
    _reveal_timestamps[:] = [t for t in _reveal_timestamps if t > cutoff]
    if len(_reveal_timestamps) >= _REVEAL_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="Too many reveal requests. Try again shortly.")
    _reveal_timestamps.append(now)

    # --- Reveal ---
    env_on_disk = load_env()
    value = env_on_disk.get(body.key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"{body.key} not found in .env")

    _log.info("env/reveal: %s", body.key)
    return {"key": body.key, "value": value}

from vermes_cli.blueprints.helpers import _session_latest_descendant


# ---------------------------------------------------------------------------
# WebChat API — proxy to OpenAI-compatible LLM endpoint
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str | list  # str for text, list for multimodal (OpenAI format)


class AttachmentData(BaseModel):
    name: str
    type: str  # "image" or "file"
    data: str  # base64 encoded
    mime: str = ""
    size: int = 0


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    provider: str | None = None
    stream: bool = True
    attachments: list[AttachmentData] | None = None
    wechat_openid: str | None = None


def _validate_attachments(attachments: list[AttachmentData] | None) -> tuple[list[AttachmentData], str | None]:
    """Validate attachment MIME types and sizes.

    Returns (filtered_attachments, error_message).
    error_message is None when all attachments are valid.
    """
    if not attachments:
        return [], None

    total_size = 0
    filtered = []
    errors = []

    for att in attachments:
        # Size check per file
        if att.size > _MAX_SINGLE_ATTACHMENT_SIZE:
            errors.append(f"{att.name}: 单文件超过 20MB 限制")
            continue

        total_size += att.size

        # MIME whitelist check
        mime = (att.mime or "application/octet-stream").lower()
        if mime not in _ALLOWED_MIME_TYPES:
            # Allow common image types even if MIME is generic
            if not mime.startswith("image/"):
                errors.append(f"{att.name}: 不支持的文件类型 ({mime})")
                continue

        filtered.append(att)

    if total_size > _MAX_ATTACHMENT_SIZE:
        errors.append(f"附件总大小 {total_size / 1024 / 1024:.1f}MB 超过 50MB 限制")

    if errors:
        return filtered, "; ".join(errors)
    return filtered, None


# ---------------------------------------------------------------------------
# Raw YAML config endpoint
# ---------------------------------------------------------------------------


class RawConfigUpdate(BaseModel):
    yaml_text: str


async def get_config_raw():
    path = get_config_path()
    if not path.exists():
        return {"yaml": ""}
    return {"yaml": path.read_text(encoding="utf-8")}


async def update_config_raw(body: RawConfigUpdate):
    try:
        parsed = yaml.safe_load(body.yaml_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML must be a mapping")
        save_config(parsed)
        return {"ok": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

# /api/pty — PTY-over-WebSocket bridge for the dashboard "Chat" tab.
#
# The endpoint spawns the same ``Vermes --tui`` binary the CLI uses, behind
# a POSIX pseudo-terminal, and forwards bytes + resize escapes across a
# WebSocket.  The browser renders the ANSI through xterm.js (see
# web/src/pages/ChatPage.tsx).
#
# Auth: ``?token=<session_token>`` query param (browsers can't set
# Authorization on the WS upgrade).  Same ephemeral ``_SESSION_TOKEN`` as
# REST.  Localhost-only — we defensively reject non-loopback clients even
# though uvicorn binds to 127.0.0.1.
# ---------------------------------------------------------------------------

import re
import asyncio

# PTY bridge is POSIX-only (depends on fcntl/termios/ptyprocess).  On native
# Windows the import raises; catch and leave PtyBridge=None so the rest of
# the dashboard (sessions, jobs, metrics, config editor) still loads and the
# /api/pty endpoint cleanly refuses with a WSL-suggested message.
try:
    from vermes_cli.pty_bridge import PtyBridge, PtyUnavailableError
    _PTY_BRIDGE_AVAILABLE = True
except ImportError as _pty_import_err:  # pragma: no cover - Windows-only path
    PtyBridge = None  # type: ignore[assignment]
    _PTY_BRIDGE_AVAILABLE = False

    class PtyUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Stub on platforms where pty_bridge can't be imported."""
        pass

_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
_PTY_READ_CHUNK_TIMEOUT = 0.2
_VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Starlette's TestClient reports the peer as "testclient"; treat it as
# loopback so tests don't need to rewrite request scope.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _ws_client_reason(ws: "WebSocket") -> Optional[str]:
    """Return a rejection reason for the client IP, or None when allowed.

    Reasons are short machine-parseable tokens logged on the rejection path
    so a "WS keeps closing" report can be diagnosed from agent.log without a
    repro. ``None`` means the peer IP passed this gate.

    See :func:`_ws_client_is_allowed` for the full policy rationale.
    """
    if getattr(app.state, "auth_required", False):
        return None
    bound_host = (getattr(app.state, "bound_host", "") or "").strip().lower()
    if bound_host and bound_host not in _LOOPBACK_HOSTS:
        return None
    client_host = ws.client.host if ws.client else ""
    if not client_host:
        # Fail-closed: a loopback-bound dashboard with auth disabled must
        # not accept a WebSocket with no identifiable peer. ASGI servers
        # behind a misconfigured proxy or unix socket can deliver
        # ws.client == None or "" — treating that as "allowed" would let
        # an unidentified peer reach a loopback-only surface.
        return f"missing_or_empty_peer bound={bound_host or '?'}"
    if client_host in _LOOPBACK_HOSTS:
        return None
    return f"peer_not_loopback peer={client_host} bound={bound_host or '?'}"


def _ws_client_is_allowed(ws: "WebSocket") -> bool:
    """Check if the WebSocket client IP is acceptable.

    Allows loopback always; allows any IP when bound to all-interfaces
    (--insecure mode, guarded by session token auth).
    """
    if _is_public_bind():
        return True
    client_host = ws.client.host if ws.client else ""
    if not client_host:
        # Fail-closed: see _ws_client_reason for rationale. An empty
        # client_host on a loopback-bound dashboard with auth disabled
        # must be rejected, not accepted as a default-allow.
        return False
    return client_host in _LOOPBACK_HOSTS

# Per-channel subscriber registry used by /api/pub (PTY-side gateway → dashboard)
# and /api/events (dashboard → browser sidebar).  Keyed by an opaque channel id
# the chat tab generates on mount; entries auto-evict when the last subscriber
# drops AND the publisher has disconnected.
_event_channels: dict[str, set] = {}
_event_lock = asyncio.Lock()


def _resolve_chat_argv(
    resume: Optional[str] = None,
    sidecar_url: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[dict]]:
    """Resolve the argv + cwd + env for the chat PTY.

    Default: whatever ``Vermes --tui`` would run.  Tests monkeypatch this
    function to inject a tiny fake command (``cat``, ``sh -c 'printf …'``)
    so nothing has to build Node or the TUI bundle.

    Session resume is propagated via the ``VERMES_TUI_RESUME`` env var —
    matching what ``vermes_cli.main._launch_tui`` does for the CLI path.
    Appending ``--resume <id>`` to argv doesn't work because ``ui-tui`` does
    not parse its argv.

    `sidecar_url` (when set) is forwarded as ``VERMES_TUI_SIDECAR_URL`` so
    the spawned ``tui_gateway.entry`` can mirror dispatcher emits to the
    dashboard's ``/api/pub`` endpoint (see :func:`pub_ws`).
    """
    from vermes_cli.main import PROJECT_ROOT, _make_tui_argv

    argv, cwd = _make_tui_argv(PROJECT_ROOT / "ui-tui", tui_dev=False)
    env = os.environ.copy()
    env.setdefault("NODE_ENV", "production")
    # Browser-embedded chat should prefer stable wheel-based scrollback over
    # native terminal mouse tracking. When mouse tracking is enabled, wheel
    # events are consumed by the TUI and forwarded as terminal input, which
    # makes browser-side transcript scrolling feel broken. Keep the terminal
    # build unchanged for native CLI usage; only disable mouse tracking for
    # the dashboard PTY path.
    env.setdefault("VERMES_TUI_DISABLE_MOUSE", "1")
    env.setdefault("VERMES_TUI_INLINE", "1")

    if resume:
        latest_resume, _latest_path = _session_latest_descendant(resume)
        if latest_resume:
            resume = latest_resume
        env["VERMES_TUI_RESUME"] = resume

    if sidecar_url:
        env["VERMES_TUI_SIDECAR_URL"] = sidecar_url

    return list(argv), str(cwd) if cwd else None, env


def _build_sidecar_url(channel: str) -> Optional[str]:
    """ws:// URL the PTY child should publish events to, or None when unbound."""
    host = getattr(app.state, "bound_host", None)
    port = getattr(app.state, "bound_port", None)

    if not host or not port:
        return None

    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    qs = urllib.parse.urlencode({"token": _SESSION_TOKEN, "channel": channel})

    return f"ws://{netloc}/api/pub?{qs}"


async def _broadcast_event(channel: str, payload: str) -> None:
    """Fan out one publisher frame to every subscriber on `channel`."""
    async with _event_lock:
        subs = list(_event_channels.get(channel, ()))

    for sub in subs:
        try:
            await sub.send_text(payload)
        except Exception:
            # Subscriber went away mid-send; the /api/events finally clause
            # will remove it from the registry on its next iteration.
            pass


def _channel_or_close_code(ws: WebSocket) -> Optional[str]:
    """Return the channel id from the query string or None if invalid."""
    channel = ws.query_params.get("channel", "")

    return channel if _VALID_CHANNEL_RE.match(channel) else None


@app.websocket("/api/pty")
async def pty_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    # --- auth + loopback check (before accept so we can close cleanly) ---
    token = ws.query_params.get("token", "")
    expected = _SESSION_TOKEN
    if not hmac.compare_digest(token.encode(), expected.encode()):
        await ws.close(code=4401)
        return

    if not _ws_client_is_allowed(ws):
        await ws.close(code=4403)
        return

    await ws.accept()

    # On native Windows, the POSIX PTY bridge can't be imported.  Tell the
    # client and close cleanly rather than pretending the feature works.
    if not _PTY_BRIDGE_AVAILABLE:
        await ws.send_text(
            "\r\n\x1b[31mChat unavailable: the embedded terminal requires a "
            "POSIX PTY, which native Windows Python doesn't provide.\x1b[0m\r\n"
            "\x1b[33mInstall Vermes inside WSL2 to use the dashboard's /chat "
            "tab — the rest of the dashboard works here.\x1b[0m\r\n"
        )
        await ws.close(code=1011)
        return

    # --- spawn PTY ------------------------------------------------------
    resume = ws.query_params.get("resume") or None
    channel = _channel_or_close_code(ws)
    sidecar_url = _build_sidecar_url(channel) if channel else None

    try:
        argv, cwd, env = _resolve_chat_argv(resume=resume, sidecar_url=sidecar_url)
    except SystemExit as exc:
        # _make_tui_argv calls sys.exit(1) when node/npm is missing.
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return


    try:
        bridge = PtyBridge.spawn(argv, cwd=cwd, env=env)
    except PtyUnavailableError as exc:
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    except (FileNotFoundError, OSError) as exc:
        await ws.send_text(f"\r\n\x1b[31mChat failed to start: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return

    loop = asyncio.get_running_loop()

    # --- reader task: PTY master → WebSocket ----------------------------
    async def pump_pty_to_ws() -> None:
        while True:
            chunk = await loop.run_in_executor(
                None, bridge.read, _PTY_READ_CHUNK_TIMEOUT
            )
            if chunk is None:  # EOF
                return
            if not chunk:  # no data this tick; yield control and retry
                await asyncio.sleep(0)
                continue
            try:
                await ws.send_bytes(chunk)
            except Exception:
                return

    reader_task = asyncio.create_task(pump_pty_to_ws())

    # --- writer loop: WebSocket → PTY master ----------------------------
    try:
        while True:
            msg = await ws.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            raw = msg.get("bytes")
            if raw is None:
                text = msg.get("text")
                raw = text.encode("utf-8") if isinstance(text, str) else b""
            if not raw:
                continue

            # Resize escape is consumed locally, never written to the PTY.
            match = _RESIZE_RE.match(raw)
            if match and match.end() == len(raw):
                cols = int(match.group(1))
                rows = int(match.group(2))
                bridge.resize(cols=cols, rows=rows)
                continue

            bridge.write(raw)
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        bridge.close()


# ---------------------------------------------------------------------------
# /api/ws — JSON-RPC WebSocket sidecar for the dashboard "Chat" tab.
#
# Drives the same `tui_gateway.dispatch` surface Ink uses over stdio, so the
# dashboard can render structured metadata (model badge, tool-call sidebar,
# slash launcher, session info) alongside the xterm.js terminal that PTY
# already paints. Both transports bind to the same session id when one is
# active, so a tool.start emitted by the agent fans out to both sinks.
# ---------------------------------------------------------------------------


@app.websocket("/api/ws")
async def gateway_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await ws.close(code=4401)
        return

    if not _ws_client_is_allowed(ws):
        await ws.close(code=4403)
        return

    from tui_gateway.ws import handle_ws

    await handle_ws(ws)


# ---------------------------------------------------------------------------
# /api/pub + /api/events — chat-tab event broadcast.
#
# The PTY-side ``tui_gateway.entry`` opens /api/pub at startup (driven by
# VERMES_TUI_SIDECAR_URL set in /api/pty's PTY env) and writes every
# dispatcher emit through it.  The dashboard fans those frames out to any
# subscriber that opened /api/events on the same channel id.  This is what
# gives the React sidebar its tool-call feed without breaking the PTY
# child's stdio handshake with Ink.
# ---------------------------------------------------------------------------


@app.websocket("/api/pub")
async def pub_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await ws.close(code=4401)
        return

    if not _ws_client_is_allowed(ws):
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    try:
        while True:
            await _broadcast_event(channel, await ws.receive_text())
    except WebSocketDisconnect:
        pass


@app.websocket("/api/events")
async def events_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await ws.close(code=4401)
        return

    if not _ws_client_is_allowed(ws):
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    async with _event_lock:
        _event_channels.setdefault(channel, set()).add(ws)

    try:
        while True:
            # Subscribers don't speak — the receive() just blocks until
            # disconnect so the connection stays open as long as the
            # browser holds it.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _event_lock:
            subs = _event_channels.get(channel)

            if subs is not None:
                subs.discard(ws)

                if not subs:
                    _event_channels.pop(channel, None)


@app.websocket("/api/ws/chat")
async def chat_ws(ws: WebSocket) -> None:
    """Real-time chat WebSocket — 停止生成、会话切换、全渠道实时同步。

    消息发送继续走 SSE (/api/chat/completions)，
    本端点处理实时控制信号 + 渠道新消息推送（桌面控制台）。
    """
    await ws.accept()
    _active_chat_ws.add(ws)
    # 首个桌面客户端连接时启动后台渠道轮询（仅起一次）
    _sync_task = _channel_sync_state["task"]
    if _sync_task is None or getattr(_sync_task, "done", lambda: True)():
        try:
            _channel_sync_state["task"] = asyncio.create_task(_channel_sync_loop())
        except Exception:
            logger.warning("failed to start channel sync loop")
    # 下发当前未读快照，避免冷连接后角标空白
    try:
        await ws.send_text(json.dumps({
            "type": "channel_unread_snapshot",
            "unread": dict(_channel_sync_state["unread"]),
        }, ensure_ascii=False))
    except Exception:
        pass
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "stop":
                # 停止指定会话的生成
                session_id = msg.get("session_id", "")
                from vermes_cli.blueprints.agent_cache import stop_agent_session
                await stop_agent_session(session_id)
                await ws.send_text(json.dumps({"type": "stopped", "session_id": session_id}))

            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "mark_read":
                # 桌面标记某渠道会话已读 → 清除未读角标并广播
                sid = msg.get("session_id", "")
                if sid:
                    _channel_sync_state["unread"][sid] = 0
                    await _channel_sync_broadcast({
                        "type": "channel_update",
                        "session_id": sid,
                        "source": None,
                        "unread": 0,
                        "new_count": 0,
                        "latest": None,
                    })

    except WebSocketDisconnect:
        pass
    finally:
        _active_chat_ws.discard(ws)


def _normalise_prefix(raw: Optional[str]) -> str:
    """Normalise an X-Forwarded-Prefix header value.

    Returns a string like ``"/Vermes"`` (no trailing slash) or ``""`` when
    no prefix is set / the header is malformed. We deliberately reject
    anything containing ``..`` or non-printable bytes so a hostile proxy
    can't inject HTML via the prefix.
    """
    if not raw:
        return ""
    p = raw.strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    if "//" in p or ".." in p or any(c in p for c in ('"', "'", "<", ">", " ", "\n", "\r", "\t")):
        return ""
    if len(p) > 64:
        return ""
    return p


def mount_spa(application: FastAPI):
    """Mount the built SPA. Falls back to index.html for client-side routing.

    The session token is injected into index.html via a ``<script>`` tag so
    the SPA can authenticate against protected API endpoints without a
    separate (unauthenticated) token-dispensing endpoint.

    When served behind a path-prefix reverse proxy (e.g.
    ``mission-control.tilos.com/Vermes/*`` -> local Caddy -> :9119), the
    proxy injects ``X-Forwarded-Prefix: /Vermes`` on every request. We
    rewrite the served ``index.html`` so absolute asset URLs (``/assets/...``)
    and the SPA's runtime ``__vermes_BASE_PATH__`` honour that prefix
    without rebuilding the bundle.
    """
    if not WEB_DIST.exists():
        @application.get("/{full_path:path}")
        async def no_frontend(full_path: str):
            return JSONResponse(
                {"error": "Frontend not built. Run: cd web && npm run build"},
                status_code=404,
            )
        return

    _index_path = WEB_DIST / "index.html"

    def _serve_index(prefix: str = ""):
        """Return index.html with the session token + base-path injected.

        ``prefix`` is the normalised ``X-Forwarded-Prefix`` (e.g. ``/Vermes``)
        or empty string when served at root.
        """
        html = _index_path.read_text()
        chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
        token_script = (
            f'<script>'
            f'window.__VERMES_SESSION_TOKEN__="{_SESSION_TOKEN}";'
            f'window.__OPENCLAW_SESSION_KEY__="{_SESSION_TOKEN}";'
            f'window.__vermes_DASHBOARD_EMBEDDED_CHAT__={chat_js};'
            f'window.__vermes_BASE_PATH__="{prefix}";'
            f'</script>'
        )
        if prefix:
            # Rewrite absolute asset URLs baked into the Vite build so the
            # browser fetches them through the same proxy prefix.
            html = html.replace('href="/assets/', f'href="{prefix}/assets/')
            html = html.replace('src="/assets/', f'src="{prefix}/assets/')
            html = html.replace('href="/favicon.ico"', f'href="{prefix}/favicon.ico"')
            html = html.replace('href="/fonts/', f'href="{prefix}/fonts/')
            html = html.replace('href="/ds-assets/', f'href="{prefix}/ds-assets/')
            html = html.replace('src="/ds-assets/', f'src="{prefix}/ds-assets/')
        html = html.replace("</head>", f"{token_script}</head>", 1)
        # Inject shutdown-on-close script
        html = html.replace("</body>",
            '<script>window.addEventListener("beforeunload",function(){navigator.sendBeacon("/api/shutdown","close")});</script></body>', 1)
        return HTMLResponse(
            html,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    # When served behind a path-prefix proxy, the built CSS contains
    # absolute ``url(/fonts/...)`` and ``url(/ds-assets/...)`` references.
    # Browsers resolve those against the document origin, which means
    # under ``/Vermes`` they'd hit ``mission-control.tilos.com/fonts/...``
    # (the MC Pages app), not the Vermes backend. Intercept CSS asset
    # requests BEFORE the StaticFiles mount and rewrite the absolute paths
    # when a prefix is in play.
    @application.get("/assets/{filename}.css")
    async def serve_css(filename: str, request: Request):
        css_path = WEB_DIST / "assets" / f"{filename}.css"
        if not css_path.is_file() or not css_path.resolve().is_relative_to(
            WEB_DIST.resolve()
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        css = css_path.read_text()
        if prefix:
            for asset_dir in ("/fonts/", "/fonts-terminal/", "/ds-assets/", "/assets/"):
                css = css.replace(f"url({asset_dir}", f"url({prefix}{asset_dir}")
                css = css.replace(f"url(\"{asset_dir}", f"url(\"{prefix}{asset_dir}")
                css = css.replace(f"url('{asset_dir}", f"url('{prefix}{asset_dir}")
        return Response(content=css, media_type="text/css")

    application.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str, request: Request):
        # Don't catch API routes — let the main app handle them
        if full_path.startswith("api/"):
            return JSONResponse({"error": "Not found"}, status_code=404)
        prefix = _normalise_prefix(request.headers.get("x-forwarded-prefix"))
        file_path = WEB_DIST / full_path
        # Prevent path traversal via url-encoded sequences (%2e%2e/)
        if (
            full_path
            and file_path.resolve().is_relative_to(WEB_DIST.resolve())
            and file_path.exists()
            and file_path.is_file()
        ):
            return FileResponse(file_path)
        return _serve_index(prefix)


# ---------------------------------------------------------------------------
# Dashboard theme endpoints
# ---------------------------------------------------------------------------

# Built-in dashboard themes — label + description only.  The actual color
# definitions live in the frontend (web/src/themes/presets.ts).
_BUILTIN_DASHBOARD_THEMES = [
    {"name": "default",       "label": "Vermes Teal",         "description": "Classic dark teal — the canonical Vermes look"},
    {"name": "default-large", "label": "Vermes Teal (Large)", "description": "Vermes Teal with bigger fonts and roomier spacing"},
    {"name": "midnight",      "label": "Midnight",            "description": "Deep blue-violet with cool accents"},
    {"name": "ember",     "label": "Ember",          "description": "Warm crimson and bronze — forge vibes"},
    {"name": "mono",      "label": "Mono",           "description": "Clean grayscale — minimal and focused"},
    {"name": "cyberpunk", "label": "Cyberpunk",      "description": "Neon green on black — matrix terminal"},
    {"name": "rose",      "label": "Rosé",           "description": "Soft pink and warm ivory — easy on the eyes"},
]


def _parse_theme_layer(value: Any, default_hex: str, default_alpha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Normalise a theme layer spec from YAML into `{hex, alpha}` form.

    Accepts shorthand (a bare hex string) or full dict form.  Returns
    ``None`` on garbage input so the caller can fall back to a built-in
    default rather than blowing up.
    """
    if value is None:
        return {"hex": default_hex, "alpha": default_alpha}
    if isinstance(value, str):
        return {"hex": value, "alpha": default_alpha}
    if isinstance(value, dict):
        hex_val = value.get("hex", default_hex)
        alpha_val = value.get("alpha", default_alpha)
        if not isinstance(hex_val, str):
            return None
        try:
            alpha_f = float(alpha_val)
        except (TypeError, ValueError):
            alpha_f = default_alpha
        return {"hex": hex_val, "alpha": max(0.0, min(1.0, alpha_f))}
    return None


_THEME_DEFAULT_TYPOGRAPHY: Dict[str, str] = {
    "fontSans": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    "fontMono": 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace',
    "baseSize": "15px",
    "lineHeight": "1.55",
    "letterSpacing": "0",
}

_THEME_DEFAULT_LAYOUT: Dict[str, str] = {
    "radius": "0.5rem",
    "density": "comfortable",
}

_THEME_OVERRIDE_KEYS = {
    "card", "cardForeground", "popover", "popoverForeground",
    "primary", "primaryForeground", "secondary", "secondaryForeground",
    "muted", "mutedForeground", "accent", "accentForeground",
    "destructive", "destructiveForeground", "success", "warning",
    "border", "input", "ring",
}

# Well-known named asset slots themes can populate.  Any other keys under
# ``assets.custom`` are exposed as ``--theme-asset-custom-<key>`` CSS vars
# for plugin/shell use.
_THEME_NAMED_ASSET_KEYS = {"bg", "hero", "logo", "crest", "sidebar", "header"}

# Component-style buckets themes can override.  The value under each bucket
# is a mapping from camelCase property name to CSS string; each pair emits
# ``--component-<bucket>-<kebab-property>`` on :root.  The frontend's shell
# components (Card, App header, Backdrop, etc.) consume these vars so themes
# can restyle chrome (clip-path, border-image, segmented progress, etc.)
# without shipping their own CSS.
_THEME_COMPONENT_BUCKETS = {
    "card", "header", "footer", "sidebar", "tab",
    "progress", "badge", "backdrop", "page",
}

_THEME_LAYOUT_VARIANTS = {"standard", "cockpit", "tiled"}

# Cap on customCSS length so a malformed/oversized theme YAML can't blow up
# the response payload or the <style> tag.  32 KiB is plenty for every
# practical reskin (the Strike Freedom demo is ~2 KiB).
_THEME_CUSTOM_CSS_MAX = 32 * 1024


def _normalise_theme_definition(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a user theme YAML into the wire format `ThemeProvider`
    expects.  Returns ``None`` if the theme is unusable.

    Accepts both the full schema (palette/typography/layout) and a loose
    form with bare hex strings, so hand-written YAMLs stay friendly.
    """
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    # Palette
    palette_src = data.get("palette", {}) if isinstance(data.get("palette"), dict) else {}
    # Allow top-level `colors.background` as a shorthand too.
    colors_src = data.get("colors", {}) if isinstance(data.get("colors"), dict) else {}

    def _layer(key: str, default_hex: str, default_alpha: float = 1.0) -> Dict[str, Any]:
        spec = palette_src.get(key, colors_src.get(key))
        parsed = _parse_theme_layer(spec, default_hex, default_alpha)
        return parsed if parsed is not None else {"hex": default_hex, "alpha": default_alpha}

    palette = {
        "background": _layer("background", "#041c1c", 1.0),
        "midground": _layer("midground", "#ffe6cb", 1.0),
        "foreground": _layer("foreground", "#ffffff", 0.0),
        "warmGlow": palette_src.get("warmGlow") or data.get("warmGlow") or "rgba(255, 189, 56, 0.35)",
        "noiseOpacity": 1.0,
    }
    raw_noise = palette_src.get("noiseOpacity", data.get("noiseOpacity"))
    try:
        palette["noiseOpacity"] = float(raw_noise) if raw_noise is not None else 1.0
    except (TypeError, ValueError):
        palette["noiseOpacity"] = 1.0

    # Typography
    typo_src = data.get("typography", {}) if isinstance(data.get("typography"), dict) else {}
    typography = dict(_THEME_DEFAULT_TYPOGRAPHY)
    for key in ("fontSans", "fontMono", "fontDisplay", "fontUrl", "baseSize", "lineHeight", "letterSpacing"):
        val = typo_src.get(key)
        if isinstance(val, str) and val.strip():
            typography[key] = val

    # Layout
    layout_src = data.get("layout", {}) if isinstance(data.get("layout"), dict) else {}
    layout = dict(_THEME_DEFAULT_LAYOUT)
    radius = layout_src.get("radius")
    if isinstance(radius, str) and radius.strip():
        layout["radius"] = radius
    density = layout_src.get("density")
    if isinstance(density, str) and density in {"compact", "comfortable", "spacious"}:
        layout["density"] = density

    # Color overrides — keep only valid keys with string values.
    overrides_src = data.get("colorOverrides", {})
    color_overrides: Dict[str, str] = {}
    if isinstance(overrides_src, dict):
        for key, val in overrides_src.items():
            if key in _THEME_OVERRIDE_KEYS and isinstance(val, str) and val.strip():
                color_overrides[key] = val

    # Assets — named slots + arbitrary user-defined keys.  Values must be
    # strings (URLs or CSS ``url(...)``/``linear-gradient(...)`` expressions).
    # We don't fetch remote assets here; the frontend just injects them as
    # CSS vars.  Empty values are dropped so a theme can explicitly clear a
    # slot by setting ``hero: ""``.
    assets_out: Dict[str, Any] = {}
    assets_src = data.get("assets", {}) if isinstance(data.get("assets"), dict) else {}
    for key in _THEME_NAMED_ASSET_KEYS:
        val = assets_src.get(key)
        if isinstance(val, str) and val.strip():
            assets_out[key] = val
    custom_assets_src = assets_src.get("custom")
    if isinstance(custom_assets_src, dict):
        custom_assets: Dict[str, str] = {}
        for key, val in custom_assets_src.items():
            if (
                isinstance(key, str)
                and key.replace("-", "").replace("_", "").isalnum()
                and isinstance(val, str)
                and val.strip()
            ):
                custom_assets[key] = val
        if custom_assets:
            assets_out["custom"] = custom_assets

    # Custom CSS — raw CSS text the frontend injects as a scoped <style>
    # tag on theme apply.  Clipped to _THEME_CUSTOM_CSS_MAX to keep the
    # payload bounded.  We intentionally do NOT parse/sanitise the CSS
    # here — the dashboard is localhost-only and themes are user-authored
    # YAML in ~/.vermes/, same trust level as the config file itself.
    custom_css_val = data.get("customCSS")
    custom_css: Optional[str] = None
    if isinstance(custom_css_val, str) and custom_css_val.strip():
        custom_css = custom_css_val[:_THEME_CUSTOM_CSS_MAX]

    # Component style overrides — per-bucket dicts of camelCase CSS
    # property -> CSS string.  The frontend converts these into CSS vars
    # that shell components (Card, App header, Backdrop) consume.
    component_styles_src = data.get("componentStyles", {})
    component_styles: Dict[str, Dict[str, str]] = {}
    if isinstance(component_styles_src, dict):
        for bucket, props in component_styles_src.items():
            if bucket not in _THEME_COMPONENT_BUCKETS or not isinstance(props, dict):
                continue
            clean: Dict[str, str] = {}
            for prop, value in props.items():
                if (
                    isinstance(prop, str)
                    and prop.replace("-", "").replace("_", "").isalnum()
                    and isinstance(value, (str, int, float))
                    and str(value).strip()
                ):
                    clean[prop] = str(value)
            if clean:
                component_styles[bucket] = clean

    layout_variant_src = data.get("layoutVariant")
    layout_variant = (
        layout_variant_src
        if isinstance(layout_variant_src, str) and layout_variant_src in _THEME_LAYOUT_VARIANTS
        else "standard"
    )

    result: Dict[str, Any] = {
        "name": name,
        "label": data.get("label") or name,
        "description": data.get("description", ""),
        "palette": palette,
        "typography": typography,
        "layout": layout,
        "layoutVariant": layout_variant,
    }
    if color_overrides:
        result["colorOverrides"] = color_overrides
    if assets_out:
        result["assets"] = assets_out
    if custom_css is not None:
        result["customCSS"] = custom_css
    if component_styles:
        result["componentStyles"] = component_styles
    return result


def _discover_user_themes() -> list:
    """Scan ~/.vermes/dashboard-themes/*.yaml for user-created themes.

    Returns a list of fully-normalised theme definitions ready to ship
    to the frontend, so the client can apply them without a secondary
    round-trip or a built-in stub.
    """
    themes_dir = get_vermes_home() / "dashboard-themes"
    if not themes_dir.is_dir():
        return []
    result = []
    for f in sorted(themes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalised = _normalise_theme_definition(data)
        if normalised is not None:
            result.append(normalised)
    return result


async def get_dashboard_themes():
    """Return available themes and the currently active one.

    Built-in entries ship name/label/description only (the frontend owns
    their full definitions in `web/src/themes/presets.ts`).  User themes
    from `~/.vermes/dashboard-themes/*.yaml` ship with their full
    normalised definition under `definition`, so the client can apply
    them without a stub.
    """
    config = load_config()
    active = cfg_get(config, "dashboard", "theme", default="default")
    user_themes = _discover_user_themes()
    seen = set()
    themes = []
    for t in _BUILTIN_DASHBOARD_THEMES:
        seen.add(t["name"])
        themes.append(t)
    for t in user_themes:
        if t["name"] in seen:
            continue
        themes.append({
            "name": t["name"],
            "label": t["label"],
            "description": t["description"],
            "definition": t,
        })
        seen.add(t["name"])
    return {"themes": themes, "active": active}


class ThemeSetBody(BaseModel):
    name: str


async def set_dashboard_theme(body: ThemeSetBody):
    """Set the active dashboard theme (persists to config.yaml)."""
    config = load_config()
    if "dashboard" not in config:
        config["dashboard"] = {}
    config["dashboard"]["theme"] = body.name
    save_config(config)
    return {"ok": True, "theme": body.name}


# ---------------------------------------------------------------------------
# Dashboard plugin system
# ---------------------------------------------------------------------------

def _discover_dashboard_plugins() -> list:
    """Scan plugins/*/dashboard/manifest.json for dashboard extensions.

    Checks three plugin sources (same as vermes_cli.plugins):
    1. User plugins:    ~/.vermes/plugins/<name>/dashboard/manifest.json
    2. Bundled plugins: <repo>/plugins/<name>/dashboard/manifest.json  (memory/, etc.)
    3. Project plugins: ./.vermes/plugins/  (only if VERMES_ENABLE_PROJECT_PLUGINS)
    """
    plugins = []
    seen_names: set = set()

    from vermes_cli.plugins import get_bundled_plugins_dir
    bundled_root = get_bundled_plugins_dir()
    search_dirs = [
        (get_vermes_home() / "plugins", "user"),
        (bundled_root / "memory", "bundled"),
        (bundled_root, "bundled"),
    ]
    if os.environ.get("VERMES_ENABLE_PROJECT_PLUGINS"):
        search_dirs.append((Path.cwd() / ".vermes" / "plugins", "project"))

    for plugins_root, source in search_dirs:
        if not plugins_root.is_dir():
            continue
        for child in sorted(plugins_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "dashboard" / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                name = data.get("name", child.name)
                if name in seen_names:
                    continue
                seen_names.add(name)
                # Tab options: ``path`` + ``position`` for a new tab, optional
                # ``override`` to replace a built-in route, and ``hidden`` to
                # register the plugin component/slots without adding a tab
                # (useful for slot-only plugins like a header-crest injector).
                raw_tab = data.get("tab", {}) if isinstance(data.get("tab"), dict) else {}
                tab_info = {
                    "path": raw_tab.get("path", f"/{name}"),
                    "position": raw_tab.get("position", "end"),
                }
                override_path = raw_tab.get("override")
                if isinstance(override_path, str) and override_path.startswith("/"):
                    tab_info["override"] = override_path
                if bool(raw_tab.get("hidden")):
                    tab_info["hidden"] = True
                # Slots: list of named slot locations this plugin populates.
                # The frontend exposes ``registerSlot(pluginName, slotName, Component)``
                # on window; plugins with non-empty slots call it from their JS bundle.
                slots_src = data.get("slots")
                slots: List[str] = []
                if isinstance(slots_src, list):
                    slots = [s for s in slots_src if isinstance(s, str) and s]
                plugins.append({
                    "name": name,
                    "label": data.get("label", name),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", "Puzzle"),
                    "version": data.get("version", "0.0.0"),
                    "tab": tab_info,
                    "slots": slots,
                    "entry": data.get("entry", "dist/index.js"),
                    "css": data.get("css"),
                    "has_api": bool(data.get("api")),
                    "source": source,
                    "_dir": str(child / "dashboard"),
                    "_api_file": data.get("api"),
                })
            except Exception as exc:
                _log.warning("Bad dashboard plugin manifest %s: %s", manifest_file, exc)
                continue
    return plugins


# Cache discovered plugins per-process (refresh on explicit re-scan).
_dashboard_plugins_cache: Optional[list] = None


def _get_dashboard_plugins(force_rescan: bool = False) -> list:
    global _dashboard_plugins_cache
    if _dashboard_plugins_cache is None or force_rescan:
        _dashboard_plugins_cache = _discover_dashboard_plugins()
    elif _dashboard_plugins_cache:
        if any(not Path(p["_dir"]).is_dir() for p in _dashboard_plugins_cache):
            _dashboard_plugins_cache = _discover_dashboard_plugins()
    return _dashboard_plugins_cache


async def get_dashboard_plugins():
    """Return discovered dashboard plugins (excludes user-hidden ones)."""
    plugins = _get_dashboard_plugins()
    # Read user's hidden plugins list from config.
    config = load_config()
    hidden: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []
    # Strip internal fields before sending to frontend and filter out hidden.
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in plugins
        if p["name"] not in hidden
    ]


async def rescan_dashboard_plugins():
    """Force re-scan of dashboard plugins."""
    plugins = _get_dashboard_plugins(force_rescan=True)
    return {"ok": True, "count": len(plugins)}


class _AgentPluginInstallBody(BaseModel):
    identifier: str
    force: bool = False
    enable: bool = True


def _strip_dashboard_manifest(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in p.items() if not k.startswith("_")}


def _merged_plugins_hub() -> Dict[str, Any]:
    """Agent discovery + dashboard manifests + optional provider picker metadata."""
    from vermes_cli.plugins_cmd import (
        _discover_all_plugins,
        _get_current_context_engine,
        _get_current_memory_provider,
        _discover_context_engines,
        _discover_memory_providers,
        _get_disabled_set,
        _get_enabled_set,
        _read_manifest as _read_plugin_manifest_at,
    )

    dashboard_list = _get_dashboard_plugins()
    dash_by_name = {str(p["name"]): p for p in dashboard_list}

    disabled_set = _get_disabled_set()
    enabled_set = _get_enabled_set()

    # Read user-hidden plugins from config for the user_hidden field.
    config = load_config()
    hidden_plugins: list = cfg_get(config, "dashboard", "hidden_plugins", default=[]) or []

    plugins_root_resolved = (get_vermes_home() / "plugins").resolve()
    rows: List[Dict[str, Any]] = []

    for name, version, description, source, dir_str in _discover_all_plugins():
        if name in disabled_set:
            runtime_status = "disabled"
        elif name in enabled_set:
            runtime_status = "enabled"
        else:
            runtime_status = "inactive"

        dir_path = Path(dir_str)
        dm = dash_by_name.get(name)
        has_dash_manifest = dm is not None or (dir_path / "dashboard" / "manifest.json").exists()

        under_user_tree = False
        try:
            dir_path.resolve().relative_to(plugins_root_resolved)
            under_user_tree = True
        except ValueError:
            pass

        can_remove_update = (
            source in {"user", "git"} and under_user_tree and Path(dir_str).is_dir()
        )

        # Check if this plugin provides tools that require auth
        auth_required = False
        auth_command = ""
        manifest_data = _read_plugin_manifest_at(dir_path)
        provides_tools = manifest_data.get("provides_tools") or []
        if provides_tools:
            try:
                from tools.registry import registry
                for tname in provides_tools:
                    entry = registry.get_entry(tname)
                    if entry and entry.check_fn and not entry.check_fn():
                        auth_required = True
                        auth_command = f"vermes auth {name}"
                        break
            except Exception:
                pass

        rows.append({
            "name": name,
            "version": version or "",
            "description": description or "",
            "source": source,
            "runtime_status": runtime_status,
            "has_dashboard_manifest": has_dash_manifest,
            "dashboard_manifest": _strip_dashboard_manifest(dm) if dm else None,
            "path": dir_str,
            "can_remove": can_remove_update,
            "can_update_git": can_remove_update and (Path(dir_str) / ".git").exists(),
            "auth_required": auth_required,
            "auth_command": auth_command,
            "user_hidden": name in hidden_plugins,
        })

    agent_names = {r["name"] for r in rows}
    orphan_dashboard = [
        _strip_dashboard_manifest(p)
        for p in dashboard_list
        if str(p["name"]) not in agent_names
    ]

    memory_providers: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_memory_providers():
            memory_providers.append({"name": n, "description": desc})
    except Exception:
        memory_providers = []

    context_engines: List[Dict[str, str]] = []
    try:
        for n, desc in _discover_context_engines():
            context_engines.append({"name": n, "description": desc})
    except Exception:
        context_engines = []

    return {
        "plugins": rows,
        "orphan_dashboard_plugins": orphan_dashboard,
        "providers": {
            "memory_provider": _get_current_memory_provider() or "",
            "memory_options": memory_providers,
            "context_engine": _get_current_context_engine(),
            "context_options": context_engines,
        },
    }


async def get_plugins_hub(request: Request):
    """Unified agent plugins + dashboard extension metadata (session protected)."""
    _require_token(request)
    try:
        return _merged_plugins_hub()
    except Exception as exc:
        _log.warning("plugins/hub failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to build plugins hub.") from exc


async def post_agent_plugin_install(request: Request, body: _AgentPluginInstallBody):
    _require_token(request)
    from vermes_cli.plugins_cmd import dashboard_install_plugin

    result = dashboard_install_plugin(
        body.identifier.strip(),
        force=body.force,
        enable=body.enable,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "Install failed.",
        )
    _get_dashboard_plugins(force_rescan=True)
    # Strip internal paths from the response
    result.pop("after_install_path", None)
    return result


def _validate_plugin_name(name: str) -> str:
    """Reject path-traversal attempts in plugin name URL parameters."""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid plugin name.")
    return name


async def post_agent_plugin_enable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from vermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Enable failed.")
    return result


async def post_agent_plugin_disable(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from vermes_cli.plugins_cmd import dashboard_set_agent_plugin_enabled

    result = dashboard_set_agent_plugin_enabled(name, enabled=False)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Disable failed.")
    return result


async def post_agent_plugin_update(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from vermes_cli.plugins_cmd import dashboard_update_user_plugin

    result = dashboard_update_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Update failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


async def delete_agent_plugin(request: Request, name: str):
    _require_token(request)
    name = _validate_plugin_name(name)
    from vermes_cli.plugins_cmd import dashboard_remove_user_plugin

    result = dashboard_remove_user_plugin(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "Remove failed.")
    _get_dashboard_plugins(force_rescan=True)
    return result


class _PluginProvidersPutBody(BaseModel):
    memory_provider: Optional[str] = None
    context_engine: Optional[str] = None


async def put_plugin_providers(request: Request, body: _PluginProvidersPutBody):
    """Persist memory provider / context engine selection (writes config.yaml)."""
    _require_token(request)
    from vermes_cli.plugins_cmd import (
        _save_context_engine,
        _save_memory_provider,
    )

    if body.memory_provider is not None:
        _save_memory_provider(body.memory_provider)
    if body.context_engine is not None:
        _save_context_engine(body.context_engine)
    return {"ok": True}


class _PluginVisibilityBody(BaseModel):
    hidden: bool


async def post_plugin_visibility(request: Request, name: str, body: _PluginVisibilityBody):
    """Toggle a plugin's sidebar visibility (persists to config.yaml dashboard.hidden_plugins)."""
    _require_token(request)
    name = _validate_plugin_name(name)

    config = load_config()
    if "dashboard" not in config or not isinstance(config.get("dashboard"), dict):
        config["dashboard"] = {}
    hidden_list: list = config["dashboard"].get("hidden_plugins") or []
    if not isinstance(hidden_list, list):
        hidden_list = []

    if body.hidden and name not in hidden_list:
        hidden_list.append(name)
    elif not body.hidden and name in hidden_list:
        hidden_list.remove(name)

    config["dashboard"]["hidden_plugins"] = hidden_list
    save_config(config)
    return {"ok": True, "name": name, "hidden": body.hidden}


async def serve_plugin_asset(plugin_name: str, file_path: str):
    """Serve static assets from a dashboard plugin directory.

    Only serves files from the plugin's ``dashboard/`` subdirectory.
    Path traversal is blocked by checking ``resolve().is_relative_to()``.
    """
    plugins = _get_dashboard_plugins()
    plugin = next((p for p in plugins if p["name"] == plugin_name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    base = Path(plugin["_dir"])
    target = (base / file_path).resolve()

    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Guess content type
    suffix = target.suffix.lower()
    content_types = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }
    media_type = content_types.get(suffix, "application/octet-stream")
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


def _mount_plugin_api_routes():
    """Import and mount backend API routes from plugins that declare them.

    Each plugin's ``api`` field points to a Python file that must expose
    a ``router`` (FastAPI APIRouter).  Routes are mounted under
    ``/api/plugins/<name>/``.
    """
    for plugin in _get_dashboard_plugins():
        api_file_name = plugin.get("_api_file")
        if not api_file_name:
            continue
        api_path = Path(plugin["_dir"]) / api_file_name
        if not api_path.exists():
            _log.warning("Plugin %s declares api=%s but file not found", plugin["name"], api_file_name)
            continue
        try:
            module_name = f"VERMES_dashboard_plugin_{plugin['name']}"
            spec = importlib.util.spec_from_file_location(module_name, api_path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec_module so pydantic/FastAPI
            # can resolve forward references (e.g. models defined in a file
            # that uses `from __future__ import annotations`). Without this,
            # TypeAdapter lazy-build fails at first request with
            # "is not fully defined" because the module namespace isn't
            # reachable by name for string-annotation resolution.
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            router = getattr(mod, "router", None)
            if router is None:
                _log.warning("Plugin %s api file has no 'router' attribute", plugin["name"])
                continue
            app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
            _log.info("Mounted plugin API routes: /api/plugins/%s/", plugin["name"])
        except Exception as exc:
            _log.warning("Failed to load plugin %s API routes: %s", plugin["name"], exc)


# Mount plugin API routes before the SPA catch-all.
_mount_plugin_api_routes()

def _find_available_port(host: str, start_port: int, max_tries: int = 100) -> int:
    """Find an available port starting from start_port."""
    import socket
    for p in range(start_port, start_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.bind((host, p))
            return p
        except OSError:
            continue
    raise SystemExit(f"No available port found in range {start_port}-{start_port + max_tries - 1}")


def start_server(
    host: str = "127.0.0.1",
    port: int = 9119,
    open_browser: bool = True,
    allow_public: bool = False,
    *,
    embedded_chat: bool = False,
):
    """Start the web UI server."""
    import uvicorn

    # Phase 0: stash the auth-gate flag on app.state so middleware / SPA-token
    # injection / WS-auth paths can branch on it consistently.  Phase 3.5
    # uses this to decide whether to refuse the bind, log the gate-on
    # banner, and enable uvicorn proxy_headers.
    app.state.auth_required = should_require_auth(host)

    # ``--insecure`` no longer disables the auth gate (June 2026 hardening:
    # the Vermes-0day MCP-persistence campaign abused unauthenticated public
    # dashboards). If a caller still passes it, warn that it is now a no-op
    # rather than silently changing their expectation of an open bind.
    if allow_public and host not in _LOOPBACK_HOST_VALUES:
        _log.warning(
            "--insecure no longer bypasses dashboard authentication. A "
            "non-loopback bind (%s) now ALWAYS requires an auth provider "
            "(OAuth or the bundled password provider). Configure one — see "
            "below — or bind to 127.0.0.1 and reach it over an SSH tunnel / "
            "Tailscale.", host,
        )

    if app.state.auth_required:
        # The gate engages on every non-loopback bind. Require at least one
        # provider to be registered, else fail closed — there is no longer an
        # escape hatch that serves the dashboard without authentication.
        from vermes_cli.dashboard_auth import list_providers
        if not list_providers():
            # Surface the *specific* reason any bundled provider declined
            # to register (e.g. missing VERMES_DASHBOARD_OAUTH_CLIENT_ID).
            # Each provider plugin that ships with Vermes Agent exposes a
            # module-level ``LAST_SKIP_REASON`` string for this purpose;
            # without it the operator would only see "no providers" which
            # is misleading when the provider IS installed but unconfigured.
            skip_reasons: list[str] = []
            try:
                from plugins.dashboard_auth import nous as _nous_plugin

                if _nous_plugin.LAST_SKIP_REASON:
                    skip_reasons.append(
                        f"  • nous: {_nous_plugin.LAST_SKIP_REASON}"
                    )
            except Exception:
                pass

            _fix_hint = (
                "Configure an auth provider before exposing the dashboard:\n"
                "  • Password: set dashboard_auth.basic.username + "
                "password_hash in config.yaml\n"
                "    (hash with: python -c \"from "
                "plugins.dashboard_auth.basic import hash_password; "
                "print(hash_password('your-password'))\")\n"
                "  • OAuth: run `Vermes dashboard register` (Nous Portal) or "
                "install a DashboardAuthProvider plugin.\n"
                "There is no unauthenticated public-bind option — to keep it "
                "local, bind 127.0.0.1 and tunnel in (SSH / Tailscale)."
            )
            if skip_reasons:
                raise SystemExit(
                    f"Refusing to bind dashboard to {host} — the auth gate "
                    f"engages on non-loopback binds, but no auth providers "
                    f"are registered.\n\n"
                    f"Bundled providers reported these issues:\n"
                    + "\n".join(skip_reasons)
                    + "\n\n"
                    + _fix_hint
                )
            raise SystemExit(
                f"Refusing to bind dashboard to {host} — the auth gate "
                f"engages on non-loopback binds, but no auth providers are "
                f"registered.\n\n" + _fix_hint
            )
        _log.info(
            "Dashboard binding to %s with auth gate enabled. Providers: %s",
            host,
            ", ".join(p.name for p in list_providers()),
        )

    # Record the bound host so host_header_middleware can validate incoming
    # Host headers against it. Defends against DNS rebinding (GHSA-ppp5-vxwm-4cf7).
    # bound_port is also stashed so /api/pty can build the back-WS URL the
    # PTY child uses to publish events to the dashboard sidebar.
    app.state.bound_host = host
    app.state.bound_port = port

    if open_browser:
        import webbrowser

        # On headless Linux (no DISPLAY or WAYLAND_DISPLAY) some registered
        # browsers are TUI programs (links, lynx, www-browser) that try to
        # take over the terminal.  That can send SIGHUP to the server process
        # and cause an immediate exit even though uvicorn bound successfully.
        # Skip the auto-open attempt on headless systems and let the user
        # open the URL manually.  macOS and Windows are always considered
        # display-capable.
        _has_display = (
            sys.platform != "linux"
            or bool(os.environ.get("DISPLAY"))
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )

        if _has_display:
            def _open():
                try:
                    time.sleep(1.0)
                    webbrowser.open(f"http://{host}:{port}")
                except Exception:
                    pass

            threading.Thread(target=_open, daemon=True).start()
        else:
            _log.debug(
                "Skipping browser-open: no DISPLAY or WAYLAND_DISPLAY detected "
                "(headless Linux). Pass --no-open to suppress this detection."
            )

    logger.info(f"  Vermes Web UI → http://{host}:{port}")
    # proxy_headers=False so _ws_client_is_allowed sees the real connection peer
    # rather than X-Forwarded-For's rewritten value (which would defeat the
    # loopback gate when behind a reverse proxy).
    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=False)

# --- Trial Token wrapper (delegates to blueprints.quota) ---

async def claim_trial_token_wrapper(wechat_openid: str) -> dict:
    """Internal: Claim trial token — delegates to quota blueprint."""
    from vermes_cli.blueprints.quota import _claim_trial_token
    return await _claim_trial_token(wechat_openid)

async def discover_models():
    """Scan local Ollama for available models."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                return {"ok": False, "error": "Ollama not running on localhost:11434"}
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Mount SPA sub-app LAST so API routes take priority over its catch-all
# ─────────────────────────────────────────────────────────────────
# Blueprint 注册（功能域路由模块化）
# 函数已全部定义完毕，导入 blueprints 不会产生循环依赖问题
# ─────────────────────────────────────────────────────────────────
# 注意：所有已迁移路由的 @app 装饰器已移除
# Blueprint 中的 register_to(app) 函数负责向 app 注册路由
from vermes_cli import blueprints

# 批量调用各 Blueprint 的 register_to（位于各 blueprint/*.py 文件中）
blueprints.chat.register_to(app)
blueprints.quota.register_to(app)
blueprints.wechat.register_to(app)
blueprints.models.register_to(app)
blueprints.config.register_to(app)
blueprints.providers.register_to(app)
blueprints.dashboard.register_to(app)
blueprints.session.register_to(app)
blueprints.cron_jobs.register_to(app)
blueprints.storage.register_to(app)
blueprints.analytics.register_to(app)
blueprints.skills_tools.register_to(app)
blueprints.update.register_to(app)
blueprints.status.register_to(app)
blueprints.gateway_channels.register_to(app)
blueprints.profiles.register_to(app)
blueprints.oauth.register_to(app)

# ── 生态模块加载器（替代硬编码 ScholarForge 注册） ──
# 必须在 mount_spa() 之前注册，否则 SPA catch-all 会拦截 /api/modules 路由
try:
    from agent.module_loader import register_modules, register_module_api, HostAPI, _set_app_ref
    _host_api = HostAPI()
    _set_app_ref(app, _host_api)
    _registered_modules = register_modules(app, _host_api)
    register_module_api(app, _host_api)
    # 启动模块文件 watcher：作为 patch/write_file 等绕过 self_modify 的安全网，
    # 任何工具改了 ~/.vermes/modules/ 下的文件都会兜底热重载。
    from agent.module_loader import start_module_watcher
    start_module_watcher()
    # 启动 prompt processor watcher：~/.vermes/processors/ 下 YAML 变更
    # 自动失效缓存，下次 build_system_prompt 拾取新内容
    try:
        from agent.prompt_processor_loader import start_processor_watcher
        start_processor_watcher()
    except Exception as e:
        logger.warning("[ProcessorWatcher] failed to start: %s", e)
    if _registered_modules:
        logger.info("[Modules] Loaded %d module(s): %s", len(_registered_modules), [m.name for m in _registered_modules])
    else:
        logger.info("[Modules] No modules installed")
except Exception as e:
    logger.warning("[Modules] Module loader failed: %s", e)
    import traceback; traceback.print_exc()

# ── /health 端点：Electron 后端就绪检测 + G1/G5 完整性透传 ──
@app.get("/health")
async def health_check():
    """Health endpoint for Electron splash screen backend detection.

    G4: carries the G1 integrity verdict and the G5 profile-mismatch flag so
    main.js can branch the splash (db_corrupt/missing_with_profile → hard
    block; profile_mismatch → banner).  ``status`` stays "ok" whenever the
    process is alive — backward compatible with the current resp.ok-only
    polling in main.js; the splash branching lands in c3.
    """
    from vermes_cli import __version__

    integrity: Dict[str, Any] = {"state_db": "probing", "profile_mismatch": False, "detail": ""}
    try:
        import vermes_state as _hs
        _status = _hs.get_integrity_status()
        if _status is not None:
            integrity["state_db"] = _status.get("state_db", "probing")
            integrity["detail"] = _status.get("detail", "")
            integrity["db_path"] = _status.get("db_path", "")
        elif isinstance(_startup_integrity, dict):
            integrity["state_db"] = _startup_integrity.get("state_db", "probing")
            integrity["detail"] = _startup_integrity.get("detail", "")
        integrity["lockdown"] = _hs.is_integrity_lockdown()
    except Exception:
        pass
    try:
        from vermes_constants import get_profile_fallback_active
        integrity["profile_mismatch"] = get_profile_fallback_active()
    except Exception:
        pass

    # Bug B: 暴露自动回滚信息让前端显示横幅
    rolled_back = None
    try:
        import tempfile as _tf, os as _os
        _flag = _os.path.join(_tf.gettempdir(), "vermes-rolled-back.flag")
        if _os.path.exists(_flag):
            with open(_flag) as _rf:
                rolled_back = _rf.read().strip() or None
    except Exception:
        pass

    return {"status": "ok", "version": __version__, "integrity": integrity, "rolled_back": rolled_back}


@app.get("/api/session-token")
async def session_token_refresh():
    """Return current session token. No auth required — used by frontend
    to refresh token after server restart. Only accessible from localhost."""
    return {"token": _SESSION_TOKEN}


# ── /api/v1/metrics：Route D 可观测性端点 ──
@app.get("/api/v1/metrics")
async def prometheus_metrics():
    """Prometheus-format metrics for agent runtime observability.

    Returns text/plain metrics in Prometheus exposition format.
    Zero external dependencies, zero upload — purely local.
    """
    from agent.metrics import render_prometheus
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ── mfgcad 3D 文件服务 ──────────────────────────────────
# 安全地服务 ~/.vermes/mfgcad/output/ 下的模型文件给前端 WebGL 视图器。
# 路径限制：只允许 session_id 目录下的已知扩展名文件。

_MFGCAD_ALLOWED_EXT = {".stl", ".glb", ".gltf", ".3mf", ".png", ".jpg", ".jpeg", ".step", ".stp"}
_MFGCAD_OUTPUT_DIR = Path.home() / ".vermes" / "mfgcad" / "output"


@app.get("/api/mfgcad/files/{session_id}/{filename}")
async def serve_mfgcad_file(session_id: str, filename: str):
    """提供 mfgcad 生成的 3D 模型文件给前端查看器。

    安全约束：
    - session_id 只允许字母数字下划线连字符
    - filename 只允许字母数字下划线连字符点
    - 扩展名白名单（stl/glb/gltf/3mf/png/jpg）
    - 路径穿越检测（resolve 后必须在 output_dir 下）
    """
    import re
    from fastapi.responses import FileResponse

    # 输入校验
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return {"error": "invalid session_id"}, 400
    if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
        return {"error": "invalid filename"}, 400

    ext = Path(filename).suffix.lower()
    if ext not in _MFGCAD_ALLOWED_EXT:
        return {"error": f"extension {ext} not allowed"}, 400

    file_path = (_MFGCAD_OUTPUT_DIR / session_id / filename).resolve()
    # 路径穿越防护
    try:
        file_path.relative_to(_MFGCAD_OUTPUT_DIR.resolve())
    except ValueError:
        return {"error": "path traversal denied"}, 403

    if not file_path.is_file():
        return {"error": "file not found"}, 404

    # MIME 类型
    mime_map = {
        ".stl": "model/stl",
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".3mf": "model/3mf",
        ".step": "application/STEP",
        ".stp": "application/STEP",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }
    return FileResponse(str(file_path), media_type=mime_map.get(ext, "application/octet-stream"))


@app.get("/api/mfgcad/sessions")
async def list_mfgcad_sessions():
    """列出所有 mfgcad 设计会话及其生成文件。

    自动扫描 output 目录补齐旧 session 缺失的 stl/step 路径，
    返回前端可用的 /api/mfgcad/files/{sid}/{filename} URL。
    """
    import json
    from fastapi.responses import JSONResponse

    sessions = []
    sess_dir = Path.home() / ".vermes" / "mfgcad" / "sessions"
    output_dir = Path.home() / ".vermes" / "mfgcad" / "output"
    if not sess_dir.is_dir():
        return JSONResponse({"sessions": []})

    for sf in sorted(sess_dir.glob("*/session.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            sid = data.get("session_id", sf.parent.name)

            # 自动扫描 output/<sid>/ 补齐缺失文件
            sess_out = output_dir / sid
            if sess_out.is_dir():
                for ext, key in [(".step", "step_path"), (".stl", "stl_path"), (".3mf", "stl_3mf_path")]:
                    if not data.get(key):
                        files = sorted(sess_out.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
                        if files:
                            data[key] = str(files[0])

            # 自动检测参数化能力：从 output 目录的源码抽取参数
            if not data.get("has_parameters"):
                from vermes_cli.mfgcad.parametric import extract_parameters
                sess_out = output_dir / sid
                if sess_out.is_dir():
                    # 找 build123d 源码
                    py_files = list(sess_out.glob("*.py"))
                    for pf in py_files:
                        try:
                            code = pf.read_text(encoding="utf-8")
                            params = extract_parameters(code)
                            if params:
                                data["has_parameters"] = True
                                data["_auto_params"] = {k: v for k, v in params.items()}
                                data["_auto_source"] = str(pf)
                                break
                        except Exception:
                            continue

            # 把绝对路径转为前端可用的 URL
            def _to_url(abs_path):
                if not abs_path:
                    return None
                p = Path(abs_path)
                try:
                    rel = p.relative_to(output_dir / sid)
                    return f"/api/mfgcad/files/{sid}/{rel.name}"
                except ValueError:
                    pass
                return None

            files_url = {}
            for k, v in {
                "step": data.get("step_path"),
                "stl": data.get("stl_path"),
                "3mf": data.get("stl_3mf_path"),
                "glb": data.get("glb_path"),
                "preview": data.get("preview_path"),
            }.items():
                url = _to_url(v)
                if url:
                    files_url[k] = url

            sessions.append({
                "session_id": sid,
                "request": data.get("request", ""),
                "backend": data.get("backend", "mac"),
                "ok": data.get("ok", False),
                "files": files_url,
                "volume_mm3": data.get("volume_mm3"),
                "qa": data.get("qa", {}),
                "ts": data.get("ts", 0),
                "has_parameters": data.get("has_parameters", False),
                "build123d_source": data.get("build123d_source"),
            })
        except Exception:
            continue

    return JSONResponse({"sessions": sessions})


@app.get("/api/mfgcad/sessions/{session_id}/parameters")
async def get_mfgcad_parameters(session_id: str):
    """返回会话的可调参数（供前端渲染滑块：value/min/max/step/unit）。"""
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    from vermes_cli.mfgcad.parametric import (
        load_parameters,
        load_source,
        acquire_source,
        extract_parameters,
    )

    params = load_parameters(session_id)
    if not params:
        # parameters.json 可能未生成（引擎未落源码）→ 退而从源码现抽
        src = load_source(session_id) or acquire_source(
            session_id, str(_MFGCAD_OUTPUT_DIR / session_id)
        )
        if src:
            params = extract_parameters(src)

    return JSONResponse({
        "session_id": session_id,
        "has_parameters": bool(params),
        "parameters": params,
    })


@app.post("/api/mfgcad/sessions/{session_id}/rebuild")
async def rebuild_mfgcad_parametric(session_id: str, request):
    """参数化重建：用新参数重建源会话，返回新建的子会话。

    body: {"parameters": {"HEIGHT": 120.0, "HOLE_COUNT": 12}}
    """
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)

    params = (body or {}).get("parameters")
    if not isinstance(params, dict) or not params:
        return JSONResponse({"error": "missing parameters object"}, status_code=400)

    from vermes_cli.mfgcad import tools as mfgcad_tools

    message = await mfgcad_tools._handle_mfg_rebuild_parametric({
        "base_session_id": session_id,
        "parameters": params,
    })

    # 读取本次新建的子会话（base_session_id 指向源会话、ts 最大者）
    sess_root = Path.home() / ".vermes" / "mfgcad" / "sessions"
    child = None
    child_ts = -1
    if sess_root.is_dir():
        for sf in sess_root.glob("*/session.json"):
            try:
                d = json.loads(sf.read_text(encoding="utf-8"))
            except Exception:
                continue
            if d.get("base_session_id") == session_id:
                ts = d.get("ts", 0)
                if ts > child_ts:
                    child_ts = ts
                    child = d

    return JSONResponse({
        "message": message,
        "child_session": child,
    })


@app.post("/api/mfgcad/upload")
async def upload_mfgcad_file(request: Request):
    """上传用户自有 STEP/STL/3MF 文件，创建新会话。"""
    from fastapi.responses import JSONResponse
    import time
    import shutil
    import re

    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return JSONResponse({"error": "expected multipart/form-data"}, status_code=400)

    form = await request.form()
    upload_file = form.get("file")
    name = (form.get("name") or "uploaded").strip()

    if not upload_file or not hasattr(upload_file, "filename"):
        return JSONResponse({"error": "no file uploaded"}, status_code=400)

    filename = upload_file.filename
    ext = Path(filename).suffix.lower()
    if ext not in {".step", ".stp", ".stl", ".3mf"}:
        return JSONResponse({"error": f"unsupported format: {ext}"}, status_code=400)

    # 创建会话
    session_id = f"upload_{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_-]', '', name)[:20]}"
    output_dir = Path.home() / ".vermes" / "mfgcad" / "output" / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # 保存文件
    safe_name = f"model{ext}"
    file_path = output_dir / safe_name
    with open(file_path, "wb") as f:
        content = await upload_file.read()
        f.write(content)

    # 如果是 STEP，尝试生成 STL 预览
    stl_path = None
    if ext in (".step", ".stp"):
        stl_name = "model.stl"
        # 尝试用 trimesh 转换（如果有）
        try:
            import subprocess
            venv_python = str(Path.home() / ".vermes" / "engines" / "mac" / ".venv" / "bin" / "python")
            if not Path(venv_python).exists():
                venv_python = "python3"
            script = f'''
import sys
try:
    import trimesh
    mesh = trimesh.load("{file_path}")
    if hasattr(mesh, "export"):
        mesh.export("{output_dir / stl_name}")
        print("OK")
    else:
        print("SKIP")
except Exception as e:
    print(f"ERR: {{e}}")
'''
            result = subprocess.run([venv_python, "-c", script], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and "OK" in result.stdout:
                stl_path = str(output_dir / stl_name)
        except Exception:
            pass

    # 创建 session.json
    session_data = {
        "session_id": session_id,
        "request": f"\U0001f4c2 打开文件: {filename}",
        "backend": "upload",
        "ok": True,
        "step_path": str(file_path) if ext in (".step", ".stp") else None,
        "stl_path": stl_path or (str(file_path) if ext == ".stl" else None),
        "stl_3mf_path": str(file_path) if ext == ".3mf" else None,
        "volume_mm3": None,
        "qa": {"passed": 0, "failed": 0, "issues": []},
        "has_parameters": False,
        "ts": int(time.time()),
    }

    sess_dir = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    (sess_dir / "session.json").write_text(json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return JSONResponse({
        "session_id": session_id,
        "message": f"\u2705 {filename} \u5df2\u5bfc\u5165",
        "files": {
            "step": f"/api/mfgcad/files/{session_id}/{safe_name}" if ext in (".step", ".stp") else None,
            "stl": f"/api/mfgcad/files/{session_id}/{safe_name}" if ext == ".stl" else (f"/api/mfgcad/files/{session_id}/model.stl" if stl_path else None),
            "3mf": f"/api/mfgcad/files/{session_id}/{safe_name}" if ext == ".3mf" else None,
        },
    })


@app.post("/api/mfgcad/sessions/{session_id}/ai-assist")
async def mfgcad_ai_assist(session_id: str, request: Request):
    """AI 协助修改：用户用自然语言描述修改需求，AI 调参重建。

    body: {"prompt": "\u58c1\u539a\u6539\u62105mm"}
    """
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)

    prompt = (body or {}).get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"error": "missing prompt"}, status_code=400)

    # 读取会话信息
    sess_file = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id / "session.json"
    if not sess_file.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)

    session_data = json.loads(sess_file.read_text(encoding="utf-8"))

    # 如果有参数，尝试用 LLM 解析用户需求→改参→重建
    from vermes_cli.mfgcad.parametric import load_parameters, load_source, apply_parameters
    params = load_parameters(session_id)
    source = load_source(session_id)

    if params and source:
        # 有参数：LLM 解析需求→改参→重建
        import httpx
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds

        api_key, base_url, model_name = _resolve_mfgcad_service_creds()
        if not api_key:
            return JSONResponse({
                "message": "\u274c \u672a\u914d\u7f6e API Key\uff0cAI \u534f\u52a9\u4e0d\u53ef\u7528\u3002\u8bf7\u5728\u8bbe\u7f6e\u2192\u670d\u52a1\u4e2d\u914d\u7f6e\u5236\u9020 CAD \u7684 API Key\u3002"
            })

        # 构建参数描述
        param_desc = "\n".join([
            f"- {name}: \u5f53\u524d\u503c {p['value']} {p.get('unit', '')}\uff08\u8303\u56f4 {p.get('min', 0)}~{p.get('max', 999)}\uff09"
            for name, p in params.items()
        ])

        system_msg = f"\u4f60\u662f\u4e00\u4e2a3D\u5efa\u6a21\u52a9\u624b\u3002\u7528\u6237\u8981\u4fee\u6539\u6a21\u578b\u53c2\u6570\u3002\u6839\u636e\u7528\u6237\u63cf\u8ff0\uff0c\u8fd4\u56de\u9700\u8981\u4fee\u6539\u7684\u53c2\u6570\u540d\u548c\u65b0\u503c\u3002\n\n\u5f53\u524d\u53c2\u6570\uff1a\n{param_desc}\n\n\u8fd4\u56de JSON \u683c\u5f0f: {{\"parameters\": {{\"PARAM_NAME\": new_value}}}}\uff0c\u53ea\u8fd4\u56de\u9700\u8981\u6539\u7684\u53c2\u6570\u3002\u5982\u679c\u7528\u6237\u7684\u63cf\u8ff0\u65e0\u6cd5\u6620\u5c04\u5230\u73b0\u6709\u53c2\u6570\uff0c\u8fd4\u56de {{\"parameters\": {{}}, \"reply\": \"\u65e0\u6cd5\u4ece\u73b0\u6709\u53c2\u6570\u5b9e\u73b0\u8be5\u4fee\u6539\uff0c\u8bf7\u5c1d\u8bd5\u91cd\u65b0\u5efa\u6a21\"}}\u3002"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_name or "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                    },
                )
                resp.raise_for_status()
                ai_reply = resp.json()["choices"][0]["message"]["content"]

            # 解析 AI 返回的参数
            import re as _re
            json_match = _re.search(r'\{[^}]+\}', ai_reply, _re.DOTALL)
            if json_match:
                ai_result = json.loads(json_match.group())
                new_params = ai_result.get("parameters", {})
                reply_text = ai_result.get("reply", "")

                if new_params:
                    # 应用新参数重建
                    from vermes_cli.mfgcad import tools as mfgcad_tools
                    rebuild_msg = await mfgcad_tools._handle_mfg_rebuild_parametric({
                        "base_session_id": session_id,
                        "parameters": new_params,
                    })
                    return JSONResponse({
                        "message": reply_text or f"\u2705 \u5df2\u8c03\u6574 {len(new_params)} \u4e2a\u53c2\u6570\u5e76\u91cd\u5efa: {rebuild_msg}",
                        "rebuilt": True,
                    })
                else:
                    return JSONResponse({"message": reply_text or "\u65e0\u6cd5\u4ece\u73b0\u6709\u53c2\u6570\u5b9e\u73b0\u8be5\u4fee\u6539"})
            else:
                return JSONResponse({"message": ai_reply})
        except Exception as e:
            return JSONResponse({"message": f"\u274c AI \u5904\u7406\u5931\u8d25: {e}"})
    else:
        # 无参数：提示用户用对话重新建模
        return JSONResponse({
            "message": "\u8be5\u6a21\u578b\u65e0\u53ef\u8c03\u53c2\u6570\uff08\u53ef\u80fd\u662f\u4e0a\u4f20\u6587\u4ef6\u6216\u65e7\u7248\u4f1a\u8bdd\uff09\u3002\u8bf7\u5728\u5bf9\u8bdd\u4e2d\u91cd\u65b0\u63cf\u8ff0\u9700\u6c42\uff0cAI \u4f1a\u751f\u6210\u65b0\u7684\u53ef\u53c2\u6570\u5316\u6a21\u578b\u3002"
        })


# ── Phase D: 2D 工程图 / BOM / 3D 打印建议 ──────────────────

@app.get("/api/mfgcad/sessions/{session_id}/drawing")
async def mfgcad_drawing(session_id: str):
    """生成 2D 工程图（三视图 + 尺寸标注）。

    用 matplotlib 渲染 STL 的前视/顶视/侧视 + 等轴测，
    标注关键尺寸（包围盒长宽高）。
    """
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    sess_file = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id / "session.json"
    if not sess_file.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)

    session = json.loads(sess_file.read_text(encoding="utf-8"))

    # 找 STL 文件
    output_dir = Path.home() / ".vermes" / "mfgcad" / "output" / session_id
    stl_path = None
    if output_dir.is_dir():
        for f in output_dir.iterdir():
            if f.suffix.lower() == ".stl":
                stl_path = f
                break
    # 也看 session 记录
    if not stl_path and session.get("stl_path"):
        p = Path(session["stl_path"])
        if p.is_file():
            stl_path = p

    if not stl_path:
        return JSONResponse({"error": "no STL file found for this session"}, status_code=404)

    try:
        import struct
        import numpy as np

        # 解析二进制 STL
        data = stl_path.read_bytes()
        if len(data) < 84:
            return JSONResponse({"error": "invalid STL file"}, status_code=400)

        n_tris = struct.unpack_from("<I", data, 80)[0]
        verts = []
        for i in range(n_tris):
            offset = 84 + i * 50
            for j in range(3):
                x, y, z = struct.unpack_from("<fff", data, offset + 12 + j * 12)
                verts.append([x, y, z])

        verts = np.array(verts)
        # 包围盒
        bbox_min = verts.min(axis=0)
        bbox_max = verts.max(axis=0)
        dims = bbox_max - bbox_min  # [x, y, z] = [长, 宽, 高]

        # 用纯 numpy + Pillow 生成工程图（不依赖 matplotlib）
        from PIL import Image, ImageDraw, ImageFont

        W, H = 1200, 900
        img = Image.new('RGB', (W, H), 'white')
        draw = ImageDraw.Draw(img)

        # 标题
        try:
            font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 18)
            font_sm = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 12)
        except Exception:
            font = ImageFont.load_default()
            font_sm = font

        title = f"工程图 — {session.get('request', session_id)[:40]}"
        draw.text((W//2 - len(title)*5, 10), title, fill='black', font=font)

        # 四个子图区域
        regions = [
            (10, 50, 580, 420, 'front', '前视图'),
            (600, 50, 1170, 420, 'top', '顶视图'),
            (10, 440, 580, 810, 'side', '侧视图'),
            (600, 440, 1170, 810, 'iso', '等轴测'),
        ]

        for x0, y0, x1, y1, view, label in regions:
            # 边框
            draw.rectangle([x0, y0, x1, y1], outline='gray', width=1)
            draw.text((x0 + 5, y0 + 3), label, fill='gray', font=font_sm)

            # 投影
            if view == 'front':
                xs, ys = verts[:, 0], verts[:, 2]
                dx, dy = dims[0], dims[2]
            elif view == 'top':
                xs, ys = verts[:, 0], verts[:, 1]
                dx, dy = dims[0], dims[1]
            elif view == 'side':
                xs, ys = verts[:, 1], verts[:, 2]
                dx, dy = dims[1], dims[2]
            else:  # iso
                xs = verts[:, 0] * 0.7 + verts[:, 1] * 0.35
                ys = verts[:, 2] * 0.8 + verts[:, 1] * 0.3
                dx = dims[0] * 0.7 + dims[1] * 0.35
                dy = dims[2] * 0.8 + dims[1] * 0.3

            # 缩放到子图区域
            pw, ph = (x1 - x0 - 40), (y1 - y0 - 40)
            scale = min(pw / max(dx, 0.1), ph / max(dy, 0.1)) * 0.8
            cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
            px = cx + (xs - xs.mean()) * scale
            py = cy - (ys - ys.mean()) * scale  # Y 翻转

            # 画点（降采样）
            step = max(1, len(px) // 2000)
            points = list(zip(px[::step], py[::step]))
            for p in points:
                draw.point(p, fill='steelblue')

            # 尺寸标注
            if view != 'iso':
                # 水平尺寸
                dim_y = y1 - 15
                draw.line([(x0 + 20, dim_y), (x1 - 20, dim_y)], fill='red', width=1)
                draw.text((cx - 15, dim_y + 1), f"{dx:.1f}mm", fill='red', font=font_sm)
                # 垂直尺寸
                dim_x = x1 - 25
                draw.line([(dim_x, y0 + 20), (dim_x, y1 - 20)], fill='red', width=1)
                draw.text((dim_x - 25, cy - 5), f"{dy:.1f}mm", fill='red', font=font_sm)

        # 保存
        drawing_dir = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id
        drawing_dir.mkdir(parents=True, exist_ok=True)
        drawing_path = drawing_dir / "engineering_drawing.png"
        img.save(drawing_path, 'PNG')

        return JSONResponse({
            "drawing_url": f"/api/mfgcad/files/{session_id}/engineering_drawing.png",
            "dimensions": {
                "length_mm": round(float(dims[0]), 2),
                "width_mm": round(float(dims[1]), 2),
                "height_mm": round(float(dims[2]), 2),
            },
            "volume_mm3": round(float(session.get("volume_mm3", 0)), 2),
        })
    except Exception as e:
        return JSONResponse({"error": f"生成工程图失败: {e}"}, status_code=500)


def __find_cjk_font():
    """找系统中可用的 CJK 字体。"""
    import matplotlib.font_manager as fm
    for name in ['PingFang SC', 'Heiti SC', 'STHeiti', 'SimHei', 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', 'Arial Unicode MS']:
        try:
            fp = fm.findfont(fm.FontProperties(family=name))
            if fp and 'LastResort' not in fp:
                return fp
        except Exception:
            continue
    return None

def __cjk_font():
    fp = __find_cjk_font()
    if fp:
        return __import__('matplotlib.font_manager').font_manager.FontProperties(fname=fp)
    return None


@app.get("/api/mfgcad/sessions/{session_id}/bom")
async def mfgcad_bom(session_id: str):
    """生成 BOM + 组装指南。"""
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    try:
        from vermes_cli.mfgcad.bom import _load_session, _load_source, _load_parameters, _infer_material
        session = _load_session(session_id)
        if not session:
            return JSONResponse({"error": "session not found"}, status_code=404)

        source = _load_source(session_id)
        params = _load_parameters(session_id)
        request_text = session.get("request", "")
        material = _infer_material(request_text)
        volume = session.get("volume_mm3", 0)

        # 构建 BOM 结构
        parts = []
        if params:
            for p in params:
                parts.append({
                    "name": p.get("name", "未知"),
                    "value": p.get("value", ""),
                    "unit": p.get("unit", "mm"),
                    "type": "参数化尺寸",
                })

        bom = {
            "request": request_text,
            "material": material,
            "volume_mm3": volume,
            "volume_cm3": round(volume / 1000, 2) if volume else 0,
            "parts": parts,
            "session_id": session_id,
        }

        # 如果有 API key，用 LLM 生成组装指南
        try:
            import httpx
            from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds
            api_key, base_url, model_name = _resolve_mfgcad_service_creds()
            if api_key:
                param_desc = "\n".join([f"- {p['name']}: {p['value']} {p.get('unit', '')}" for p in parts]) or "无参数信息"
                system_msg = f"你是制造工程师。根据以下 3D 模型信息生成简洁的 BOM 表和组装指南。\n\n模型: {request_text}\n材料: {material}\n体积: {volume} mm³\n参数:\n{param_desc}\n\n返回 Markdown 格式，包含：## BOM 表（表格）和 ## 组装步骤（编号列表）。"
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": model_name or "deepseek-chat", "messages": [{"role": "system", "content": system_msg}], "temperature": 0.3},
                    )
                    resp.raise_for_status()
                    bom["assembly_guide"] = resp.json()["choices"][0]["message"]["content"]
        except Exception:
            bom["assembly_guide"] = None  # fail-open

        return JSONResponse(bom)
    except Exception as e:
        return JSONResponse({"error": f"生成 BOM 失败: {e}"}, status_code=500)


@app.get("/api/mfgcad/sessions/{session_id}/print-advice")
async def mfgcad_print_advice(session_id: str):
    """3D 打印参数建议。"""
    import re
    from fastapi.responses import JSONResponse

    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    sess_file = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id / "session.json"
    if not sess_file.is_file():
        return JSONResponse({"error": "session not found"}, status_code=404)

    session = json.loads(sess_file.read_text(encoding="utf-8"))
    volume = session.get("volume_mm3", 0)
    request_text = session.get("request", "")

    # 基于体积和请求推断
    advice = {
        "volume_cm3": round(volume / 1000, 2) if volume else 0,
        "estimated_weight_g": round(volume * 0.00125, 1) if volume else 0,  # PLA 密度 1.25 g/cm³
        "recommendations": [],
    }

    v = volume / 1000  # cm³
    if v > 0:
        if v < 10:
            advice["recommendations"].append("小模型，建议 0.12mm 层厚提高精度")
            advice["recommendations"].append("填充率 20% 即可保证强度")
            advice["estimate_time"] = "30min - 1h"
        elif v < 100:
            advice["recommendations"].append("中等模型，建议 0.16mm 层厚平衡速度与精度")
            advice["recommendations"].append("填充率 15-20%，壁厚 1.2mm")
            advice["estimate_time"] = "1-3h"
        else:
            advice["recommendations"].append("大模型，建议 0.20mm 层厚加快打印")
            advice["recommendations"].append("填充率 10-15% 节省材料，壁厚 1.5mm")
            advice["recommendations"].append("建议加支撑（悬垂 >45° 区域）")
            advice["estimate_time"] = "3-8h"

        advice["recommendations"].append(f"预计用料 ~{advice['estimated_weight_g']}g PLA")
        advice["recommendations"].append("打印温度 200-210°C（PLA）/ 热床 60°C")
        advice["recommendations"].append("不建议用 ABS（需封闭舱体 + 100°C 热床）")

    return JSONResponse(advice)

mount_spa(app)