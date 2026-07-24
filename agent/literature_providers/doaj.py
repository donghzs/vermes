"""DOAJ literature provider — free, no credential required.

DOAJ (Directory of Open Access Journals) hosts curated OA metadata for
~20 000 journals. The public API needs no key and returns JSON.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

register_service(
    "doaj",
    label="DOAJ (免费)",
    category="literature",
    description="开放获取期刊元数据检索，无需凭证",
    url="https://doaj.org/",
)

_DOAJ_URL = "https://doaj.org/api/search/articles/{query}"


class DoajProvider(LiteratureProvider):
    """Free OA journal search via the DOAJ public API."""

    @property
    def name(self) -> str:
        return "doaj"

    @property
    def display_name(self) -> str:
        return "DOAJ (免费)"

    def is_available(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        import urllib.parse

        encoded = urllib.parse.quote(query)
        url = _DOAJ_URL.format(query=encoded)
        res = http_get_json(url, params={"pageSize": min(int(limit), 50), "page": 1})
        if not res.get("ok"):
            return {"success": False, "error": f"DOAJ 检索失败: {res.get('error')}"}

        data = res.get("data") or {}
        results = data.get("results") or []
        papers = []
        for hit in results:
            bib = hit.get("bibjson") or {}
            title = (bib.get("title") or "").strip()
            if not title:
                continue
            authors = []
            for a in bib.get("author") or []:
                name = (a.get("name") or "").strip()
                if name:
                    authors.append(name)
            year = ""
            for k in ("year", "date"):
                v = bib.get(k)
                if v:
                    year = str(v)[:4]
                    break
            journal = (bib.get("journal") or {}).get("title", "") if isinstance(bib.get("journal"), dict) else ""
            abstract = (bib.get("abstract") or "").strip()[:800]
            doi = (bib.get("identifier") or [{}])[0].get("id", "") if isinstance(bib.get("identifier"), list) else ""
            link = hit.get("id", "")
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=year,
                    journal=journal,
                    abstract=abstract,
                    cited_count=0,
                    url=link,
                    doi=doi,
                    keywords=[],
                    source="doaj",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "开放获取期刊元数据检索，无需凭证",
            "env_vars": [],
        }
