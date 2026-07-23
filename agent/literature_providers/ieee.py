"""IEEE Xplore literature provider — paid, user-supplied API key.

Uses the IEEE Xplore Metadata Search API. The user supplies ``IEEE_API_KEY``
(applied for at developer.ieee.org) via the unified 文献源 settings form.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_IEEE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"

register_service(
    "ieee",
    api_key_env_var="IEEE_API_KEY",
    label="IEEE Xplore",
    category="literature",
    description="IEEE 电气电子工程文献库（用户自备 API Key）",
    url="https://developer.ieee.org/",
)


class IeeeProvider(LiteratureProvider):
    """IEEE Xplore metadata search (API-key driven)."""

    @property
    def name(self) -> str:
        return "ieee"

    @property
    def display_name(self) -> str:
        return "IEEE Xplore"

    def is_available(self) -> bool:
        return bool(get_api_key("ieee"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        key = get_api_key("ieee")
        if not key:
            return {"success": False, "error": "IEEE_API_KEY 未配置 — 请在设置的「文献源」中填入"}
        res = http_get_json(
            _IEEE_URL,
            params={
                "apikey": key,
                "querytext": query,
                "max_records": min(int(limit), 50),
                "sort_order": "desc",
                "sort_field": "relevance",
            },
        )
        if not res.get("ok"):
            return {"success": False, "error": f"IEEE Xplore 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("articles", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            authors_obj = item.get("authors") or {}
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[
                        a.get("full_name", "")
                        for a in (authors_obj.get("authors") or [])
                        if a.get("full_name")
                    ],
                    year=str(item.get("publication_year", "") or ""),
                    journal=item.get("publication_title", "") or "",
                    abstract=(item.get("abstract") or "")[:800],
                    cited_count=int(item.get("citing_paper_count", 0) or 0),
                    url=item.get("html_url", "") or item.get("pdf_url", "") or "",
                    doi=item.get("doi", "") or "",
                    keywords=list(
                        ((item.get("index_terms") or {}).get("ieee_terms") or {}).get("terms", [])
                    )[:5],
                    source="ieee",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key",
            "tag": "IEEE 电气电子/计算机工程文献库 — 填入 API Key 即启用",
            "env_vars": [
                {"key": "IEEE_API_KEY", "prompt": "IEEE Xplore API Key", "url": "https://developer.ieee.org/"},
            ],
        }
