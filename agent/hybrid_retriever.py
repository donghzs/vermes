"""HybridRetriever — 记忆语义检索层。

写时 embedding 存储，加载时静态排序，对话时 query 召回。
三层降级：embedding API → 词重叠 Jaccard → 空结果（原行为）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 存储 ────────────────────────────────────────────────────────────

_EMBEDDING_DIMS = 1536  # text-embedding-ada-002 默认维度


def _get_index_dir() -> Path:
    """Get or create the index directory under HERMES_HOME."""
    from hermes_constants import get_hermes_home
    index_dir = get_hermes_home() / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    return index_dir


def _get_db_path() -> Path:
    return _get_index_dir() / "embeddings.db"


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    db_path = db_path or _get_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS embeddings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_hash TEXT UNIQUE NOT NULL,
        content TEXT NOT NULL,
        target TEXT NOT NULL DEFAULT 'memory',
        vector BLOB,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS embedding_config (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    return conn


# ── 写时 embedding ────────────────────────────────────────────────────

def _resolve_embedding_api() -> tuple[str, str, str]:
    """Resolve embedding API credentials. 优先 ONEAPI_KEY → OPENAI_API_KEY → 空。"""
    base_url = os.environ.get("ONEAPI_BASE_URL") or os.environ.get("OPENAI_BASE_URL", "")
    api_key = os.environ.get("ONEAPI_KEY") or os.environ.get("OPENAI_API_KEY", "")
    model = "text-embedding-ada-002"
    if not base_url:
        base_url = "https://api.openai.com/v1"
    if not api_key:
        return "", "", ""
    return base_url.rstrip("/"), api_key, model


def _get_embedding(text: str) -> Optional[List[float]]:
    """调用 OpenAI 兼容 embedding API。返回 float 列表或 None。"""
    base_url, api_key, model = _resolve_embedding_api()
    if not api_key:
        return None
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "input": text},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.debug("Embedding API returned %s", resp.status_code)
            return None
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as exc:
        logger.debug("Embedding API call failed: %s", exc)
        return None


def _vector_to_blob(vec: List[float]) -> bytes:
    """Pack float32 list into binary BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vector(blob: bytes) -> List[float]:
    """Unpack binary BLOB to float32 list."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def store_embedding(content: str, target: str = "memory") -> None:
    """写入或更新 embedding。幂等（content_hash UNIQUE）。"""
    if not content.strip():
        return
    vec = _get_embedding(content)
    blob = _vector_to_blob(vec) if vec else None
    try:
        conn = _get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO embeddings (content_hash, content, target, vector, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(hash(content)), content[:500], target, blob, __import__("datetime").datetime.now().isoformat()),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("store_embedding failed: %s", exc)


def delete_embedding(content: str) -> None:
    """按内容删除 embedding。"""
    if not content.strip():
        return
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM embeddings WHERE content_hash = ?", (str(hash(content)),))
        conn.commit()
    except Exception as exc:
        logger.debug("delete_embedding failed: %s", exc)


# ── 加载时排序 ────────────────────────────────────────────────────────

def _is_kv_pair(entry: str) -> bool:
    """判断是否为 KV 对：含 → 或短内容前有冒号。"""
    if "→" in entry:
        return True
    idx = entry.find("：")
    if 0 < idx < 30:
        return True
    return False


def rank_entries(entries: List[str]) -> List[str]:
    """静态三级排序：KV 对 → 无 embedding 时按原顺序保留。"""
    if not entries:
        return entries
    kvs = [e for e in entries if _is_kv_pair(e)]
    others = [e for e in entries if not _is_kv_pair(e)]
    return kvs + others


# ── 对话时 query 召回 ─────────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def _jaccard_overlap(query: str, content: str) -> float:
    """退降级：词重叠 Jaccard 相似度。"""
    q_words = set(query.lower().split())
    c_words = set(content.lower().split())
    if not q_words or not c_words:
        return 0.0
    return len(q_words & c_words) / len(q_words | c_words)


def search(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """语义检索 top-k 记忆条目。

    降级链：embedding cosine → Jaccard 词重叠 → 空结果。
    """
    if not query.strip():
        return []
    db_path = _get_db_path()
    if not db_path.exists():
        return []
    try:
        conn = _get_conn(db_path)
        rows = conn.execute(
            "SELECT content, target, vector FROM embeddings WHERE vector IS NOT NULL"
        ).fetchall()
    except Exception:
        return []

    query_vec = _get_embedding(query)
    if query_vec and rows:
        # Layer 1: 余弦相似度
        scored = []
        for content, target, blob in rows:
            vec = _blob_to_vector(blob)
            sim = _cosine_similarity(query_vec, vec)
            scored.append((sim, {"content": content, "target": target, "score": round(sim, 4)}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # Layer 2: Jaccard 降级
    scored = []
    for content, target, _ in rows:
        sim = _jaccard_overlap(query, content)
        if sim > 0.1:
            scored.append((sim, {"content": content, "target": target, "score": round(sim, 4)}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]
