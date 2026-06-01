"""Vermes API Blueprints — 各功能域路由模块"""
from .chat import chat_bp
from .quota import quota_bp
from .wechat import wechat_bp
from .models import models_bp
from .config import config_bp
from .providers import providers_bp
from .dashboard import dashboard_bp
from .session import session_bp
from . import cron_jobs
from . import update
from . import skills_tools
from . import analytics
from . import status
from . import profiles
from . import oauth

__all__ = [
    "chat_bp", "quota_bp", "wechat_bp", "models_bp",
    "config_bp", "providers_bp", "dashboard_bp", "session_bp",
    "cron_jobs",
    "update",
    "skills_tools",
    "analytics",
    "status",
    "profiles",
    "oauth",
]
