"""Local literature-library configuration store.

A *local literature library* is a folder (or USB volume) the user has already
prepared — full of PDFs, a Zotero/BibTeX export, RIS files, etc. Unlike the
HTTP-based providers (CNKI, OpenAlex, custom card-gateway portals), a local
library needs no network and, crucially, gives the agent **real full text** to
quote and verify against.

This module persists the *configuration* of local libraries (id + root path +
label) to ``~/.vermes/literature_local_sources.json``. The actual indexing
(metadata + lazy full-text extraction) lives in
:mod:`agent.local_library_index`; this file only owns the bookkeeping so the
UI and the registry can enumerate "which local libraries exist".

No secrets are involved — a root path is not confidential — so we persist it in
plain JSON (not in ``.env``).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

logger = __import__("logging").getLogger(__name__)

_LOCAL_SOURCES_PATH = os.path.join(
    os.path.expanduser("~/.vermes"), "literature_local_sources.json"
)


def _ensure_dir() -> None:
    d = os.path.dirname(_LOCAL_SOURCES_PATH)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass


def _load_all() -> List[Dict[str, Any]]:
    if not os.path.exists(_LOCAL_SOURCES_PATH):
        return []
    try:
        with open(_LOCAL_SOURCES_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (ValueError, OSError) as exc:  # noqa: BLE001
        logger.warning("local_library_store: 读取失败: %s", exc)
    return []


def _save_all(libs: List[Dict[str, Any]]) -> None:
    _ensure_dir()
    tmp = _LOCAL_SOURCES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(libs, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _LOCAL_SOURCES_PATH)


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "_", text)
    return text.strip("_")[:32] or "lib"


def add_local_library(
    root: str, label: Optional[str] = None, *, description: str = ""
) -> Dict[str, Any]:
    """Register a local literature library by its root folder.

    Validates that *root* exists and is readable. Returns the persisted record
    (with a generated ``id``). Raises ``ValueError`` for bad input.
    """
    root = (root or "").strip()
    if not root:
        raise ValueError("本地文献库路径不能为空")
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        raise ValueError(f"路径不存在或不是文件夹: {root}")
    if not os.access(root, os.R_OK):
        raise ValueError(f"路径不可读（权限不足）: {root}")

    libs = _load_all()
    # de-dupe by root
    norm = os.path.realpath(root)
    for existing in libs:
        if os.path.realpath(existing.get("root", "")) == norm:
            return existing

    rid = "local_" + (_slug(label or os.path.basename(root)) or "lib")
    rid = f"{rid}_{uuid.uuid4().hex[:6]}"
    rec = {
        "id": rid,
        "root": root,
        "label": (label or os.path.basename(root) or rid).strip(),
        "description": (description or "").strip(),
        "created_at": time.time(),
        "updated_at": time.time(),
        "indexed_at": None,
        "file_count": 0,
        "status": "pending",  # pending | indexed | error
    }
    libs.append(rec)
    _save_all(libs)
    return rec


def list_local_libraries() -> List[Dict[str, Any]]:
    return _load_all()


def get_local_library(lib_id: str) -> Optional[Dict[str, Any]]:
    for lib in _load_all():
        if lib.get("id") == lib_id:
            return lib
    return None


def update_local_library(lib_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    libs = _load_all()
    for lib in libs:
        if lib.get("id") == lib_id:
            for k, v in fields.items():
                if k in ("label", "description", "root"):
                    lib[k] = v
            lib["updated_at"] = time.time()
            _save_all(libs)
            return lib
    return None


def delete_local_library(lib_id: str) -> bool:
    libs = _load_all()
    kept = [l for l in libs if l.get("id") != lib_id]
    if len(kept) == len(libs):
        return False
    _save_all(kept)
    # drop the index rows for this library
    try:
        from agent.local_library_index import drop_library_index

        drop_library_index(lib_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("delete_local_library: 清理索引失败(可忽略): %s", exc)
    return True


def touch_indexed(lib_id: str, file_count: int, status: str = "indexed") -> None:
    libs = _load_all()
    for lib in libs:
        if lib.get("id") == lib_id:
            lib["indexed_at"] = time.time()
            lib["file_count"] = file_count
            lib["status"] = status
            lib["updated_at"] = time.time()
            _save_all(libs)
            return
