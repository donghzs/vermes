"""ScholarForge 引用图谱 — 基于 Semantic Scholar 学术图谱的一跳引文网络。

- 复用现有 :class:`SemanticScholarProvider`（共享 S2 API Key + 限流池）。
- 结果落 ``scholarforge.db`` 的 ``citation_graph_cache`` 表，30 天 TTL，
  避开 S2 严格的速率限制（无 Key 100 req/5min，有 Key 1 req/s）。
- 纯函数 + 可注入 provider，便于无网单测（mock provider 即可）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.literature_providers.semanticscholar import SemanticScholarProvider
from hermes_cli.scholarforge.database import (
    get_citation_graph_cache,
    set_citation_graph_cache,
)

logger = logging.getLogger(__name__)

_DEFAULT_KINDS = ["citations", "references", "recommendations"]


def _get_s2_provider() -> SemanticScholarProvider:
    return SemanticScholarProvider()


def build_citation_graph(
    paper_id: str,
    kinds: Optional[List[str]] = None,
    limit: int = 50,
    use_cache: bool = True,
    provider: Optional[SemanticScholarProvider] = None,
) -> Dict[str, Any]:
    """构建某论文的一跳引用图谱（被引/引证/推荐）。

    参数
    ----
    paper_id:  DOI（``10.x/...`` 或 ``DOI:10.x/...``）或 S2 论文 ID（40 位 hex）。
    kinds:     要获取的关系子集；默认三类全要。
    limit:     每类关系最多返回条数（1–100）。
    use_cache: 是否先查/后写本地缓存（默认 True）。
    provider:  可注入的 S2 provider（测试用），默认新建实例。

    返回
    ----
    ``{"success": bool, "paper_id", "cache_key", "cache_hit",
    "kinds", "counts", "data": {citations,references,recommendations},
    "node_count", "errors"}``
    """
    if not paper_id or not str(paper_id).strip():
        return {"success": False, "error": "请提供论文标识（DOI 或 Semantic Scholar 论文 ID）"}

    kinds = kinds or list(_DEFAULT_KINDS)
    prov = provider or _get_s2_provider()
    cache_key = prov._s2_paper_key(paper_id)

    if use_cache:
        cached = get_citation_graph_cache(cache_key)
        if cached is not None:
            cached["cache_hit"] = True
            return cached

    raw = prov.citation_graph(cache_key, limit=limit, kinds=tuple(kinds))
    if not raw.get("success"):
        return {
            "success": False,
            "error": raw.get("error", "Semantic Scholar 调用失败"),
            "errors": raw.get("errors", {}),
        }

    data = raw["data"]
    # 节点去重：优先 paperId，其次 doi，再次 title
    nodes: Dict[str, Any] = {}
    for n in (
        (data.get("citations") or [])
        + (data.get("references") or [])
        + (data.get("recommendations") or [])
    ):
        nid = n.get("paperId") or n.get("doi") or n.get("title")
        if nid and nid not in nodes:
            nodes[nid] = n

    payload = {
        "success": True,
        "paper_id": paper_id,
        "cache_key": cache_key,
        "cache_hit": False,
        "kinds": list(kinds),
        "counts": {
            "citations": len(data.get("citations") or []),
            "references": len(data.get("references") or []),
            "recommendations": len(data.get("recommendations") or []),
        },
        "data": data,
        "node_count": len(nodes),
        "errors": raw.get("errors", {}),
    }

    if use_cache:
        set_citation_graph_cache(cache_key, payload)
    return payload
