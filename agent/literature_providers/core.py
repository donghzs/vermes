"""CORE literature provider — free, optional API key for higher rate limits.

CORE aggregates OA full-text from repositories worldwide. The v3 API needs
an API key for full access but works without one for limited results.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

_CORE_URL = "https://api.core.ac.uk/v3/search/works"

register_service(
    "core",
    api_key_env_var="CORE_API_KEY",
    label="CORE",
    description="CORE 开放获取知识库 — 免费，API Key 提升限额",
    category="literature",
)


class CoreProvider(LiteratureProvider):
    """Free OA repository search via CORE v3 API."""

    @property
    def name(self) -> str:
        return "core"

    @property
    def display_name(self) -> str:
        return "CORE (免费)"

    def is_available(self) -> bool:
        return True  # works without API key for limited results

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        headers: Dict[str, str] = {}
        api_key = os.environ.get("CORE_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        res = http_get_json(
            _CORE_URL,
            params={"q": query, "limit": min(int(limit), 50)},
            headers=headers or None,
        )
        if not res.get("ok"):
            return {"success": False, "error": f"CORE 检索失败: {res.get('error')}"}

        data = res.get("data") or {}
        results = data.get("results") or data.get("results") or []
        papers = []
        for hit in results:
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            authors = []
            for a in hit.get("authors") or []:
                name = a.get("name") if isinstance(a, dict) else str(a)
                if name:
                    authors.append(str(name))
            year = ""
            for k in ("year", "year_published", "published", "date"):
                v = hit.get(k)
                if v:
                    year = str(v)[:4]
                    break
            abstract = (hit.get("abstract") or hit.get("description") or "").strip()[:800]
            doi = hit.get("doi", "")
            url = hit.get("download_url") or hit.get("source_url") or ""
            cited = hit.get("citation_count", 0) or 0
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=year,
                    journal=hit.get("publisher", ""),
                    abstract=abstract,
                    cited_count=int(cited),
                    url=url,
                    doi=doi,
                    keywords=[],
                    source="core",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · optional key",
            "tag": "全球开放获取知识库，无 Key 可用，配置 Key 提升限额",
            "env_vars": [
                {"key": "CORE_API_KEY", "prompt": "CORE API Key (可选)", "url": "https://core.ac.uk/api-keys"},
            ],
        }
