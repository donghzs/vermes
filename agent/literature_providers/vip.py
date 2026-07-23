"""维普 (VIP / cqvip) literature provider — gateway + 账号密码 driven.

维普没有公开的个人开发者 API；本 provider 走「用户自建/机构网关」模式（与
CNKI 网关同一契约）：用户在 文献源 设置中填入自己的网关地址与凭证，Vermes
只是把检索请求转发给用户自己的合法接入点 — 框架不内置任何账号。

Gateway contract (same as the CNKI gateway convention):
  POST {VIP_GATEWAY_URL}/search  JSON {"query": str, "limit": int}
  auth: header ``X-API-Key: $VIP_API_KEY`` and/or JSON ``username``/``password``
  response: {"papers": [{title, authors, year, journal, abstract,
                          cited_count, url, doi, keywords}, ...]}
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_post_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

register_service(
    "vip",
    api_key_env_var="VIP_API_KEY",
    base_url_env_var="VIP_GATEWAY_URL",
    label="维普 VIP",
    category="literature",
    description="维普中文期刊库 — 用户自备网关地址 + 账号密码/API Key",
    url="https://www.cqvip.com/",
    extra_fields=[
        {"key": "VIP_USERNAME", "label": "维普账号（用户名）", "secret": False},
        {"key": "VIP_PASSWORD", "label": "维普密码", "secret": True},
    ],
)


class VipProvider(LiteratureProvider):
    """维普 VIP — 中文期刊源（网关 + 账号密码驱动）。"""

    @property
    def name(self) -> str:
        return "vip"

    @property
    def display_name(self) -> str:
        return "维普 VIP"

    def is_available(self) -> bool:
        return bool(os.environ.get("VIP_GATEWAY_URL"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        gateway = (os.environ.get("VIP_GATEWAY_URL") or "").rstrip("/")
        if not gateway:
            return {"success": False, "error": "VIP_GATEWAY_URL 未配置 — 请在设置的「文献源」中填入网关地址"}

        headers = {}
        key = get_api_key("vip")
        if key:
            headers["X-API-Key"] = key
        body: Dict[str, Any] = {"query": query, "limit": min(int(limit), 50)}
        username = os.environ.get("VIP_USERNAME")
        password = os.environ.get("VIP_PASSWORD")
        if username and password:
            body["username"] = username
            body["password"] = password

        res = http_post_json(f"{gateway}/search", json_body=body, headers=headers)
        if not res.get("ok"):
            return {"success": False, "error": f"维普网关检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("papers", []) or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            papers.append(
                PaperRecord(
                    title=item.get("title", ""),
                    authors=list(item.get("authors", []) or []),
                    year=str(item.get("year", "") or ""),
                    journal=item.get("journal", "") or "",
                    abstract=(item.get("abstract") or "")[:800],
                    cited_count=int(item.get("cited_count", 0) or 0),
                    url=item.get("url", "") or "",
                    doi=item.get("doi", "") or "",
                    keywords=list(item.get("keywords", []) or [])[:5],
                    source="vip",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "网关 + 账号密码",
            "tag": "维普中文期刊库 — 填入自有网关地址与账号密码/API Key 即启用",
            "env_vars": [
                {"key": "VIP_GATEWAY_URL", "prompt": "维普网关地址（自建/机构接入点）"},
                {"key": "VIP_API_KEY", "prompt": "网关 API Key（可选）"},
                {"key": "VIP_USERNAME", "prompt": "维普账号（用户名，可选）"},
                {"key": "VIP_PASSWORD", "prompt": "维普密码（可选）"},
            ],
        }
