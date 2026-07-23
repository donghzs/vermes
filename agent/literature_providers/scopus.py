"""Scopus literature provider — paid, user-supplied Elsevier API key.

Uses the Elsevier Scopus Search API. The user supplies ``SCOPUS_API_KEY``
(an Elsevier developer key, dev.elsevier.com) via the 文献源 settings form.
Institutional network / insttoken requirements follow the user's Elsevier
entitlements — the provider just carries the credential.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"

register_service(
    "scopus",
    api_key_env_var="SCOPUS_API_KEY",
    label="Scopus",
    category="literature",
    description="Elsevier Scopus 文摘引文库（用户自备 Elsevier API Key）",
    url="https://dev.elsevier.com/",
    extra_fields=[
        {"key": "SCOPUS_INST_TOKEN", "label": "Scopus InstToken（机构令牌，可选）", "secret": True},
    ],
)


class ScopusProvider(LiteratureProvider):
    """Elsevier Scopus abstract & citation search (API-key driven)."""

    @property
    def name(self) -> str:
        return "scopus"

    @property
    def display_name(self) -> str:
        return "Scopus"

    def is_available(self) -> bool:
        return bool(get_api_key("scopus"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        import os

        key = get_api_key("scopus")
        if not key:
            return {"success": False, "error": "SCOPUS_API_KEY 未配置 — 请在设置的「文献源」中填入"}
        headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
        inst = os.environ.get("SCOPUS_INST_TOKEN")
        if inst:
            headers["X-ELS-Insttoken"] = inst
        res = http_get_json(
            _SCOPUS_URL,
            params={"query": f"TITLE-ABS-KEY({query})", "count": min(int(limit), 25)},
            headers=headers,
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Scopus 检索失败: {res.get('error')}"}

        entries = ((res["data"].get("search-results") or {}).get("entry")) or []
        papers = []
        for item in entries:
            title = item.get("dc:title", "")
            if not title or item.get("error"):
                continue
            url = ""
            for link in item.get("link", []) or []:
                if link.get("@ref") == "scopus":
                    url = link.get("@href", "")
                    break
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[a for a in [item.get("dc:creator", "")] if a],
                    year=str(item.get("prism:coverDate", ""))[:4],
                    journal=item.get("prism:publicationName", "") or "",
                    abstract="",
                    cited_count=int(item.get("citedby-count", 0) or 0),
                    url=url,
                    doi=item.get("prism:doi", "") or "",
                    keywords=[],
                    source="scopus",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key",
            "tag": "Elsevier Scopus 文摘引文库 — 填入 Elsevier API Key 即启用",
            "env_vars": [
                {"key": "SCOPUS_API_KEY", "prompt": "Elsevier API Key", "url": "https://dev.elsevier.com/"},
                {"key": "SCOPUS_INST_TOKEN", "prompt": "机构 InstToken（可选）"},
            ],
        }
