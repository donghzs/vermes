"""Springer Nature literature provider — paid, user-supplied API key.

Uses the Springer Nature Meta API. The user supplies ``SPRINGER_API_KEY``
(dev.springernature.com) via the 文献源 settings form.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_SPRINGER_URL = "https://api.springernature.com/meta/v2/json"

register_service(
    "springer",
    api_key_env_var="SPRINGER_API_KEY",
    label="Springer Nature",
    category="literature",
    description="Springer Nature 期刊/图书元数据库（用户自备 API Key）",
    url="https://dev.springernature.com/",
)


class SpringerProvider(LiteratureProvider):
    """Springer Nature Meta API search (API-key driven)."""

    @property
    def name(self) -> str:
        return "springer"

    @property
    def display_name(self) -> str:
        return "Springer Nature"

    def is_available(self) -> bool:
        return bool(get_api_key("springer"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        key = get_api_key("springer")
        if not key:
            return {"success": False, "error": "SPRINGER_API_KEY 未配置 — 请在设置的「文献源」中填入"}
        res = http_get_json(
            _SPRINGER_URL,
            params={"q": query, "p": min(int(limit), 50), "api_key": key},
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Springer 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("records", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            url = ""
            for u in item.get("url", []) or []:
                if isinstance(u, dict) and u.get("value"):
                    url = u["value"]
                    break
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[
                        c.get("creator", "")
                        for c in (item.get("creators") or [])
                        if isinstance(c, dict) and c.get("creator")
                    ],
                    year=str(item.get("publicationDate", ""))[:4],
                    journal=item.get("publicationName", "") or "",
                    abstract=(item.get("abstract") or "")[:800],
                    cited_count=0,
                    url=url,
                    doi=item.get("doi", "") or "",
                    keywords=[],
                    source="springer",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key",
            "tag": "Springer Nature 期刊/图书库 — 填入 API Key 即启用",
            "env_vars": [
                {"key": "SPRINGER_API_KEY", "prompt": "Springer Nature API Key", "url": "https://dev.springernature.com/"},
            ],
        }
