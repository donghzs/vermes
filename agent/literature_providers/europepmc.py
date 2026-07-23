"""Europe PMC literature provider — free, no credential required.

Uses the Europe PMC RESTful search API (biomedical + life sciences, includes
PubMed/PMC/preprints). No API key needed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

register_service(
    "europepmc",
    label="Europe PMC (免费)",
    category="literature",
    description="生物医学文献免费检索（含 PubMed/PMC/预印本），无需凭证",
    url="https://europepmc.org/",
)

_EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePmcProvider(LiteratureProvider):
    """Free biomedical search via Europe PMC REST API."""

    @property
    def name(self) -> str:
        return "europepmc"

    @property
    def display_name(self) -> str:
        return "Europe PMC (免费)"

    def is_available(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        res = http_get_json(
            _EPMC_URL,
            params={
                "query": query,
                "format": "json",
                "pageSize": min(int(limit), 50),
                "resultType": "core",
            },
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Europe PMC 检索失败: {res.get('error')}"}

        results = ((res["data"].get("resultList") or {}).get("result")) or []
        papers = []
        for item in results:
            title = item.get("title", "")
            if not title:
                continue
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[
                        a.strip()
                        for a in (item.get("authorString", "") or "").split(",")
                        if a.strip()
                    ],
                    year=str(item.get("pubYear", "") or ""),
                    journal=(
                        ((item.get("journalInfo") or {}).get("journal") or {}).get("title", "")
                        or item.get("journalTitle", "")
                    ),
                    abstract=(item.get("abstractText") or "")[:800],
                    cited_count=int(item.get("citedByCount", 0) or 0),
                    url=(
                        f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id', '')}"
                        if item.get("id")
                        else ""
                    ),
                    doi=item.get("doi", "") or "",
                    keywords=[],
                    source="europepmc",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "生物医学文献免费检索（含 PubMed/PMC/预印本），无需凭证",
            "env_vars": [],
        }
