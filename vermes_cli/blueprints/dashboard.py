"""Blueprint: Dashboard（仪表盘 / 主题 / 插件）"""
from fastapi import APIRouter
dashboard_bp = APIRouter(tags=["dashboard"])

def register_to(app):
    from vermes_cli import web_server as ws
    app.add_api_route("/api/dashboard/themes", ws.get_dashboard_themes, methods=["GET"])
    app.add_api_route("/api/dashboard/theme", ws.set_dashboard_theme, methods=["PUT"])
    app.add_api_route("/api/dashboard/plugins", ws.get_dashboard_plugins, methods=["GET"])
    app.add_api_route("/api/dashboard/plugins/rescan", ws.rescan_dashboard_plugins, methods=["GET"])
    app.add_api_route("/api/dashboard/plugins/hub", ws.get_plugins_hub, methods=["GET"])
    app.add_api_route("/api/dashboard/agent-plugins/install", ws.post_agent_plugin_install, methods=["POST"])
    app.add_api_route("/api/dashboard/agent-plugins/{name}/enable", ws.post_agent_plugin_enable, methods=["POST"])
    app.add_api_route("/api/dashboard/agent-plugins/{name}/disable", ws.post_agent_plugin_disable, methods=["POST"])
    app.add_api_route("/api/dashboard/agent-plugins/{name}/update", ws.post_agent_plugin_update, methods=["POST"])
    app.add_api_route("/api/dashboard/agent-plugins/{name}", ws.delete_agent_plugin, methods=["DELETE"])
    app.add_api_route("/api/dashboard/plugin-providers", ws.put_plugin_providers, methods=["PUT"])
    app.add_api_route("/api/dashboard/plugins/{name}/visibility", ws.post_plugin_visibility, methods=["POST"])
    app.add_api_route("/dashboard-plugins/{plugin_name}/{file_path:path}", ws.serve_plugin_asset, methods=["GET"])

blueprint = dashboard_bp
