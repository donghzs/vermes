"""Blueprint: Session（会话历史管理）

Session listing, searching, detail, and deletion endpoints.
Uses hermes_state.SessionDB for persistence.
"""

import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request

from hermes_cli.blueprints.helpers import _session_latest_descendant

session_bp = APIRouter(tags=["session"])
_log = logging.getLogger(__name__)


# ── route handlers ─────────────────────────────────────────────

async def get_sessions(limit: int = 20, offset: int = 0):
    try:
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=limit, offset=offset)
            total = db.session_count()
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
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            terms = []
            for token in re.findall(r'"[^"]*"|\S+', q.strip()):
                if token.startswith('"') or token.endswith("*"):
                    terms.append(token)
                else:
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
    from hermes_state import SessionDB

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
    from hermes_state import SessionDB

    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = db.get_messages(sid)
        return {"session_id": sid, "messages": messages}
    finally:
        db.close()


async def delete_session_endpoint(session_id: str):
    from hermes_state import SessionDB
    from hermes_cli.blueprints.chat import clean_agent_for_session

    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
        # Sync cleanup: release cached Agent instance for this session
        clean_agent_for_session(session_id)
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

async def save_gui_messages(session_id: str, request: Request):
    """Save GUI messages for a session (JSON file storage)."""
    _MSG_DIR.mkdir(parents=True, exist_ok=True)
    body = await request.json()
    messages = body.get("messages", [])
    msg_file = _MSG_DIR / f"{session_id}.json"
    msg_file.write_text(_json.dumps(messages, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "count": len(messages)}

async def load_gui_messages(session_id: str):
    """Load GUI messages for a session."""
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
