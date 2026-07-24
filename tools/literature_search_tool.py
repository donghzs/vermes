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
    bootstrap_local_providers,
    get_active_literature_provider,
    get_provider_by_ref,
    iter_local_providers,
)

# Ensure bundled + custom + local providers are registered the moment this tool loads.
bootstrap_builtin_providers()
bootstrap_custom_providers()
bootstrap_local_providers()

LITERATURE_SEARCH_SCHEMA = {
    "name": "literature_search",
    "description": (
        "检索学术文献（知网 / 万方 / OpenAlex / Crossref / PubMed / arXiv / Scopus / IEEE / WOS 等内置源，"
        "用户在设置中自建的自定义文献库，以及用户本地的文献文件夹/USB 文献库）。"
        "用户配置了知网或万方凭证时自动优先使用付费中文源；否则使用免费开放源；"
        "本地已索引的文献库会在普通检索中自动并入结果；"
        "也可用 source 参数显式指定任意源（含本地库 id）。"
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

    # Idempotent — picks up built-in, custom, AND local (folder/USB) providers.
    bootstrap_builtin_providers()
    bootstrap_custom_providers()
    bootstrap_local_providers()

    if source:
        provider = get_provider_by_ref(source) or get_active_literature_provider()
        return json.dumps(await _search_one(provider, query, limit), ensure_ascii=False)

    # Auto mode: active HTTP provider + every available local library, merged
    # and de-duplicated so the user's own papers surface alongside web results.
    candidates = []
    active = get_active_literature_provider()
    if active is not None:
        candidates.append(active)
    for lp in iter_local_providers():
        if lp.is_available() and lp not in candidates:
            candidates.append(lp)

    merged: list = []
    seen: set = set()
    sources_used: list = []
    for p in candidates:
        if not p.is_available():
            continue
        r = await asyncio.to_thread(p.search, query, limit)
        if not (isinstance(r, dict) and r.get("success")):
            continue
        sources_used.append(p.name)
        for paper in (r.get("data", {}) or {}).get("papers", []):
            key = (str(paper.get("title", "")).lower().strip(), str(paper.get("year", "")))
            if key in seen:
                continue
            seen.add(key)
            merged.append(paper)
        if len(merged) >= limit:
            break
    merged = merged[:limit]

    if not merged:
        # Nothing surfaced. If the active web provider is configured but
        # unavailable, surface its setup hint (e.g. missing API key) so the
        # user knows how to enable web search; local libraries simply had no
        # match for this query.
        if active is not None and not active.is_available():
            schema = active.get_setup_schema()
            hint = " / ".join(ev.get("key", "") for ev in schema.get("env_vars", []))
            return json.dumps(
                {
                    "success": False,
                    "error": (
                        f"文献源 '{active.name}' 当前不可用："
                        f"{hint or '文件夹不可读（可能 USB 已拔出）'}"
                    ),
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "success": False,
                "error": (
                    "没有可用的文献源。可配置知网/万方凭证，"
                    "或使用免费的 OpenAlex/Crossref/PubMed/arXiv；"
                    "也可在设置 → 文献源 中添加自定义文献库或本地文献文件夹。"
                ),
            },
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "success": True,
            "data": {"papers": merged, "sources": sources_used, "count": len(merged)},
        },
        ensure_ascii=False,
    )


async def _search_one(provider, query: str, limit: int) -> dict:
    """Search a single explicitly-named provider, with clear error shaping."""
    if provider is None:
        return {
            "success": False,
            "error": "未找到指定的文献源（检查 source 参数或先在设置中添加该源）",
        }
    if not provider.is_available():
        schema = provider.get_setup_schema()
        hint = " / ".join(ev.get("key", "") for ev in schema.get("env_vars", []))
        return {
            "success": False,
            "error": f"文献源 '{provider.name}' 当前不可用：{hint or '文件夹不可读（可能 USB 已拔出）'}",
        }
    try:
        # Providers expose a synchronous ``search``; run it off the event loop
        # so credentialed (HTTP/SSE) backends don't block the agent.
        result = await asyncio.to_thread(provider.search, query, limit)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"检索异常: {exc}"}
    if not isinstance(result, dict):
        return {"success": False, "error": "provider 返回格式异常"}
    return result


registry.register(
    name="literature_search",
    toolset="literature",
    schema=LITERATURE_SEARCH_SCHEMA,
    handler=_handle_literature_search,
    is_async=True,
    emoji="📚",
    description="检索学术文献（知网/万方/OpenAlex/Crossref/本地文献库），凭证/本地库驱动自动选源",
)
