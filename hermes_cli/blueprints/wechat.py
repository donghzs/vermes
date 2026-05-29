"""Blueprint: WeChat OAuth（微信扫码登录）"""
from fastapi import APIRouter
wechat_bp = APIRouter(tags=["wechat"])

def register_to(app):
    from hermes_cli import web_server as ws
    app.add_api_route("/api/wechat/qrurl", ws.wechat_qrurl_proxy, methods=["POST"])
    app.add_api_route("/api/wechat/poll", ws.wechat_poll_proxy, methods=["GET"])

blueprint = wechat_bp
