"""OpenAlex literature provider — free, no credential required.

OpenAlex indexes Crossref / DataCite / PubMed metadata (including Chinese
journal articles with DOIs). No API key needed; the ``polite pool`` (mailto)
is optional and omitted here to keep zero-config behaviour.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

register_service(
    "openalex",
    label="OpenAlex (免费)",
    category="literature",
    description="免费开放学术库，无需任何凭证即可检索（含中文论文）",
    url="https://openalex.org/",
)

_OPENALEX_URL = "https://api.openalex.org/works"


def _reconstruct_abstract(inv_index: Optional[dict]) -> str:
    """Rebuild an abstract string from OpenAlex's inverted index."""
    if not inv_index:
        return ""
    try:
        word_positions: Dict[int, str] = {}
        for word, positions in inv_index.items():
            for pos in positions:
                word_positions[pos] = word
        return " ".join(word_positions[k] for k in sorted(word_positions))[:800]
    except Exception:  # noqa: BLE001
        return ""


class OpenAlexProvider(LiteratureProvider):
    """Free academic search via OpenAlex."""

    @property
    def name(self) -> str:
        return "openalex"

    @property
    def display_name(self) -> str:
        return "OpenAlex (免费)"

    def is_available(self) -> bool:
        # Always available — no credential required.
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
                    _OPENALEX_URL,
                    params={
                        "search": query,
                        "per_page": min(int(limit), 50),
                        "sort": "cited_by_count:desc",
                    },
                )
                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"OpenAlex 返回 HTTP {resp.status_code}",
                    }
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("OpenAlex search error: %s", exc)
            return {"success": False, "error": f"OpenAlex 检索失败: {exc}"}

        papers = []
        for w in data.get("results", []):
            title = w.get("title", "")
            if not title:
                continue
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in w.get("authorships", [])
                if a.get("author", {}).get("display_name")
            ]
            venue = ""
            if w.get("primary_location") and w["primary_location"].get("source"):
                venue = w["primary_location"]["source"].get("display_name", "")
            papers.append(
                PaperRecord(
                    title=title,
                    authors=authors,
                    year=str(w.get("publication_year", "")),
                    journal=venue,
                    abstract=_reconstruct_abstract(w.get("abstract_inverted_index")),
                    cited_count=w.get("cited_by_count", 0) or 0,
                    url=(
                        w.get("doi")
                        and f"https://doi.org/{w['doi'].lstrip('https://doi.org/')}"
                    )
                    or "",
                    doi=w.get("doi", "") or "",
                    keywords=[
                        k.get("display_name", "")
                        for k in w.get("keywords", [])[:5]
                        if k.get("display_name")
                    ],
                    source="openalex",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · no key",
            "tag": "免费开放学术库，无需任何凭证即可检索（含中文论文）",
            "env_vars": [],
        }
