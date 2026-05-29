"""Blueprint: Chat（聊天核心路由）"""
from fastapi import APIRouter

chat_bp = APIRouter(tags=["chat"])


def register_to(app):
    """注册聊天路由到 FastAPI app。"""
    from hermes_cli import web_server as ws

    app.add_api_route(
        "/api/chat/completions",
        ws.chat_completions,
        methods=["POST"],
        name="chat_completions",
    )
    app.add_api_route(
        "/api/chat/models",
        ws.chat_models,
        methods=["GET"],
        name="chat_models",
    )
