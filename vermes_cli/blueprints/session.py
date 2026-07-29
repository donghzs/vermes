"""Blueprint: Session（会话历史管理）

Session listing, searching, detail, and deletion endpoints.
Uses vermes_state.SessionDB for persistence.
"""

import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request

from vermes_cli.blueprints.helpers import _session_latest_descendant

session_bp = APIRouter(tags=["session"])
_log = logging.getLogger(__name__)


# ── route handlers ─────────────────────────────────────────────

async def get_sessions(limit: int = 20, offset: int = 0):
    try:
        from vermes_state import SessionDB

        db = SessionDB()
        try:
            # 取足够大的窗口，在内存中按“有消息优先、再按最近活动”重排，
            # 避免空壳会话（如批量产生的 telegram 空 session）淹没真实聊天。
            all_sessions = db.list_sessions_rich(limit=100000, offset=0)
            all_sessions.sort(
                key=lambda s: (
                    (s.get("message_count") or 0) > 0,
                    s.get("last_active") or s.get("started_at") or 0,
                ),
                reverse=True,
            )
            total = len(all_sessions)
            sessions = all_sessions[offset : offset + limit]
            now = time.time()
            for s in sessions:
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
            return {
                "sessions": sessions,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions failed")
        raise HTTPException(status_code=500, detail="Internal server error")


async def search_sessions(q: str = "", limit: int = 20):
    """Full-text search across session message content using FTS5."""
    if not q or not q.strip():
        return {"results": []}
    try:
        from vermes_state import SessionDB

        db = SessionDB()
        try:
            # 清洗 FTS5 特殊操作符，防止注入
            cleaned = re.sub(r'\b(AND|OR|NOT|NEAR)\b', ' ', q.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r'[()"*]', ' ', cleaned)
            cleaned = cleaned.strip()
            if not cleaned:
                return {"results": []}
            terms = []
            for token in re.findall(r'\S+', cleaned):
                terms.append(token + "*")
            prefix_query = " ".join(terms)
            matches = db.search_messages(query=prefix_query, limit=limit)
            seen: dict = {}
            for m in matches:
                sid = m["session_id"]
                if sid not in seen:
                    seen[sid] = {
                        "session_id": sid,
                        "snippet": m.get("snippet", ""),
                        "role": m.get("role"),
                        "source": m.get("source"),
                        "model": m.get("model"),
                        "session_started": m.get("session_started"),
                    }
            return {"results": list(seen.values())}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions/search failed")
        raise HTTPException(status_code=500, detail="Search failed")


async def get_session_detail(session_id: str):
    from vermes_state import SessionDB

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        session = db.get_session(sid) if sid else None
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    finally:
        db.close()


async def get_session_latest_descendant(session_id: str):
    latest, path = _session_latest_descendant(session_id)
    if not latest:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "requested_session_id": path[0] if path else session_id,
        "session_id": latest,
        "path": path,
        "changed": bool(path and latest != path[0]),
    }


async def get_session_messages(session_id: str):
    from vermes_state import SessionDB

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(sid)
        return {"session_id": sid, "messages": messages}
    finally:
        db.close()


async def send_from_desktop(session_id: str, request: Request):
    """步骤3：send-from-desktop 桥 —— 桌面代发渠道消息（web 进程侧）。

    只做三件事：校验 + 写 pending relay 信号 + 立即返回。
    - 不跑 agent（gateway 进程消费信号后走 _handle_message 管线）；
    - 不 append_message（gateway 管线自己落 user/assistant 消息，避免双写）；
    - 拒绝 source='web'（web 会话 relay 会绕回 web 进程形成环路）。
    认证：/api/sessions/* 非公开路径，web_server auth_middleware 已强制
    X-Vermes-Session-Token（即设计中的桌面 token 护栏）。
    """
    from vermes_state import SessionDB

    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > 20000:
        raise HTTPException(status_code=400, detail="text too long")

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        session = db.get_session(sid) if sid else None
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if (session.get("source") or "").lower() == "web":
            raise HTTPException(
                status_code=400,
                detail="web sessions cannot be relayed (use normal chat)",
            )
        # token 仅作 provenance 留痕落库（desktop_token 列）；真正的防伪造
        # 护栏是 auth_middleware 的 401（gateway 跨进程无 _SESSION_TOKEN 可验）。
        token = request.headers.get("X-Vermes-Session-Token", "")
        if not db.request_desktop_relay(sid, text, token, ttl=300.0):
            raise HTTPException(
                status_code=409,
                detail="another relay/handoff is already in flight for this session",
            )
        return {"ok": True, "session_id": sid, "state": "pending"}
    finally:
        db.close()


