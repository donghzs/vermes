"""Web of Science literature provider — paid, user-supplied Clarivate API key.

Uses the Web of Science Starter API (api.clarivate.com). The user supplies
``WOS_API_KEY`` via the 文献源 settings form.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_WOS_URL = "https://api.clarivate.com/apis/wos-starter/v1/documents"

register_service(
    "wos",
    api_key_env_var="WOS_API_KEY",
    label="Web of Science",
    category="literature",
    description="Clarivate Web of Science 核心合集（用户自备 Starter API Key）",
    url="https://developer.clarivate.com/apis/wos-starter",
)


class WosProvider(LiteratureProvider):
    """Web of Science Starter API search (API-key driven)."""

    @property
    def name(self) -> str:
        return "wos"

    @property
    def display_name(self) -> str:
        return "Web of Science"

    def is_available(self) -> bool:
        return bool(get_api_key("wos"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        key = get_api_key("wos")
        if not key:
            return {"success": False, "error": "WOS_API_KEY 未配置 — 请在设置的「文献源」中填入"}
        res = http_get_json(
            _WOS_URL,
            params={"q": f"TS=({query})", "limit": min(int(limit), 50), "page": 1},
            headers={"X-ApiKey": key},
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Web of Science 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("hits", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            names = ((item.get("names") or {}).get("authors")) or []
            source_info = item.get("source") or {}
            ids = item.get("identifiers") or {}
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[a.get("displayName", "") for a in names if a.get("displayName")],
                    year=str(source_info.get("publishYear", "") or ""),
                    journal=source_info.get("sourceTitle", "") or "",
                    abstract="",
                    cited_count=int(
                        next(
                            (
                                c.get("count", 0)
                                for c in (item.get("citations") or [])
                                if isinstance(c, dict)
                            ),
                            0,
                        )
                        or 0
                    ),
                    url=((item.get("links") or {}).get("record")) or "",
                    doi=ids.get("doi", "") or "",
                    keywords=list(((item.get("keywords") or {}).get("authorKeywords")) or [])[:5],
                    source="wos",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key",
            "tag": "Web of Science 核心合集 — 填入 Clarivate Starter API Key 即启用",
            "env_vars": [
                {
                    "key": "WOS_API_KEY",
                    "prompt": "Clarivate WoS Starter API Key",
                    "url": "https://developer.clarivate.com/apis/wos-starter",
                },
            ],
        }
