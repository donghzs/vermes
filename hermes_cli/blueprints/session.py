"""Blueprint: Session（会话历史管理）

Session listing, searching, detail, and deletion endpoints.
Uses hermes_state.SessionDB for persistence.
"""

import logging
import re
import time

from fastapi import APIRouter, HTTPException

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

    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="Session not found")
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


blueprint = session_bp
