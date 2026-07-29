"""Wanfang 万方 literature provider — credential-driven (pluggable).

Uses the Wanfang Open API strategy from
:mod:`vermes_cli.scholarforge.cnki_fetcher` (reused verbatim — the private
``_fetch_via_wanfang`` helper is the canonical Wanfang implementation in-tree).
A user activates this provider by supplying *either* ``WANFANG_API_KEY`` or a
``WANFANG_USER`` / ``WANFANG_PASSWORD`` pair. Credential metadata is declared
via :func:`register_service`; no code change needed to plug an account in.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider
from agent.literature_providers.cnki import _cnki_paper_to_record
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

register_service(
    "wanfang",
    api_key_env_var="WANFANG_API_KEY",
    label="Wanfang 万方",
    category="literature",
    description="万方中文文献库 — API Key 或账号密码",
    url="https://www.wanfangdata.com.cn/",
    extra_fields=[
        {"key": "WANFANG_USER", "label": "万方账号（用户名）", "secret": False},
        {"key": "WANFANG_PASSWORD", "label": "万方密码", "secret": True},
    ],
)


class WanfangProvider(LiteratureProvider):
    """Wanfang 万方 — Chinese literature source (credential-driven)."""

    @property
    def name(self) -> str:
        return "wanfang"

    @property
    def display_name(self) -> str:
        return "Wanfang 万方"

    def is_available(self) -> bool:
        return bool(get_api_key("wanfang") or os.environ.get("WANFANG_USER"))

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        try:
            # Reuse the in-tree Wanfang strategy (private helper, stable within
            # the same repo/package).
            from vermes_cli.scholarforge.cnki_fetcher import _fetch_via_wanfang
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"无法加载 Wanfang fetcher: {exc}"}

        try:
            papers = asyncio.run(_fetch_via_wanfang(query, int(limit)))
        except Exception as exc:  # noqa: BLE001
            logger.error("Wanfang search error: %s", exc)
            return {"success": False, "error": f"Wanfang 检索失败: {exc}"}

        return {
            "success": True,
            "data": {"papers": [_cnki_paper_to_record(p, "wanfang") for p in papers]},
        }

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "API key 或账号密码",
            "tag": "中文文献源 — 配置 API key 或账号密码即启用",
            "env_vars": [
                {"key": "WANFANG_API_KEY", "prompt": "万方 API Key"},
                {"key": "WANFANG_USER", "prompt": "万方账号"},
                {"key": "WANFANG_PASSWORD", "prompt": "万方密码"},
            ],
        }
