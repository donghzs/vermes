"""FastAPI router for the unified capability invoke endpoint (P3-2).

Adds POST /api/invoke — capability-aware tool dispatch via
``vermes_cli.capabilities.module_service.invoke``.

Mounted by ``web_server.py`` via ``register_to(app)`` (module import time,
before the SPA catch-all ``/{full_path:path}`` route).
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from vermes_cli.capabilities import module_service

router = APIRouter()


class InvokeRequest(BaseModel):
    cap: str
    payload: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None


class ModelChangeRequest(BaseModel):
    model: str
    provider: Optional[str] = None


@router.post("/api/invoke")
def post_invoke(req: InvokeRequest):
    """统一能力调用入口：cap → 工具解析 → model_capable 校验 → tier 决策 → dispatch。"""
    return module_service.invoke(req.cap, payload=req.payload, session_id=req.session_id)


@router.post("/api/model-change")
def post_model_change(req: ModelChangeRequest):
    """广播模型切换事件（vermes-model-change）；前端经 /api/model-change/stream 订阅。"""
    module_service.broadcast_model_change(req.model, req.provider)
    return {"ok": True, "event": "vermes-model-change", "model": req.model}


@router.get("/api/model-change/stream")
def model_change_stream():
    """SSE 流：推送 vermes-model-change 事件给前端（P3-3 消费）。"""
    client_q = module_service.subscribe_model_change()

    def gen():
        try:
            yield "retry: 5000\n\n"
            while True:
                event = client_q.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            module_service.unsubscribe_model_change(client_q)

    return StreamingResponse(gen(), media_type="text/event-stream")


def register_to(app):
    """Register the invoke routes (module import time, before SPA catch-all)."""
    app.include_router(router)
