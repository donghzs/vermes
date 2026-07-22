"""HybridRetriever — 记忆语义检索层。

写时 embedding 存储，加载时静态排序，对话时 query 召回。
三层降级：embedding API → 词重叠 Jaccard → 空结果（原行为）。

Embedding API 凭证跟随用户配置的 provider：
  1. 默认 provider 的 base_url + api_key（config.yaml）
  2. 任意已配置且有凭证的 provider
  3. .env 中 *_API_KEY 环境变量 + provider template 推导
  4. 均无 → 降级到 Jaccard

Embedding model 自动发现：
  - 用户可在 config.yaml embedding.model 显式指定
  - 未指定时，首次调用 /embeddings 失败则查询 /v1/models 自动发现
  - 发现结果缓存到 SQLite，避免重复查询
  - 失败的 provider 缓存到 _FAILED_EMBED_PROVIDERS，本进程不再重试
  - provider 切换时自动清空失败缓存，允许重新尝试新 provider
  - 发现结果缓存到 SQLite（7天 TTL），跟随 credential fingerprint 失效
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

# Default embedding model — used as first probe for any OpenAI-compatible provider.
# If the provider doesn't support it, _get_embedding() silently falls back to Jaccard.
_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# Providers that are known to NOT support /embeddings endpoint.
# This is NOT a hardcode ban — it's a performance shortcut to avoid
# a guaranteed-to-fail HTTP call. If a provider starts supporting
# embeddings, removing its entry here is the only change needed.
_NO_EMBED_PROVIDERS = frozenset({
    "https://api.anthropic.com/v1",  # Claude has no embeddings API
})

# Runtime cache of providers that failed /embeddings calls.
# Populated by _get_embedding() on 400/404/501 responses.
# Cleared on process restart — provider may have added support.
_FAILED_EMBED_PROVIDERS: set[str] = set()

# Track which provider credentials we last resolved.
# If the user changes provider, we invalidate stale caches.
_LAST_RESOLVED_KEY: str = ""

# Cache TTL for discovered embedding models (7 days).
# Providers upgrade their model lineup; stale caches prevent
# us from picking up newer/better embedding models.
_EMBED_MODEL_CACHE_TTL_SECS = 7 * 86400

# Opt-in semantic rerank: blend the composite score with query↔candidate
# embedding cosine similarity. OFF by default (zero behavior change); enable
# via env VERMES_RERANK_EMBEDDING=1. Fail-open: any error → unchanged order.
_EMBEDDING_RERANK_ENABLED = os.environ.get("VERMES_RERANK_EMBEDDING", "").lower() in ("1", "true", "yes")
_EMBEDDING_RERANK_BLEND = 0.3  # weight given to semantic cosine vs composite score


def _resolve_embedding_api() -> tuple[str, str, str]:
    """Resolve embedding API credentials from user's configured provider.

    Philosophy: follow whatever the user configured. No hardcoded provider
    lists, no assumed embedding model names. Try the user's default provider
    first; if it fails at call time, Jaccard fallback kicks in.

    Resolution order:
      1. Default provider from config.yaml (providers[id].{base_url, api_key})
      2. Any provider in config.yaml that has both base_url and api_key
      3. .env fallback: scan for *_API_KEY vars, derive base_url from same config
      4. Empty → Jaccard fallback

    Returns (base_url, api_key, model).
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent.parent / "hermes_cli"))
        from config import load_config, load_env

        cfg = load_config()
        env = load_env()
        model = cfg.get("embedding", {}).get("model", _DEFAULT_EMBEDDING_MODEL)

        # ── Path 1: default provider from config ─────────────────────
        default_provider = cfg.get("model", {}).get("provider", "")
        if not default_provider:
            default_provider = cfg.get("provider", "")

        if default_provider:
            prov_cfg = cfg.get("providers", {}).get(default_provider, {})
            base_url = prov_cfg.get("base_url", "").rstrip("/")
            api_key = prov_cfg.get("api_key", "")

            # Fall back to .env: try {PROVIDER}_API_KEY pattern
            if not api_key:
                env_key = f"{default_provider.upper().replace('-', '_')}_API_KEY"
                api_key = env.get(env_key, "")

            if not base_url:
                # Try provider template from blueprints
                try:
                    from hermes_cli.blueprints.providers import PROVIDER_TEMPLATES
                    tmpl = PROVIDER_TEMPLATES.get(default_provider, {})
                    base_url = tmpl.get("base_url", "").rstrip("/")
                    if not api_key:
                        env_key = tmpl.get("api_key_env", "")
                        if env_key:
                            api_key = env.get(env_key, "")
                except Exception as e:
                    logger.debug("hybrid_retriever.py:  resolve embedding api failed: %s", e)

            if api_key and base_url and base_url not in _NO_EMBED_PROVIDERS:
                return base_url, api_key, model

        # ── Path 2: any configured provider with credentials ────────
        providers = cfg.get("providers", {})
        if isinstance(providers, dict):
            for pid, pcfg in providers.items():
                if not isinstance(pcfg, dict):
                    continue
                burl = pcfg.get("base_url", "").rstrip("/")
                akey = pcfg.get("api_key", "")
                if not akey:
                    env_key = f"{pid.upper().replace('-', '_')}_API_KEY"
                    akey = env.get(env_key, "")
                if akey and burl and burl not in _NO_EMBED_PROVIDERS:
                    return burl, akey, model

        # ── Path 3: scan .env for any *_API_KEY ──────────────────────
        for env_key, env_val in env.items():
            if not env_val or not env_key.endswith("_API_KEY"):
                continue
            # Derive base_url from provider templates
            try:
                from hermes_cli.blueprints.providers import PROVIDER_TEMPLATES
                pid = env_key.replace("_API_KEY", "").lower().replace("_", "-")
                tmpl = PROVIDER_TEMPLATES.get(pid, {})
                burl = tmpl.get("base_url", "").rstrip("/")
                if burl and burl not in _NO_EMBED_PROVIDERS:
                    return burl, env_val, model
            except Exception as e:
                logger.debug("hybrid_retriever.py:  resolve embedding api failed: %s", e)

    except Exception as exc:
        logger.debug("_resolve_embedding_api failed: %s", exc)

    return "", "", ""


