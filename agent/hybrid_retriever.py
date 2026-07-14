"""HybridRetriever — 记忆语义检索层。

写时 embedding 存储，加载时静态排序，对话时 query 召回。
三层降级：embedding API → 词重叠 Jaccard → 空结果（原行为）。

Embedding API 凭证从 Vermes 原生配置读取：
  1. 当前默认 provider 的 api_key + base_url（config.yaml providers[id]）
  2. .env 文件中常见 embedding key（OPENAI_API_KEY 等）
  3. 均无 → 降级到 Jaccard
"""

from __future__ import annotations

import json
import logging
import os
import re
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
    """Resolve embedding API credentials from Vermes native config.

    Priority:
      1. Current default provider (from config.yaml)
      2. Common embedding API keys in .env (OPENAI_API_KEY, DEEPSEEK_API_KEY, etc.)
      3. Falls back to empty → triggers Jaccard fallback

    Returns (base_url, api_key, model).
    """
    # ── Path 1: current default provider ───────────────────────────
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent / "hermes_cli"))
        from config import load_config, load_env

        cfg = load_config()
        env = load_env()

        # Get default provider from config
        default_provider = cfg.get("model", {}).get("provider", "")
        if not default_provider:
            default_provider = cfg.get("provider", "")

        if default_provider:
            # Try config.yaml providers[id].{base_url, api_key}
            prov_cfg = cfg.get("providers", {}).get(default_provider, {})
            base_url = prov_cfg.get("base_url", "").rstrip("/")
            api_key = prov_cfg.get("api_key", "")

            # Fall back to .env via provider template env_key
            if not api_key:
                _tmpl_map = {
                    "openai":    ("OPENAI_API_KEY",    "https://api.openai.com/v1"),
                    "deepseek":  ("DEEPSEEK_API_KEY",  "https://api.deepseek.com/v1"),
                    "qwen":      ("QWEN_API_KEY",      "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                    "zhipu":     ("ZHIPU_API_KEY",     "https://open.bigmodel.cn/api/paas/v4"),
                    "doubao":    ("DOUBAO_API_KEY",    "https://ark.cn-beijing.volces.com/api/v3"),
                    "kimi":      ("KIMI_API_KEY",      "https://api.moonshot.cn/v1"),
                    "openrouter":("OPENROUTER_API_KEY","https://openrouter.ai/api/v1"),
                    "vbit":      ("VBIT_API_KEY",      "https://api.vbit.top/v1"),
                    "xiaomi":    ("XIAOMI_API_KEY",    "https://api.xiaomimimo.com/v1"),
                    "groq":      ("GROQ_API_KEY",      "https://api.groq.com/openai/v1"),
                    "together":  ("TOGETHER_API_KEY",  "https://api.together.xyz/v1"),
                    "gemini":    ("GEMINI_API_KEY",     "https://generativelanguage.googleapis.com/v1beta"),
                    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
                    "custom":    ("CUSTOM_API_KEY",    ""),
                }
                _entry = _tmpl_map.get(default_provider)
                if _entry:
                    _env_key, _default_base = _entry
                    api_key = env.get(_env_key, "")
                    if not base_url:
                        base_url = _default_base

            # Guess embedding model from provider
            _emb_model_map = {
                "openai":     "text-embedding-3-small",
                "deepseek":   "text-embedding-3-small",
                "qwen":       "text-embedding-v3",
                "zhipu":      "embedding-3",
                "doubao":     "embModel",
                "kimi":       "text-embedding-v1",
                "openrouter": "text-embedding-3-small",
                "vbit":       "text-embedding-3-small",
                "xiaomi":     "embModel",
                "groq":       "embed-english-v2",
                "together":   "togethercomputer/m2-bert-8k-base",
                "gemini":     "embedding-001",
                "anthropic":  "",
                "custom":     "text-embedding-3-small",
            }
            model = _emb_model_map.get(default_provider, "text-embedding-3-small")

            if api_key and base_url:
                return base_url.rstrip("/"), api_key, model
    except Exception as exc:
        logger.debug("_resolve_embedding_api Path1 failed: %s", exc)

    # ── Path 2: common env keys ─────────────────────────────────────
    _common_keys = [
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "QWEN_API_KEY",
        "ZHIPU_API_KEY", "DOUBAO_API_KEY", "KIMI_API_KEY",
        "OPENROUTER_API_KEY", "VBIT_API_KEY", "XIAOMI_API_KEY",
        "GROQ_API_KEY", "TOGETHER_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "CUSTOM_API_KEY",
        "ONEAPI_KEY",
    ]
    try:
        env = load_env()
        for key in _common_keys:
            val = env.get(key, "")
            if val:
                _base_map = {
                    "OPENAI_API_KEY":  "https://api.openai.com/v1",
                    "DEEPSEEK_API_KEY":"https://api.deepseek.com/v1",
                    "QWEN_API_KEY":   "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "ZHIPU_API_KEY":  "https://open.bigmodel.cn/api/paas/v4",
                    "DOUBAO_API_KEY": "https://ark.cn-beijing.volces.com/api/v3",
                    "KIMI_API_KEY":   "https://api.moonshot.cn/v1",
                    "OPENROUTER_API_KEY":"https://openrouter.ai/api/v1",
                    "VBIT_API_KEY":   "https://api.vbit.top/v1",
                    "XIAOMI_API_KEY": "https://api.xiaomimimo.com/v1",
                    "GROQ_API_KEY":   "https://api.groq.com/openai/v1",
                    "TOGETHER_API_KEY":"https://api.together.xyz/v1",
                    "GEMINI_API_KEY": "https://generativelanguage.googleapis.com/v1beta",
                    "ANTHROPIC_API_KEY":"https://api.anthropic.com/v1",
                    "CUSTOM_API_KEY": "",
                    "ONEAPI_KEY":     "https://api.openai.com/v1",
                }
                base = _base_map.get(key, "https://api.openai.com/v1")
                # OneAPI uses the configured base_url from config.yaml
                if key == "ONEAPI_KEY":
                    try:
                        cfg = load_config()
                        ob = cfg.get("oneapi", {}).get("base_url", "")
                        if ob:
                            base = ob.rstrip("/")
                        model = cfg.get("oneapi", {}).get("embedding_model", "text-embedding-3-small")
                    except Exception:
                        model = "text-embedding-3-small"
                else:
                    model = "text-embedding-3-small"
                return base, val, model
    except Exception as exc:
        logger.debug("_resolve_embedding_api Path2 failed: %s", exc)

    return "", "", ""


def _get_embedding(text: str) -> Optional[List[float]]:
    """调用 OpenAI 兼容 embedding API。返回 float 列表或 None。

    支持 base_url + api_key + model，自动处理 endpoint 差异。
    如果 API 返回 400/404（模型不支持 embedding），静默降级。
    """
    base_url, api_key, model = _resolve_embedding_api()
    if not api_key or not base_url:
        return None

    # 过滤掉明显不支持 embedding 的 provider
    _no_embed = {"", "https://api.anthropic.com/v1", "https://generativelanguage.googleapis.com/v1beta"}
    if base_url in _no_embed:
        return None

    try:
        import httpx
        # OneAPI/OpenRouter 等兼容端点
        endpoint = f"{base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "input": text[:8192]}

        resp = httpx.post(endpoint, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data["data"][0]["embedding"]
        # 静默降级：不支持的模型/endpoint
        logger.debug("Embedding API %s/%s returned %s", base_url, model, resp.status_code)
        return None
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


# ── Composite scoring ──────────────────────────────────────────────────

# Recency half-life: 7 days → score decays to 0.5 after 7 days
_RECENCY_HALF_LIFE_SECS = 7 * 86400


def _recency_score(created_at: str) -> float:
    """Recency score: exponential decay, 1.0 at now → 0.5 at half-life."""
    try:
        import datetime
        created = datetime.datetime.fromisoformat(created_at)
        age = (datetime.datetime.now() - created).total_seconds()
        return 2.0 ** (-age / _RECENCY_HALF_LIFE_SECS)
    except Exception:
        return 0.5


def _composite_search(
    query: str,
    top_k: int = 5,
    target_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Enhanced search with 4-factor composite scoring.

    Score = w_embed * embed_sim + w_jaccard * jaccard + w_recency * recency + w_freq * freq

    Weights: embed=0.45, jaccard=0.25, recency=0.15, freq=0.15
    If no embedding available, redistribute: jaccard=0.60, recency=0.25, freq=0.15
    """
    db_path = _get_db_path()
    if not db_path.exists():
        return []

    try:
        conn = _get_conn(db_path)
        if target_filter:
            rows = conn.execute(
                "SELECT content, target, vector, created_at FROM embeddings "
                "WHERE target = ? AND content IS NOT NULL",
                (target_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT content, target, vector, created_at FROM embeddings "
                "WHERE content IS NOT NULL"
            ).fetchall()
        conn.close()
    except Exception:
        return []

    if not rows:
        return []

    query_vec = _get_embedding(query)
    has_embedding = bool(query_vec)
    q_words = set(query.lower().split())

    scored: List[tuple[float, Dict[str, Any]]] = []
    for content, target, blob, created_at in rows:
        embed_sim = 0.0
        if has_embedding and blob:
            try:
                vec = _blob_to_vector(blob)
                embed_sim = max(0.0, _cosine_similarity(query_vec, vec))
            except Exception:
                embed_sim = 0.0

        c_words = set(content.lower().split())
        jaccard = len(q_words & c_words) / len(q_words | c_words) if (q_words and c_words) else 0.0
        recency = _recency_score(created_at or "")
        freq = 0.5  # default (access_count not yet in schema)

        if has_embedding:
            composite = 0.45 * embed_sim + 0.25 * jaccard + 0.15 * recency + 0.15 * freq
        else:
            composite = 0.60 * jaccard + 0.25 * recency + 0.15 * freq

        scored.append((composite, {
            "content": content,
            "target": target,
            "score": round(composite, 4),
            "embed_sim": round(embed_sim, 4),
            "jaccard": round(jaccard, 4),
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def rich_search(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Semantic hybrid search — embedding + Jaccard + recency + frequency.

    No intent routing, no synonym expansion, no language assumptions.
    The embedding model handles cross-lingual semantic matching natively;
    hardcoded patterns for zh/en only reduce recall for other languages
    and inject domain bias.

    Preferred entry point for production.
    """
    return _composite_search(query, top_k=top_k)
