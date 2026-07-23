"""
Literature search tool
=======================

Registers the ``literature_search`` tool, which dispatches to the active
literature provider resolved by :mod:`agent.literature_registry`. The built-in
providers (OpenAlex, Crossref, CNKI, Wanfang) are bootstrapped when this module
is imported by ``tools.registry`` (which auto-imports every ``tools/*.py`` that
calls ``registry.register`` at module level).

User-supplied accounts / API keys are picked up automatically: the registry
prefers a credentialed Chinese source (CNKI/Wanfang) when available and falls
back to the always-free OpenAlex / Crossref sources otherwise — no code change
needed to plug a new account in.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from tools.registry import registry

from agent.literature_registry import (
    bootstrap_builtin_providers,
    bootstrap_custom_providers,
    get_active_literature_provider,
    get_provider_by_ref,
)

# Ensure bundled + custom providers are registered the moment this tool loads.
bootstrap_builtin_providers()
bootstrap_custom_providers()

LITERATURE_SEARCH_SCHEMA = {
    "name": "literature_search",
    "description": (
        "检索学术文献（知网 / 万方 / OpenAlex / Crossref / PubMed / arXiv / Scopus / IEEE / WOS 等内置源，"
        "以及用户在设置中自建的自定义文献库）。"
        "用户配置了知网或万方凭证时自动优先使用付费中文源；否则使用免费开放源；"
        "也可用 source 参数显式指定任意源。"
        "返回论文标题、作者、年份、期刊、摘要、引用数、DOI 与链接。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词或研究主题",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "返回文献条数上限（1-50）",
            },
            "source": {
                "type": "string",
                "description": "指定文献源：openalex / crossref / cnki / wanfang / pubmed / arxiv / scopus / ieee / wos 等内置源，或用户在设置中自建的自定义文献库 id（可选，默认按凭证自动选最优源）",
            },
        },
        "required": ["query"],
    },
}


async def _handle_literature_search(args: Dict[str, Any]) -> str:
    """Handler for the ``literature_search`` tool."""
    args = args or {}
    query = args.get("query")
    if not query or not str(query).strip():
        return json.dumps(
            {"success": False, "error": "query 不能为空"}, ensure_ascii=False
        )

    try:
        limit = int(args.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))

    source = args.get("source")

    # Idempotent — picks up any user-registered (built-in or custom) providers too.
    bootstrap_builtin_providers()
    bootstrap_custom_providers()

    provider = get_provider_by_ref(source) if source else get_active_literature_provider()

    if provider is None:
        return json.dumps(
            {
                "success": False,
                "error": (
                    "没有可用的文献源。可配置知网/万方凭证，"
                    "或使用免费的 OpenAlex/Crossref/PubMed/arXiv；"
                    "也可在设置 → 文献源 中添加自定义文献库。"
                ),
            },
            ensure_ascii=False,
        )

    if not provider.is_available():
        schema = provider.get_setup_schema()
        hint = " / ".join(ev.get("key", "") for ev in schema.get("env_vars", []))
        return json.dumps(
            {
                "success": False,
                "error": f"文献源 '{provider.name}' 当前不可用，请配置凭证：{hint}",
            },
            ensure_ascii=False,
        )

    try:
        # Providers expose a synchronous ``search``; run it off the event loop
        # so credentialed (HTTP/SSE) backends don't block the agent.
        result = await asyncio.to_thread(provider.search, query, limit)
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"success": False, "error": f"检索异常: {exc}"}, ensure_ascii=False
        )

    if not isinstance(result, dict):
        return json.dumps(
            {"success": False, "error": "provider 返回格式异常"}, ensure_ascii=False
        )
    return json.dumps(result, ensure_ascii=False)


registry.register(
    name="literature_search",
    toolset="literature",
    schema=LITERATURE_SEARCH_SCHEMA,
    handler=_handle_literature_search,
    is_async=True,
    emoji="📚",
    description="检索学术文献（知网/万方/OpenAlex/Crossref），凭证驱动自动选源",
)
