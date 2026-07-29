"""Blueprint: WeChat OAuth（微信扫码登录）

Proxies WeChat login requests to vbit.top backend.
Includes token validation — replaces invalid tokens with real One-API tokens.
"""

import json as _json
import logging
import os

import httpx
from fastapi import APIRouter

from vermes_cli.config import load_env

wechat_bp = APIRouter(tags=["wechat"])
_log = logging.getLogger(__name__)


# ── route handlers ─────────────────────────────────────────────

async def wechat_qrurl_proxy():
    """Proxy WeChat QR URL request to vbit.top."""
    try:
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.get(
                "https://vbit.top/api/wechat/qrurl", timeout=15
            )
            return resp.json()
    except Exception as e:
        _log.warning(f"[WeChat] qrurl proxy failed: {e}")
        return {"success": False, "error": str(e)}


async def wechat_poll_proxy(state: str):
    """Proxy WeChat poll request to vbit.top, with token validation."""
    try:
        async with httpx.AsyncClient(verify=True, timeout=15) as client:
            resp = await client.get(
                f"https://vbit.top/api/wechat/poll?state={state}",
            )
            data = resp.json()

        # If scanned and token exists, validate it against One-API
        if data.get("scanned") and data.get("token"):
            token = data["token"]
            try:
                async with httpx.AsyncClient(verify=True, timeout=5) as vc:
                    test_resp = await vc.get(
                        "https://api.vbit.top/v1/models",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if test_resp.status_code == 401 or "无效的令牌" in test_resp.text:
                        env = load_env()
                        admin_key = env.get("ONEAPI_KEY", "")
                        create_resp = await vc.post(
                            os.environ.get("ONEAPI_URL", "http://127.0.0.1:8083")
                            + "/api/token/",
                            headers={
                                "Content-Type": "application/json",
                                "Authorization": f"Bearer {admin_key}",
                            },
                            json={
                                "name": f"wx-{data.get('openid', 'user')[:8]}",
                                "remain_quota": 3500000,
                                "models": "agnes-2.0-flash",
                                "unlimited_quota": False,
                            },
                        )
                        create_data = create_resp.json()
                        if create_data.get("success"):
                            new_token = create_data["data"]["key"]
                            data["token"] = new_token
                            _log.info(
                                "[WeChat] Replaced invalid token with valid One-API token"
                            )
            except Exception as e:
                _log.warning(f"[WeChat] Token validation failed: {e}")

        return data
    except Exception as e:
        _log.warning(f"[WeChat] poll proxy failed: {e}")
        return {"success": False, "error": str(e)}


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register WeChat routes on the FastAPI app."""
    app.add_api_route(
        "/api/wechat/qrurl",
        wechat_qrurl_proxy,
        methods=["POST"],
        name="wechat_qrurl",
    )
    app.add_api_route(
        "/api/wechat/poll",
        wechat_poll_proxy,
        methods=["GET"],
        name="wechat_poll",
    )


blueprint = wechat_bp
