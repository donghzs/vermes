"""Blueprint: 凭证轮换 + approvals 协同 (P1-4)

端点:
- GET  /api/credentials/health           — 全部凭证健康状态
- GET  /api/credentials/health/{provider} — 单个凭证健康状态
- POST /api/credentials/refresh/{provider} — 触发凭证刷新
- POST /api/credentials/rotate/{provider}  — 泄露轮换（清除旧凭证）
- GET  /api/credentials/trust-gate         — TrustGate 严格模式状态
- POST /api/credentials/trust-gate         — 切换严格模式
- POST /api/credentials/suggest-approval   — 建议审批流
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
cred_bp = APIRouter(tags=["credentials"])


@cred_bp.get("/api/credentials/health")
async def get_all_credentials_health():
    """全部凭证健康状态。"""
    from vermes_cli.credential_lifecycle import check_all_credentials
    results = check_all_credentials()
    return {
        "credentials": [r.to_dict() for r in results],
        "total": len(results),
        "healthy": sum(1 for r in results if r.status == "healthy"),
        "expiring": sum(1 for r in results if r.status == "expiring"),
        "expired": sum(1 for r in results if r.status == "expired"),
        "missing": sum(1 for r in results if r.status == "missing"),
    }


@cred_bp.get("/api/credentials/health/{provider}")
async def get_credential_health(provider: str):
    """单个凭证健康状态。"""
    from vermes_cli.credential_lifecycle import check_credential_health
    result = check_credential_health(provider)
    return result.to_dict()


@cred_bp.post("/api/credentials/refresh/{provider}")
async def refresh_credential(provider: str):
    """触发凭证刷新。"""
    from vermes_cli.credential_lifecycle import refresh_credential as do_refresh
    success, message = do_refresh(provider)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "provider": provider, "message": message}


@cred_bp.post("/api/credentials/rotate/{provider}")
async def rotate_credential(provider: str):
    """泄露轮换：清除旧凭证，标记需要重新授权。"""
    from vermes_cli.credential_lifecycle import rotate_credential as do_rotate
    success, message = do_rotate(provider)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "provider": provider, "message": message}


@cred_bp.get("/api/credentials/trust-gate")
async def get_trust_gate():
    """TrustGate 严格模式状态。"""
    from vermes_cli.credential_lifecycle import is_trust_gate_strict
    return {"strict_mode": is_trust_gate_strict()}


class TrustGateToggle(BaseModel):
    strict: bool


@cred_bp.post("/api/credentials/trust-gate")
async def set_trust_gate(req: TrustGateToggle):
    """切换 TrustGate 严格模式。"""
    from vermes_cli.credential_lifecycle import set_trust_gate_strict
    set_trust_gate_strict(req.strict)
    return {"ok": True, "strict_mode": req.strict}


class SuggestApprovalReq(BaseModel):
    action: str
    context: Dict[str, Any] = {}


@cred_bp.post("/api/credentials/suggest-approval")
async def suggest_approval(req: SuggestApprovalReq):
    """建议审批流：低危操作给建议而非硬拒。"""
    from vermes_cli.credential_lifecycle import suggest_approval
    return suggest_approval(req.action, req.context)


def register_to(app):
    """Register credential lifecycle routes."""
    app.include_router(cred_bp)
