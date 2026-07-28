"""Vermes API Blueprints — 各功能域路由模块"""
from .quota import quota_bp
from .wechat import wechat_bp
from .models import models_bp
from .dashboard import dashboard_bp
from .session import session_bp
from . import chat
from . import config
from . import providers
from . import cron_jobs
from . import update
from . import skills_tools
from . import analytics
from . import status
from . import gateway_channels
from . import profiles
from . import oauth
from . import storage

__all__ = [
    "quota_bp", "wechat_bp", "models_bp",
    "dashboard_bp", "session_bp",
    "chat", "config", "providers",
    "cron_jobs", "update", "skills_tools",
    "analytics", "status", "gateway_channels", "profiles",
    "oauth", "storage",
]
