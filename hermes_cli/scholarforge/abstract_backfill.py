"""摘要回填 — 按 DOI 从 Semantic Scholar 补论文 abstract。

复用现有 SemanticScholarProvider（共享 S2 Key + 限流池）：
- ``fetch_abstract_by_doi``：单篇查询，返回归一化节点里的 abstract。
- ``backfill_project_abstracts``：批量回填项目文献库中缺失 abstract 的条目。

两者都接受可注入的 provider，便于无网单测。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.literature_providers.semanticscholar import SemanticScholarProvider
from hermes_cli.scholarforge.database import (
    list_literatures_for_backfill,
    set_literature_abstract,
)

logger = logging.getLogger(__name__)


def _get_s2_provider() -> SemanticScholarProvider:
    return SemanticScholarProvider()


def fetch_abstract_by_doi(
    doi: str, provider: Optional[SemanticScholarProvider] = None
) -> Dict[str, Any]:
    """按 DOI（或 S2 paperId）从 Semantic Scholar 补论文摘要。

    Returns::

        {"success": True, "title", "abstract", "doi", "url", "year", ...}
        {"success": False, "error": str}  # 含「该论文无可用摘要」情形
    """
    if not doi or not str(doi).strip():
        return {"success": False, "error": "请提供 DOI 或论文 ID"}
    prov = provider or _get_s2_provider()
    res = prov.get_paper(doi)
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "Semantic Scholar 查询失败")}
    paper = res.get("paper") or {}
    if not paper.get("abstract"):
        return {
            "success": False,
            "error": "该论文无可用摘要（S2 未收录 abstract）",
            "paper": paper,
        }
    return {
        "success": True,
        "title": paper.get("title", ""),
        "abstract": paper["abstract"],
        "doi": paper.get("doi", ""),
        "url": paper.get("url", ""),
        "year": paper.get("year", ""),
    }


def backfill_project_abstracts(
    project_id: int, provider: Optional[SemanticScholarProvider] = None
) -> Dict[str, Any]:
    """回填项目文献库中缺失 abstract 的条目（按 DOI 查 S2）。

    Returns::

        {"checked": int, "updated": int, "failed": int, "failed_list": [...]}
    """
    rows = list_literatures_for_backfill(project_id)
    checked = updated = 0
    failed_list: List[Dict[str, Any]] = []
    for r in rows:
        doi = (r.get("doi") or "").strip()
        if not doi:
            continue
        checked += 1
        res = fetch_abstract_by_doi(doi, provider=provider)
        if res.get("success") and res.get("abstract"):
            set_literature_abstract(r["id"], res["abstract"])
            updated += 1
        else:
            failed_list.append({"doi": doi, "error": res.get("error", "")})
    return {
        "checked": checked,
        "updated": updated,
        "failed": len(failed_list),
        "failed_list": failed_list,
    }
