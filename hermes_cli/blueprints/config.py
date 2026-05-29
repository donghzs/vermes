"""Blueprint: Config（配置 / 环境变量）"""
from fastapi import APIRouter
config_bp = APIRouter(tags=["config"])

def register_to(app):
    from hermes_cli import web_server as ws
    app.add_api_route("/api/config", ws.get_config, methods=["GET"])
    app.add_api_route("/api/config", ws.update_config, methods=["PUT"])
    app.add_api_route("/api/config/defaults", ws.get_defaults, methods=["GET"])
    app.add_api_route("/api/config/schema", ws.get_schema, methods=["GET"])
    app.add_api_route("/api/env", ws.get_env_vars, methods=["GET"])
    app.add_api_route("/api/env", ws.set_env_var, methods=["PUT"])
    app.add_api_route("/api/env", ws.remove_env_var, methods=["DELETE"])
    app.add_api_route("/api/env/reveal", ws.reveal_env_var, methods=["POST"])
    app.add_api_route("/api/config/raw", ws.get_config_raw, methods=["GET"])
    app.add_api_route("/api/config/raw", ws.update_config_raw, methods=["PUT"])

blueprint = config_bp
