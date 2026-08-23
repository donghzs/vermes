"""Blueprint: Models（模型管理）"""
from fastapi import APIRouter
models_bp = APIRouter(tags=["models"])

def register_to(app):
    from vermes_cli import web_server as ws
    app.add_api_route("/api/model/info", ws.get_model_info, methods=["GET"])
    app.add_api_route("/api/model/options", ws.get_model_options, methods=["GET"])
    app.add_api_route("/api/models", ws.get_model_options, methods=["GET"])  # 兼容性别名
    app.add_api_route("/api/model/auxiliary", ws.get_auxiliary_models, methods=["GET"])
    app.add_api_route("/api/model/set", ws.set_model_assignment, methods=["POST"])
    app.add_api_route("/api/model/discover", ws.discover_models, methods=["POST"])

blueprint = models_bp
