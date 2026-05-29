"""Blueprint: Quota（积分 / 领 Token）"""
from fastapi import APIRouter
quota_bp = APIRouter(tags=["quota"])

def register_to(app):
    from hermes_cli import web_server as ws
    app.add_api_route("/api/claim", ws.claim_trial_token, methods=["POST"])
    app.add_api_route("/api/quota/check", ws.quota_check_proxy, methods=["GET"])
    app.add_api_route("/api/quota/spend", ws.quota_spend_proxy, methods=["POST"])
    app.add_api_route("/api/quota/referral/code", ws.referral_code_proxy, methods=["GET"])
    app.add_api_route("/api/quota/referral/bind", ws.referral_bind_proxy, methods=["POST"])

blueprint = quota_bp
