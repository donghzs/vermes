"""arXiv literature provider — free, no credential required.

Uses the public arXiv Atom API. Best for preprints in physics, CS, math,
statistics, etc. No API key needed.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_text
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

register_service(
    "arxiv",
    label="arXiv (免费)",
    category="literature",
    description="物理/计算机/数学等预印本免费检索，无需凭证",
    url="https://arxiv.org/",
)

_ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


class ArxivProvider(LiteratureProvider):
    """Free preprint search via the arXiv Atom API."""

    @property
    def name(self) -> str:
        return "arxiv"

    @property
    def display_name(self) -> str:
        return "arXiv (免费)"

    def is_available(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        res = http_get_text(
            _ARXIV_URL,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": min(int(limit), 50),
                "sortBy": "relevance",
            },
        )
        if not res.get("ok"):
            return {"success": False, "error": f"arXiv 检索失败: {res.get('error')}"}

        try:
            root = ET.fromstring(res["text"])
        except ET.ParseError as exc:
            return {"success": False, "error": f"arXiv 响应解析失败: {exc}"}

        papers = []
        for entry in root.findall(f"{_ATOM}entry"):
            title = (entry.findtext(f"{_ATOM}title") or "").strip().replace("\n", " ")
            if not title:
                continue
            url = (entry.findtext(f"{_ATOM}id") or "").strip()
            doi = ""
            for link in entry.findall(f"{_ATOM}link"):
                if link.get("title") == "doi":
                    doi = (link.get("href") or "").replace("https://doi.org/", "")
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[
                        (a.findtext(f"{_ATOM}name") or "").strip()
                        for a in entry.findall(f"{_ATOM}author")
                        if (a.findtext(f"{_ATOM}name") or "").strip()
                    ],
                    year=(entry.findtext(f"{_ATOM}published") or "")[:4],
                    journal="arXiv preprint",
                    abstract=(entry.findtext(f"{_ATOM}summary") or "").strip()[:800],
                    cited_count=0,
                    url=url,
                    doi=doi,
                    keywords=[],
                    source="arxiv",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "物理/计算机/数学等预印本免费检索，无需凭证",
            "env_vars": [],
        }
