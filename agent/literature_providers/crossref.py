"""Crossref literature provider — free, no credential required.

Crossref is the primary DOI registration agency; its REST API returns rich
metadata (authors, journal, citation count, often an abstract stub) for both
English and Chinese publications. No API key needed.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.literature_provider import LiteratureProvider, PaperRecord

logger = logging.getLogger(__name__)

_CROSSREF_URL = "https://api.crossref.org/works"


class CrossrefProvider(LiteratureProvider):
    """Free academic search via the Crossref REST API."""

    @property
    def name(self) -> str:
        return "crossref"

    @property
    def display_name(self) -> str:
        return "Crossref (免费)"

    def is_available(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        try:
            import httpx
        except ImportError:
            return {"success": False, "error": "httpx 未安装 — pip install httpx"}

        try:
            with httpx.Client(timeout=20) as client:
                resp = client.get(
                    _CROSSREF_URL,
                    params={"query": query, "rows": min(int(limit), 50)},
                )
                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Crossref 返回 HTTP {resp.status_code}",
                    }
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("Crossref search error: %s", exc)
            return {"success": False, "error": f"Crossref 检索失败: {exc}"}

        items = (data.get("message") or {}).get("items", []) or []
        papers: List[Dict[str, Any]] = []
        for it in items:
            title = (it.get("title") or [""])[0] if it.get("title") else ""
            if not title:
                continue
            authors = [
                (a.get("given", "") + " " + a.get("family", "")).strip()
                or a.get("name", "")
                for a in it.get("author", []) or []
                if a.get("given") or a.get("family") or a.get("name")
            ]
            container = (it.get("container-title") or [""])[0] if it.get("container-title") else ""
            year = ""
            issued = it.get("issued") or {}
            parts = issued.get("date-parts") or [[]]
            if parts and parts[0]:
                year = str(parts[0][0])
            doi = it.get("DOI", "") or ""
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=year,
                    journal=container,
                    abstract=_jats_to_text(it.get("abstract")),
                    cited_count=int(it.get("is-referenced-by-count", 0) or 0),
                    url=doi and f"https://doi.org/{doi}" or (it.get("URL", "") or ""),
                    doi=doi,
                    keywords=[
                        s for s in (it.get("subject") or [])[:5] if isinstance(s, str)
                    ],
                    source="crossref",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "免费 DOI 元数据库（Crossref），无需任何凭证即可检索",
            "env_vars": [],
        }


def _jats_to_text(abstract: Optional[str]) -> str:
    """Strip JATS XML tags from a Crossref abstract (best-effort)."""
    if not abstract or not isinstance(abstract, str):
        return ""
    try:
        import re

        return re.sub(r"<[^>]+>", "", abstract)[:800].strip()
    except Exception:  # noqa: BLE001
        return ""
