"""FastAPI router for the unified capability invoke endpoint (P3-2).

Adds POST /api/invoke — capability-aware tool dispatch via
``vermes_cli.capabilities.module_service.invoke``.

Mounted by ``web_server.py`` via ``register_to(app)`` (module import time,
before the SPA catch-all ``/{full_path:path}`` route).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vermes_cli.capabilities import module_service

router = APIRouter()


class InvokeRequest(BaseModel):
    cap: str
    payload: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


@router.post("/api/invoke")
def post_invoke(req: InvokeRequest):
    """统一能力调用入口：cap → 工具解析 → model_capable 校验 → tier 决策 → dispatch。"""
    return module_service.invoke(req.cap, payload=req.payload, session_id=req.session_id)


def register_to(app):
    """Register the invoke route (module import time, before SPA catch-all)."""
    app.include_router(router)
