"""Semantic Scholar literature provider — free; API key optional.

Uses the Semantic Scholar Graph API. Works without a key (shared rate pool);
supplying ``S2_API_KEY`` raises rate limits. The optional key is declared via
:func:`register_service` so it appears in the unified 文献源 settings form.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json
from agent.service_credentials import get_api_key, register_service

logger = logging.getLogger(__name__)

_S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,authors,year,venue,abstract,citationCount,externalIds,url"
# 引用图谱端点需要的字段（含 paperId 以便节点去重）
_CG_FIELDS = "title,authors,year,venue,abstract,citationCount,externalIds,url,paperId"

register_service(
    "semanticscholar",
    api_key_env_var="S2_API_KEY",
    label="Semantic Scholar",
    category="literature",
    description="免费学术图谱检索；API Key 可选（提升速率限制）",
    url="https://www.semanticscholar.org/product/api",
)


class SemanticScholarProvider(LiteratureProvider):
    """Free academic graph search via Semantic Scholar (optional API key)."""

    @property
    def name(self) -> str:
        return "semanticscholar"

    @property
    def display_name(self) -> str:
        return "Semantic Scholar (免费)"

    def is_available(self) -> bool:
        return True  # key optional — works without one

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        headers = {}
        key = get_api_key("semanticscholar")
        if key:
            headers["x-api-key"] = key
        res = http_get_json(
            _S2_URL,
            params={"query": query, "limit": min(int(limit), 50), "fields": _FIELDS},
            headers=headers,
        )
        if not res.get("ok"):
            return {"success": False, "error": f"Semantic Scholar 检索失败: {res.get('error')}"}

        papers = []
        for item in res["data"].get("data", []) or []:
            title = item.get("title", "")
            if not title:
                continue
            ext = item.get("externalIds") or {}
            papers.append(
                PaperRecord(
                    title=title,
                    authors=[a.get("name", "") for a in item.get("authors", []) or [] if a.get("name")],
                    year=str(item.get("year", "") or ""),
                    journal=item.get("venue", "") or "",
                    abstract=(item.get("abstract") or "")[:800],
                    cited_count=int(item.get("citationCount", 0) or 0),
                    url=item.get("url", "") or "",
                    doi=ext.get("DOI", "") or "",
                    keywords=[],
                    source="semanticscholar",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    # ──────────────────────────────────────────────────────────────
    # 引用图谱（一跳）：复用同一 S2 Key + 限流池
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _s2_paper_key(paper_id: str) -> str:
        """把用户传入的标识规范化为 S2 Graph API 可用的 paper key。

        - ``DOI:10.x/...`` 原样保留
        - 裸 DOI ``10.x/...`` → 前缀 ``DOI:``
        - 40 位 hex（S2 paperId）或 ARXIV:/PMID:/ACL: 前缀原样保留
        """
        pid = (paper_id or "").strip()
        if pid.lower().startswith("doi:"):
            return pid
        if re.fullmatch(r"10\.\d{4,9}/.+", pid):
            return f"DOI:{pid}"
        return pid

    @staticmethod
    def _normalize_s2_paper(item: Dict[str, Any]) -> Dict[str, Any]:
        """把 S2 Graph 返回的原始 paper 节点规整为统一字段。"""
        if not item:
            return {}
        ext = item.get("externalIds") or {}
        return {
            "title": item.get("title", "") or "",
            "authors": [
                a.get("name", "") for a in (item.get("authors") or []) if a.get("name")
            ],
            "year": str(item.get("year", "") or ""),
            "venue": item.get("venue", "") or "",
            "citationCount": int(item.get("citationCount", 0) or 0),
            "doi": ext.get("DOI", "") or "",
            "url": item.get("url", "") or "",
            "paperId": item.get("paperId", "") or "",
            "abstract": (item.get("abstract") or "")[:400],
        }

    def citation_graph(
        self,
        paper_id: str,
        limit: int = 50,
        kinds: tuple = ("citations", "references", "recommendations"),
    ) -> Dict[str, Any]:
        """返回某论文的一跳引用图谱（被引 / 引证 / 推荐）。

        端点（S2 Graph API）：
          - ``/paper/{key}/citations``      → 引用该论文的论文（入边）
          - ``/paper/{key}/references``     → 该论文引用的论文（出边）
          - ``/paper/{key}/recommendations``→ S2 相似论文推荐

        Returns::

            {"success": True,
             "data": {"citations": [...], "references": [...], "recommendations": [...]},
             "errors": {kind: err, ...}}
            {"success": False, "error": str, "errors": {...}}

        每个列表元素为统一字段的论文节点（见 ``_normalize_s2_paper``）。
        任一关系拉取失败只在 ``errors`` 中记录，不会让整体失败（部分可用）。
        """
        key = self._s2_paper_key(paper_id)
        headers: Dict[str, str] = {}
        s2key = get_api_key("semanticscholar")
        if s2key:
            headers["x-api-key"] = s2key
        base = f"https://api.semanticscholar.org/graph/v1/paper/{key}"
        errors: Dict[str, str] = {}
        data: Dict[str, list] = {"citations": [], "references": [], "recommendations": []}

        for kind in kinds:
            if kind not in ("citations", "references", "recommendations"):
                continue
            res = http_get_json(
                f"{base}/{kind}",
                params={"limit": min(int(limit), 100), "fields": _CG_FIELDS},
                headers=headers,
                timeout=30,
            )
            if not res.get("ok"):
                errors[kind] = res.get("error", "unknown")
                continue
            body = res.get("data", {}) or {}
            items = body.get("data", []) or []
            if kind == "citations":
                data["citations"] = [
                    self._normalize_s2_paper(d.get("citingPaper")) for d in items
                ]
            elif kind == "references":
                data["references"] = [
                    self._normalize_s2_paper(d.get("citedPaper")) for d in items
                ]
            else:  # recommendations — 节点直接是 paper
                data["recommendations"] = [
                    self._normalize_s2_paper(d) for d in items
                ]

        if not any(data.values()) and errors:
            return {
                "success": False,
                "error": "; ".join(errors.values()),
                "errors": errors,
            }
        return {"success": True, "data": data, "errors": errors}

    def get_paper(self, paper_id: str) -> Dict[str, Any]:
        """返回单篇论文的归一化节点（用于按 DOI 补 abstract 等）。

        Returns::

            {"success": True, "paper": {...normalized node...}}
            {"success": False, "error": str}
        """
        key = self._s2_paper_key(paper_id)
        headers: Dict[str, str] = {}
        s2key = get_api_key("semanticscholar")
        if s2key:
            headers["x-api-key"] = s2key
        res = http_get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/{key}",
            params={"fields": _CG_FIELDS},
            headers=headers,
            timeout=30,
        )
        if not res.get("ok"):
            return {"success": False, "error": res.get("error", "unknown")}
        return {"success": True, "paper": self._normalize_s2_paper(res.get("data", {}) or {})}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "free · key 可选",
            "tag": "免费学术图谱检索（引用数/摘要齐全）；可选 API Key 提升速率",
            "env_vars": [
                {
                    "key": "S2_API_KEY",
                    "prompt": "Semantic Scholar API Key（可选）",
                    "url": "https://www.semanticscholar.org/product/api",
                },
            ],
        }