async def get_relay_state(session_id: str):
    """轮询 relay 进度（pending/running/completed/failed + error）。"""
    from vermes_state import SessionDB

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        state = db.get_desktop_relay_state(sid)
        return {"session_id": sid, "relay": state}
    finally:
        db.close()


async def delete_session_endpoint(session_id: str):
    from vermes_state import SessionDB
    from vermes_cli.blueprints.agent_cache import clean_agent_for_session

    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        # Sync cleanup: release cached Agent instance for this session
        clean_agent_for_session(session_id)
        # P1-3: also drop in-memory + SQLite plan state (closes mem-leak gap, audit #1)
        try:
            from vermes_cli.blueprints.chat import clean_session_plan_state

            clean_session_plan_state(session_id)
        except Exception:
            pass  # best-effort cleanup
        return {"ok": True}
    finally:
        db.close()


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register session routes on the FastAPI app."""
    app.add_api_route("/api/sessions", get_sessions, methods=["GET"], name="get_sessions")
    app.add_api_route(
        "/api/sessions/search", search_sessions, methods=["GET"], name="search_sessions"
    )
    app.add_api_route(
        "/api/sessions/{session_id}",
        get_session_detail,
        methods=["GET"],
        name="get_session_detail",
    )
    app.add_api_route(
        "/api/sessions/{session_id}/latest-descendant",
        get_session_latest_descendant,
        methods=["GET"],
        name="session_latest_descendant",
    )
    app.add_api_route(
        "/api/sessions/{session_id}/messages",
        get_session_messages,
        methods=["GET"],
        name="get_session_messages",
    )
    app.add_api_route(
        "/api/sessions/{session_id}/send-from-desktop",
        send_from_desktop,
        methods=["POST"],
        name="send_from_desktop",
    )
    app.add_api_route(
        "/api/sessions/{session_id}/relay-state",
        get_relay_state,
        methods=["GET"],
        name="get_relay_state",
    )
    app.add_api_route(
        "/api/sessions/{session_id}",
        delete_session_endpoint,
        methods=["DELETE"],
        name="delete_session",
    )
    # Vermes GUI 消息持久化
    app.add_api_route("/api/gui/messages/{session_id}", save_gui_messages, methods=["POST"], name="save_gui_messages")
    app.add_api_route("/api/gui/messages/{session_id}", load_gui_messages, methods=["GET"], name="load_gui_messages")
    app.add_api_route("/api/gui/messages/{session_id}", delete_gui_messages, methods=["DELETE"], name="delete_gui_messages")
    app.add_api_route("/api/gui/sessions", list_gui_sessions, methods=["GET"], name="list_gui_sessions")


blueprint = session_bp


# ── Vermes GUI 消息持久化 (JSON 文件存储) ─────────────────────────────

import json as _json
from pathlib import Path as _Path

_MSG_DIR = _Path.home() / ".vermes" / "messages"


def _validate_session_id(session_id: str) -> str:
    """校验 session_id 防止路径穿越攻击"""
    if not session_id or not isinstance(session_id, str):
        raise ValueError("session_id 不能为空")
    # 只允许字母、数字、连字符、下划线、点
    if not re.match(r'^[a-zA-Z0-9._-]+$', session_id):
        raise ValueError(f"session_id 含非法字符: {session_id!r}")
    # 防止路径穿越（.. 或绝对路径）
    resolved = (_MSG_DIR / f"{session_id}.json").resolve()
    if not str(resolved).startswith(str(_MSG_DIR.resolve())):
        raise ValueError(f"session_id 路径穿越: {session_id!r}")
    return session_id

async def save_gui_messages(session_id: str, request: Request):
    """Save GUI messages for a session (JSON file storage)."""
    session_id = _validate_session_id(session_id)
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    body = await request.json()
    messages = body.get("messages", [])
    msg_file = _MSG_DIR / f"{session_id}.json"
    msg_file.write_text(_json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(messages)}

async def load_gui_messages(session_id: str):
    """Load GUI messages for a session."""
    session_id = _validate_session_id(session_id)
    msg_file = _MSG_DIR / f"{session_id}.json"
    if not msg_file.exists():
        return {"messages": []}
    try:
        messages = _json.loads(msg_file.read_text(encoding="utf-8"))
        return {"messages": messages}
    except Exception:
        return {"messages": []}

async def delete_gui_messages(session_id: str):
    """Delete GUI messages for a session."""
    session_id = _validate_session_id(session_id)
    msg_file = _MSG_DIR / f"{session_id}.json"
    if msg_file.exists():
        msg_file.unlink()
    return {"ok": True}

async def list_gui_sessions():
    """List all GUI sessions with messages."""
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for f in sorted(_MSG_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            msgs = _json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "id": f.stem,
                "message_count": len(msgs),
                "last_modified": f.stat().st_mtime,
            })
        except Exception:
            pass
    return {"sessions": sessions}
