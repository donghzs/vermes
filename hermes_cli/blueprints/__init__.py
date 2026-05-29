"""Vermes API Blueprints — 各功能域路由模块"""
from .chat import chat_bp
from .quota import quota_bp
from .wechat import wechat_bp
from .models import models_bp
from .config import config_bp
from .providers import providers_bp
from .dashboard import dashboard_bp
from .session import session_bp

__all__ = [
    "chat_bp", "quota_bp", "wechat_bp", "models_bp",
    "config_bp", "providers_bp", "dashboard_bp", "session_bp",
]
