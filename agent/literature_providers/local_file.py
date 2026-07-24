"""Local-file literature provider.

Wraps a user-prepared folder (PDFs / BibTeX / RIS export) as a
:class:`agent.literature_provider.LiteratureProvider`. Unlike HTTP providers,
a local library is **always available** (when its folder is mounted) and,
critically, supports *full text* — the agent can pull and quote the actual
paper the user already owns.

Indexing/metadata + lazy full-text extraction live in
:mod:`agent.local_library_index`; this class is the thin provider-facing shell.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from agent.literature_provider import LiteratureProvider, PaperRecord

logger = logging.getLogger(__name__)


class LocalFileProvider(LiteratureProvider):
    """A single local literature library rooted at a folder."""

    def __init__(self, definition: Dict[str, Any]):
        self._id = definition.get("id") or "local"
        self.label = definition.get("label", self._id)
        self._root = (definition.get("root") or "").strip()

    # ── identity / capability ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self.label

    def is_available(self) -> bool:
        # Cheap, no I/O beyond a stat. USB unmounted → False (graceful).
        return bool(self._root) and os.path.isdir(self._root) and os.access(
            self._root, os.R_OK
        )

    def supports_search(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return True

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.label,
            "badge": "本地文件夹",
            "tag": "用户本地已备文献库（PDF / BibTeX / RIS），可全文引用与核实",
            "env_vars": [],
        }

    # ── search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        if not self.is_available():
            return {
                "success": False,
                "error": f"本地文献库 '{self.label}' 当前不可用（文件夹不存在或不可读，可能 USB 已拔出）",
            }
        try:
            from agent.local_library_index import search_local

            papers = search_local(self._id, query, limit)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LocalFileProvider.search(%s) failed: %s", self._id, exc)
            return {"success": False, "error": f"本地检索异常: {exc}"}
        return {
            "success": True,
            "data": {
                "papers": [PaperRecord.from_dict(p).to_dict() for p in papers],
                "source": self._id,
                "count": len(papers),
            },
        }

    # ── full text ──────────────────────────────────────────────────────────────

    def fetch_fulltext(self, paper: PaperRecord, **kwargs: Any) -> Dict[str, Any]:
        path = getattr(paper, "local_path", "") or (paper.to_dict().get("local_path") or "")
        if not path or not os.path.exists(path):
            return {"success": False, "error": "本地文件路径缺失或不存在"}
        try:
            from agent.local_library_index import ensure_fulltext

            text = ensure_fulltext(self._id, path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LocalFileProvider.fetch_fulltext failed: %s", exc)
            return {"success": False, "error": f"全文提取失败: {exc}"}
        if not text:
            return {
                "success": False,
                "error": "无法提取全文（可能非 PDF，或本机无 PyMuPDF）",
                "pdf_path": path,
            }
        return {
            "success": True,
            "data": {"content": text, "pdf_path": path, "source": self._id},
        }
