"""ScienceDirect literature provider — paid, user-supplied Elsevier API key.

Uses the Elsevier ScienceDirect Search API v2. ``SCIENCEDIRECT_API_KEY`` is
used when set; otherwise falls back to ``SCOPUS_API_KEY`` (both are Elsevier
developer keys and are commonly shared).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_post_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_SD_URL = "https://api.elsevier.com/content/search/sciencedirect"

register_service(
    "sciencedirect",
    api_key_env_var="SCIENCEDIRECT_API_KEY",
    label="ScienceDirect",
    category="literature",
    description="Elsevier ScienceDirect 全文平台（用户自备 Elsevier API Key；缺省可复用 Scopus Key）",
    url="https://dev.elsevier.com/",
)


def _resolve_key() -> Optional[str]:
    return get_api_key("sciencedirect") or get_api_key("scopus")


class ScienceDirectProvider(LiteratureProvider):
    """Elsevier ScienceDirect search (API-key driven, PUT/POST v2 API)."""

    @property
    def name(self) -> str:
        return "sciencedirect"

    @property
    def display_name(self) -> str:
        return "ScienceDirect"

    def is_available(self) -> bool:
        return bool(_resolve_key())

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        key = _resolve_key()
        if not key:
            return {
                "success": False,
                "error": "SCIENCEDIRECT_API_KEY / SCOPUS_API_KEY 未配置 — 请在设置的「文献源」中填入",
            }
        res = http_post_json(
            _SD_URL,
            json_body={"qs": query, "display": {"show": min(int(limit), 50), "sortBy": "relevance"}},
            headers={"X-ELS-APIKey": key, "Accept": "application/json"},
        )
        if not res.get("ok"):
            return {"success": False, "error": f"ScienceDirect 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("results", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            authors = [
                a.get("name", "")
                for a in (item.get("authors") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=str(item.get("publicationDate", ""))[:4],
                    journal=item.get("sourceTitle", "") or "",
                    abstract="",
                    cited_count=0,
                    url=item.get("uri", "") or "",
                    doi=item.get("doi", "") or "",
                    keywords=[],
                    source="sciencedirect",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key",
            "tag": "Elsevier ScienceDirect 全文平台 — 填入 Elsevier API Key 即启用（可复用 Scopus Key）",
            "env_vars": [
                {"key": "SCIENCEDIRECT_API_KEY", "prompt": "Elsevier API Key", "url": "https://dev.elsevier.com/"},
            ],
        }
