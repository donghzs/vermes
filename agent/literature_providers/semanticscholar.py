"""Semantic Scholar literature provider — free; API key optional.

Uses the Semantic Scholar Graph API. Works without a key (shared rate pool);
supplying ``S2_API_KEY`` raises rate limits. The optional key is declared via
:func:`register_service` so it appears in the unified 文献源 settings form.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,authors,year,venue,abstract,citationCount,externalIds,url"

register_service(
    "semanticscholar",
    api_key_env_var="S2_API_KEY",
    label="Semantic Scholar",
    category="literature",
    description="免费学术图谱检索；API Key 可选（提升速率限制）",
    url="https://www.semanticscholar.org/product/api",
)


class SemanticScholarProvider(LiteratureProvider):
    """Free academic graph search via Semantic Scholar (optional API key)."""

    @property
    def name(self) -> str:
        return "semanticscholar"

    @property
    def display_name(self) -> str:
        return "Semantic Scholar (免费)"

    def is_available(self) -> bool:
        return True  # key optional — works without one

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        headers = {}
        key = get_api_key("semanticscholar")
        if key:
            headers["x-api-key"] = key
        res = http_get_json(
            _S2_URL,
            params={"query": query, "limit": min(int(limit), 50), "fields": _FIELDS},
            headers=headers,
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Semantic Scholar 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("data", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            ext = item.get("externalIds") or {}
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[a.get("name", "") for a in item.get("authors", []) or [] if a.get("name")],
                    year=str(item.get("year", "") or ""),
                    journal=item.get("venue", "") or "",
                    abstract=(item.get("abstract") or "")[:800],
                    cited_count=int(item.get("citationCount", 0) or 0),
                    url=item.get("url", "") or "",
                    doi=ext.get("DOI", "") or "",
                    keywords=[],
                    source="semanticscholar",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · key 可选",
            "tag": "免费学术图谱检索（引用数/摘要齐全）；可选 API Key 提升速率",
            "env_vars": [
                {
                    "key": "S2_API_KEY",
                    "prompt": "Semantic Scholar API Key（可选）",
                    "url": "https://www.semanticscholar.org/product/api",
                },
            ],
        }
