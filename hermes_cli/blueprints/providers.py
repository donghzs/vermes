"""Blueprint: Providers（API Key 提供商管理）"""
from fastapi import APIRouter
providers_bp = APIRouter(tags=["providers"])

def register_to(app):
    from hermes_cli import web_server as ws
    app.add_api_route("/api/providers/oauth", ws.list_oauth_providers, methods=["GET"])
    app.add_api_route("/api/providers/oauth/{provider_id}", ws.disconnect_oauth_provider, methods=["DELETE"])
    app.add_api_route("/api/providers/oauth/{provider_id}/start", ws.start_oauth_login, methods=["POST"])
    app.add_api_route("/api/providers/oauth/{provider_id}/submit", ws.submit_oauth_code, methods=["POST"])
    app.add_api_route("/api/providers/oauth/{provider_id}/poll/{session_id}", ws.poll_oauth_session, methods=["GET"])
    app.add_api_route("/api/providers/oauth/sessions/{session_id}", ws.cancel_oauth_session, methods=["DELETE"])
    app.add_api_route("/api/providers/templates", ws.get_provider_templates, methods=["GET"])
    app.add_api_route("/api/provider/add", ws.add_provider, methods=["POST"])
    app.add_api_route("/api/provider/verify", ws.verify_provider, methods=["POST"])
    app.add_api_route("/api/provider/sync-models", ws.provider_sync_models, methods=["POST"])

blueprint = providers_bp
