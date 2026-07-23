"""PubMed literature provider — free, no credential required.

Uses NCBI E-utilities (esearch + esummary, JSON mode). Best for biomedical /
life-science literature. No API key needed at low request rates.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

register_service(
    "pubmed",
    label="PubMed (免费)",
    category="literature",
    description="生物医学/生命科学文献免费检索（NCBI E-utilities），无需凭证",
    url="https://pubmed.ncbi.nlm.nih.gov/",
)

_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


class PubMedProvider(LiteratureProvider):
    """Free biomedical search via NCBI PubMed E-utilities."""

    @property
    def name(self) -> str:
        return "pubmed"

    @property
    def display_name(self) -> str:
        return "PubMed (免费)"

    def is_available(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        res = http_get_json(
            _ESEARCH_URL,
            params={
                "db": "pubmed",
                "term": query,
                "retmax": min(int(limit), 50),
                "retmode": "json",
                "sort": "relevance",
            },
        )
        if not res.get("ok"):
            return {"success": False, "error": f"PubMed 检索失败: {res.get('error')}"}
        ids = (res["data"].get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return {"success": True, "data": {"papers": []}}

        res2 = http_get_json(
            _ESUMMARY_URL,
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        )
        if not res2.get("ok"):
            return {"success": False, "error": f"PubMed 摘要获取失败: {res2.get('error')}"}

        summary = res2["data"].get("result") or {}
        papers = []
        for pmid in ids:
            item = summary.get(pmid)
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            if not title:
                continue
            doi = ""
            for aid in item.get("articleids", []) or []:
                if aid.get("idtype") == "doi":
                    doi = aid.get("value", "")
                    break
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[a.get("name", "") for a in item.get("authors", []) if a.get("name")],
                    year=str(item.get("pubdate", ""))[:4],
                    journal=item.get("fulljournalname", "") or item.get("source", ""),
                    abstract="",
                    cited_count=0,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    doi=doi,
                    keywords=[],
                    source="pubmed",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "生物医学/生命科学文献免费检索（NCBI E-utilities），无需凭证",
            "env_vars": [],
        }
