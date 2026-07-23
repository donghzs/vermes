"""CNKI 知网 literature provider — credential-driven (pluggable).

Reuses the battle-tested multi-strategy fetcher in
:mod:`hermes_cli.scholarforge.cnki_fetcher` (user gateway → Wanfang API →
OpenAlex-CN fallback). A user activates this provider by supplying *either*:

* ``CNKI_GATEWAY_URL`` + ``CNKI_API_KEY`` (self-hosted gateway), or
* ``CNKI_API_KEY`` (official CNKI API), or
* ``WANFANG_API_KEY`` (Wanfang API — the fetcher can route through it).

The credential metadata is declared via :func:`register_service` so the
frontend renders the right setup form, and read back at call time through the
unified credential layer. No code change is needed to plug a new account in.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

# Declare credential metadata (idempotent — merges with the entry already made
# by scholarforge.cnki_fetcher so extra_fields accumulate across the tree).
register_service(
    "cnki",
    api_key_env_var="CNKI_API_KEY",
    base_url_env_var="CNKI_GATEWAY_URL",
    label="CNKI 知网",
    extra_fields=["CNKI_GATEWAY_URL", "CNKI_USERNAME", "CNKI_PASSWORD"],
)


def _cnki_paper_to_record(cnki_paper: Any, source: str) -> Dict[str, Any]:
    """Convert a ``scholarforge.cnki_fetcher.CnkiPaper`` to a PaperRecord dict."""
    return PaperRecord(
        title=getattr(cnki_paper, "title", "") or "",
        authors=list(getattr(cnki_paper, "authors", []) or []),
        year=str(getattr(cnki_paper, "year", "") or ""),
        journal=getattr(cnki_paper, "journal", "") or "",
        abstract=getattr(cnki_paper, "abstract", "") or "",
        cited_count=int(getattr(cnki_paper, "cited_count", 0) or 0),
        url=getattr(cnki_paper, "url", "") or "",
        doi=getattr(cnki_paper, "doi", "") or "",
        keywords=list(getattr(cnki_paper, "keywords", []) or []),
        source=source,
    ).to_dict()


class CnkiProvider(LiteratureProvider):
    """CNKI 知网 — Chinese literature primary source (credential-driven)."""

    @property
    def name(self) -> str:
        return "cnki"

    @property
    def display_name(self) -> str:
        return "CNKI 知网"

    def is_available(self) -> bool:
        # Available whenever any upstream strategy has credentials. The
        # fetcher itself will skip strategies it can't service.
        return bool(
            os.environ.get("CNKI_GATEWAY_URL")
            or get_api_key("cnki")
            or get_api_key("wanfang")
        )

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        try:
            from hermes_cli.scholarforge.cnki_fetcher import search_cnki
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"无法加载 CNKI fetcher: {exc}"}

        try:
            papers = asyncio.run(search_cnki(query, int(limit)))
        except Exception as exc:  # noqa: BLE001
            logger.error("CNKI search error: %s", exc)
            return {"success": False, "error": f"CNKI 检索失败: {exc}"}

        return {
            "success": True,
            "data": {"papers": [_cnki_paper_to_record(p, "cnki") for p in papers]},
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key 或账号密码",
            "tag": "中文文献主源 — 配置网关 / API key / 账号密码即启用（多策略自动降级）",
            "env_vars": [
                {
                    "key": "CNKI_GATEWAY_URL",
                    "prompt": "自建 CNKI 网关地址（可选，最稳定）",
                },
                {"key": "CNKI_API_KEY", "prompt": "CNKI API Key"},
                {"key": "CNKI_USERNAME", "prompt": "知网账号（用户名）"},
                {"key": "CNKI_PASSWORD", "prompt": "知网密码"},
                {"key": "WANFANG_API_KEY", "prompt": "万方 API Key（可经万方回退）"},
            ],
        }