def _discover_embedding_model(base_url: str, api_key: str) -> str:
    """Auto-discover embedding model from /v1/models endpoint.

    Queries the provider's model list, finds entries containing 'embed',
    and caches the result in SQLite with a TTL. Falls back to
    _DEFAULT_EMBEDDING_MODEL.

    TTL ensures we pick up new models when providers upgrade.
    """
    import datetime
    cache_key = f"embed_model::{base_url}"
    cache_ts_key = f"embed_model_ts::{base_url}"

    # Check cache + TTL
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT value FROM embedding_config WHERE key = ?", (cache_key,)
        ).fetchone()
        ts_row = conn.execute(
            "SELECT value FROM embedding_config WHERE key = ?", (cache_ts_key,)
        ).fetchone()
        if row and row[0] and ts_row and ts_row[0]:
            cached_time = datetime.datetime.fromisoformat(ts_row[0])
            age = (datetime.datetime.now() - cached_time).total_seconds()
            if age < _EMBED_MODEL_CACHE_TTL_SECS:
                return row[0]
            logger.debug("Embedding model cache for %s expired (%.1f days old), re-discovering", base_url, age / 86400)
    except Exception as e:
        logger.debug("hybrid_retriever.py:  discover embedding model failed: %s", e)

    try:
        import httpx
        resp = httpx.get(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            models = resp.json().get("data", [])
            # Find embedding models — prefer small/fast ones
            embed_models = [
                m["id"] for m in models
                if "embed" in m.get("id", "").lower()
            ]
            if embed_models:
                # Prefer 'small' or 'lite' variants for speed
                chosen = next(
                    (m for m in embed_models if "small" in m.lower() or "lite" in m.lower()),
                    embed_models[0],
                )
                # Cache it with timestamp
                try:
                    conn = _get_conn()
                    now = datetime.datetime.now().isoformat()
                    conn.execute(
                        "INSERT OR REPLACE INTO embedding_config (key, value) VALUES (?, ?)",
                        (cache_key, chosen),
                    )
                    conn.execute(
                        "INSERT OR REPLACE INTO embedding_config (key, value) VALUES (?, ?)",
                        (cache_ts_key, now),
                    )
                    conn.commit()
                except Exception as e:
                    logger.debug("hybrid_retriever.py:  discover embedding model failed: %s", e)
                logger.info("Discovered embedding model for %s: %s", base_url, chosen)
                return chosen
    except Exception as exc:
        logger.debug("_discover_embedding_model failed for %s: %s", base_url, exc)

    return _DEFAULT_EMBEDDING_MODEL


def _get_embedding(text: str) -> Optional[List[float]]:
    """Call OpenAI-compatible /embeddings endpoint. Returns float list or None.

    If a provider returns 404/400 (no embedding support), it's cached in
    _FAILED_EMBED_PROVIDERS so we don't retry every turn. Restarts clear
    the cache — provider may have added embedding support meanwhile.

    If the user switches provider (detected via credential change),
    _FAILED_EMBED_PROVIDERS is cleared automatically.
    """
    global _LAST_RESOLVED_KEY

    base_url, api_key, model = _resolve_embedding_api()
    if not api_key or not base_url:
        return None

    # Detect provider switch: if credentials changed, clear stale failure cache
    current_key = f"{base_url}::{api_key[:4]}****"
    if current_key != _LAST_RESOLVED_KEY:
        if _FAILED_EMBED_PROVIDERS:
            logger.debug("Provider changed (%s → %s), clearing failure cache", _LAST_RESOLVED_KEY, current_key)
        _FAILED_EMBED_PROVIDERS.clear()
        _LAST_RESOLVED_KEY = current_key

    if base_url in _NO_EMBED_PROVIDERS or base_url in _FAILED_EMBED_PROVIDERS:
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

        # Model not found? Try auto-discovering the right embedding model
        if resp.status_code == 400 and model == _DEFAULT_EMBEDDING_MODEL:
            discovered = _discover_embedding_model(base_url, api_key)
            if discovered != model:
                payload["model"] = discovered
                resp = httpx.post(endpoint, headers=headers, json=payload, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["data"][0]["embedding"]

        # Cache failure for unsupported providers (404/400/501)
        if resp.status_code in (400, 404, 501):
            _FAILED_EMBED_PROVIDERS.add(base_url)
            logger.info(
                "Provider %s doesn't support /embeddings (HTTP %d) — caching for this session, falling back to Jaccard",
                base_url, resp.status_code,
            )
        else:
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
    """写入或更新 embedding。幂等（content_hash UNIQUE）。

    Fire-and-forget: 实际写入在后台线程执行，调用方无需等待网络 I/O。
    Embedding 是"写时"操作，不需要同步等结果，异步化能显著降低交互延迟。
    """
    if not content.strip():
        return

    def _do_store():
        try:
            vec = _get_embedding(content)
            blob = _vector_to_blob(vec) if vec else None
            conn = _get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO embeddings (content_hash, content, target, vector, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(hash(content)), content[:500], target, blob, __import__("datetime").datetime.now().isoformat()),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("store_embedding failed: %s", exc)

    threading.Thread(target=_do_store, daemon=True, name="embed-store").start()


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


def _rerank_by_query_embedding(
    candidates: List[Dict[str, Any]],
    query: str,
    top_k: int,
    blend: float = _EMBEDDING_RERANK_BLEND,
) -> List[Dict[str, Any]]:
    """Optional semantic rerank: blend composite score with query↔candidate
    embedding cosine similarity.

    Reuses each candidate's STORED write-time embedding vector (a local DB read,
    no extra API calls per candidate). Fail-open: any error returns candidates
    unchanged, so the production recall path is never broken by a rerank failure.

    This is the lean, zero-new-dependency reranker. A heavier local
    cross-encoder (bge/cohere) can later hang off the same hook, but that
    requires un-excluding torch/transformers in the PyInstaller spec.
    """
    if not candidates:
        return candidates
    try:
        q_vec = _get_embedding(query)
        if not q_vec:
            return candidates
        db_path = _get_db_path()
        if not db_path.exists():
            return candidates
        conn = _get_conn(db_path)
        try:
            reranked: List[tuple[float, Dict[str, Any]]] = []
            for cand in candidates:
                content = cand.get("content", "")
                row = conn.execute(
                    "SELECT vector FROM embeddings WHERE content = ? LIMIT 1", (content,)
                ).fetchone()
                cand_vec = _blob_to_vector(row[0]) if row and row[0] else None
                sim = _cosine_similarity(q_vec, cand_vec) if cand_vec else 0.0
                base = float(cand.get("score", 0.0))
                new_score = (1.0 - blend) * base + blend * max(0.0, sim)
                reranked.append((new_score, cand))
        finally:
            conn.close()
        reranked.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in reranked[:top_k]]
    except Exception as exc:
        logger.debug("embedding rerank skipped (fail-open): %s", exc)
        return candidates


def rich_search(query: str, top_k: int = 3, rerank: Optional[bool] = None) -> List[Dict[str, Any]]:
    """Semantic hybrid search — embedding + Jaccard + recency + frequency.

    No intent routing, no synonym expansion, no language assumptions.
    The embedding model handles cross-lingual semantic matching natively;
    hardcoded patterns for zh/en only reduce recall for other languages
    and inject domain bias.

    Preferred entry point for production. When ``rerank`` is True (or the
    VERMES_RERANK_EMBEDDING env flag is set), results are re-ranked by
    query↔candidate embedding cosine similarity as a semantic boost.
    """
    results = _composite_search(query, top_k=top_k)
    if rerank is None:
        rerank = _EMBEDDING_RERANK_ENABLED
    if rerank:
        results = _rerank_by_query_embedding(results, query, top_k)
    return results
