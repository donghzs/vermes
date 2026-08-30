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
from . import workflows
from . import update
from . import skills_tools
from . import analytics
from . import status
from . import gateway_channels
from . import profiles
from . import oauth
from . import storage
from . import artifacts
from . import modules_market
from . import capabilities
from . import mcp_catalog
from . import credential_lifecycle
from . import bricks  # P1-2: 四态合一注册表 API（GET/POST /api/v1/bricks）
from . import invoke  # P3-2/P3-3: 统一能力调用端点（POST /api/invoke 等 4 个）
from . import benchmark  # P4-4 T2: benchmark 可视化大盘端点

__all__ = [
    "quota_bp", "wechat_bp", "models_bp",
    "dashboard_bp", "session_bp",
    "chat", "config", "providers",
    "cron_jobs", "workflows", "update", "skills_tools",
    "analytics", "status", "gateway_channels", "profiles",
    "oauth", "storage", "artifacts", "modules_market", "mcp_catalog", "credential_lifecycle",
    "bricks", "invoke", "benchmark",
]
