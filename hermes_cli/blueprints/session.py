"""Blueprint: Session（会话历史管理）"""
from fastapi import APIRouter
session_bp = APIRouter(tags=["session"])

def register_to(app):
    from hermes_cli import web_server as ws
    app.add_api_route("/api/sessions", ws.get_sessions, methods=["GET"])
    app.add_api_route("/api/sessions/search", ws.search_sessions, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}", ws.get_session_detail, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}/latest-descendant", ws.get_session_latest_descendant, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}/messages", ws.get_session_messages, methods=["GET"])
    app.add_api_route("/api/sessions/{session_id}", ws.delete_session_endpoint, methods=["DELETE"])

blueprint = session_bp
