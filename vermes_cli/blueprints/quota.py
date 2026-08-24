"""Blueprint: Quota（积分 / 领 Token）

Vermes quota endpoints — all proxied to vbit.top backend.
Includes trial token claiming for WeChat users.
"""

import hashlib
import logging
import os
import platform
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from vermes_cli.config import load_env

quota_bp = APIRouter(tags=["quota"])
_log = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────

def _generate_device_fingerprint() -> str:
    """Generate a stable device fingerprint from hardware info."""
    parts = [
        platform.node(),
        platform.machine(),
        platform.processor(),
        platform.system(),
    ]
    raw = "|".join(p for p in parts if p)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── route handlers ─────────────────────────────────────────────

async def _claim_trial_token(wechat_openid: str) -> dict:
    """Internal: Claim trial token using wechat_openid. Returns the vbit API result."""
    fp = _generate_device_fingerprint()
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=True) as client:
            resp = await client.post(
                "https://api.vbit.top/api/claim",
                json={"wechat_openid": wechat_openid, "device_id": fp},
            )
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def claim_trial_token(request: Request):
    """微信登录后自动配置 Agnes 免费模型 provider，不调 vbit.top 领 token。"""
    try:
        body = (
            await request.json()
            if request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        wechat_openid = body.get("wechat_openid") or os.environ.get(
            "VERMES_WECHAT_OPENID", ""
        )

        if not wechat_openid:
            return {
                "success": False,
                "error": "请先微信登录后再领取免费体验",
                "require_login": True,
            }

        # 微信登录成功，返回 Agnes 免费模型 provider 配置
        # 用户登录后仍可自行配置其他 provider，这里只是自动加上免费选项
        return {
            "success": True,
            "message": "Agnes AI 免费模型已就绪",
            "provider": "agnes",
            "base_url": "https://apihub.agnes-ai.com/v1",
            "models": ["agnes-2.0-flash", "agnes-2.5-flash"],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def quota_check_proxy(request: Request):
    """Proxy quota check to vbit.top."""
    try:
        qs = str(request.url.query)
        # 从 header 读取 openid，转发到 vbit 也用 header（避免日志泄露）
        openid = request.headers.get("x-wechat-openid", "")
        headers = {}
        if openid:
            headers["X-WeChat-Openid"] = openid
        # 移除旧的 query param 传递（如果有的话）
        if "wechat_openid=" in qs:
            import re
            qs = re.sub(r'[&]?wechat_openid=[^&]*', '', qs).lstrip('&')
        url = f"https://api.vbit.top/api/quota/check"
        if qs:
            url += f"?{qs}"
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(url, headers=headers, timeout=10)
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def quota_spend_proxy(request: Request):
    """Proxy quota spend to vbit.top (with internal auth)."""
    try:
        body = await request.json()
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                "https://api.vbit.top/api/quota/spend",
                json=body,
                headers={
                    "X-Vermes-Secret": os.environ.get("VERMES_INTERNAL_SECRET", "")
                },
                timeout=10,
            )
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def referral_code_proxy(request: Request):
    """Proxy referral code to vbit.top."""
    try:
        qs = str(request.url.query)
        url = f"https://api.vbit.top/api/quota/referral/code?{qs}"
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(url, timeout=10)
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


async def referral_bind_proxy(request: Request):
    """Proxy referral bind to vbit.top."""
    try:
        body = await request.json()
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                "https://api.vbit.top/api/quota/referral/bind",
                json=body,
                timeout=10,
            )
            return resp.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register quota routes on the FastAPI app."""
    app.add_api_route(
        "/api/claim", claim_trial_token, methods=["POST"], name="claim_trial_token"
    )
    app.add_api_route(
        "/api/quota/check",
        quota_check_proxy,
        methods=["GET"],
        name="quota_check",
    )
    app.add_api_route(
        "/api/quota/spend",
        quota_spend_proxy,
        methods=["POST"],
        name="quota_spend",
    )
    app.add_api_route(
        "/api/quota/referral/code",
        referral_code_proxy,
        methods=["GET"],
        name="referral_code",
    )
    app.add_api_route(
        "/api/quota/referral/bind",
        referral_bind_proxy,
        methods=["POST"],
        name="referral_bind",
    )


blueprint = quota_bp
