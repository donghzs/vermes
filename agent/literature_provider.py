"""
Literature Provider ABC
========================

Defines the pluggable-backend interface for academic-literature search and
full-text retrieval. Providers register instances via
``PluginContext.register_literature_provider()`` (the bundled built-ins are
registered through :func:`agent.literature_registry.bootstrap_builtin_providers`);
the active one (selected via ``literature.backend`` / ``literature.search_backend``
in ``config.yaml``) services every ``literature_search`` tool call.

This ABC is the SINGLE plugin-facing surface for literature providers — every
provider in the tree (openalex, crossref, cnki, wanfang) implements it. The
shape mirrors :class:`agent.web_search_provider.WebSearchProvider` so the
registry / tool-wrapper contract stays familiar across capabilities.

Pluggable credential model (per user requirement)
-------------------------------------------------
A provider may be:

* **Free / no-credential** (OpenAlex, Crossref) — ``is_available()`` returns
  ``True`` unconditionally; the user needs nothing to start searching.
* **Credential-driven** (CNKI, Wanfang) — ``is_available()`` returns ``True``
  only when the user has supplied *either* an API key *or* a username/password
  pair via the unified credential layer (:mod:`agent.service_credentials`).
  The provider declares its credential metadata through ``register_service``
  at import time so the frontend can render the right setup form, and reads
  them back through ``get_api_key`` / ``os.environ`` at call time.

This lets a user "drop in an account or API key and have the agent pick the
source up instantly" without code changes — the framework's zero-vendor-name
rule is preserved (only the plugin self-declares its service id).

Response shape
--------------

Search results::

    {
        "success": True,
        "data": {
            "papers": [
                {"title": str, "authors": [str], "year": str, "journal": str,
                 "abstract": str, "cited_count": int, "url": str, "doi": str,
                 "keywords": [str], "source": str},
                ...
            ]
        }
    }

On failure::

    {"success": False, "error": str}
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class PaperRecord:
    """Normalized academic-paper record returned by every literature provider."""

    title: str
    authors: List[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    abstract: str = ""
    cited_count: int = 0
    url: str = ""
    doi: str = ""
    keywords: List[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PaperRecord":
        if not isinstance(d, dict):
            return cls(title="")
        return cls(
            title=str(d.get("title", "")),
            authors=list(d.get("authors") or []),
            year=str(d.get("year", "")),
            journal=str(d.get("journal", "")),
            abstract=str(d.get("abstract", "")),
            cited_count=int(d.get("cited_count") or 0),
            url=str(d.get("url", "")),
            doi=str(d.get("doi", "")),
            keywords=list(d.get("keywords") or []),
            source=str(d.get("source", "")),
        )


class LiteratureProvider(abc.ABC):
    """Abstract base class for a literature search/retrieval backend.

    Subclasses must implement :meth:`is_available` and :meth:`search`. The
    :meth:`supports_fulltext` capability flag lets the registry route
    full-text calls and lets providers advertise full-text support from the
    same class.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable short identifier used in ``literature.search_backend`` /
        ``literature.backend`` config keys.

        Lowercase, no spaces; hyphens permitted. Examples: ``openalex``,
        ``crossref``, ``cnki``, ``wanfang``.
        """

    @property
    def display_name(self) -> str:
        """Human-readable label shown in tool pickers. Defaults to ``name``."""
        return self.name

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider can service calls.

        Typically a cheap check (env var / account present, optional Python
        dep importable). Must NOT make network calls — this runs at
        tool-registration time and on every provider-list paint.
        """

    def supports_search(self) -> bool:
        """Return True if this provider implements :meth:`search`."""
        return True

    def supports_fulltext(self) -> bool:
        """Return True if this provider implements :meth:`fetch_fulltext`."""
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Execute a literature search.

        Override when :meth:`supports_search` returns True. The default raises
        NotImplementedError; callers should gate on ``supports_search`` first.

        Return shape::

            {"success": True, "data": {"papers": [PaperRecord.to_dict(), ...]}}
            {"success": False, "error": str}
        """
        raise NotImplementedError(
            f"{self.name} does not support search (override supports_search)"
        )

    def fetch_fulltext(self, paper: PaperRecord, **kwargs: Any) -> Dict[str, Any]:
        """Fetch the full text (PDF / HTML) for a paper record.

        Override when :meth:`supports_fulltext` returns True. The default
        raises NotImplementedError; callers should gate on
        ``supports_fulltext`` first.

        Return shape::

            {"success": True, "data": {"content": str, "pdf_path": str, ...}}
            {"success": False, "error": str}
        """
        raise NotImplementedError(
            f"{self.name} does not support fetch_fulltext (override supports_fulltext)"
        )

    def get_setup_schema(self) -> Dict[str, Any]:
        """Return provider metadata for the credential-setup UI.

        Shape (consumed by the unified service-setup picker)::

            {
                "name": "CNKI 知网",
                "badge": "API key or account",
                "tag": "中文文献主源 — 配置网关/API key/账号密码即启用",
                "env_vars": [
                    {"key": "CNKI_API_KEY", "prompt": "CNKI API Key",
                     "url": "..."},
                    {"key": "CNKI_USERNAME", "prompt": "知网账号"},
                    {"key": "CNKI_PASSWORD", "prompt": "知网密码"},
                ],
            }

        Default: minimal entry derived from ``display_name``.
        """
        return {
            "name": self.display_name,
            "badge": "",
            "tag": "",
            "env_vars": [],
        }
