"""Aggregate models.dev's full provider registry into a curated manifest.

This module is the backend half of the P0 "model capability catalog" feature.
It does NOT replace any existing logic — it reuses ``agent.models_dev.fetch_models_dev``
(the same on-disk cache the rest of Vermes reads) and produces a curated view
(``pinned`` / ``mainstream`` / ``longtail``) for the Settings UI.

Design rules (verified against current code, 2026-08-28):
  * Local model discovery (``/api/model/discover``, 8 known endpoints + 13 ports)
    and the chat-model dropdown (``localStorage['vermes-providers']`` + the
    ``providers-updated`` event) are NOT touched. This module only *adds* a
    read-only catalog of cloud providers.
  * The full 204-provider / 7424-model registry stays the source of truth
    (``models.dev`` is "an enrichment source, never a hard dependency").
    The frontend shows only the curated subset.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from agent.models_dev import fetch_models_dev

try:
    from vermes_constants import get_vermes_home
except Exception:  # pragma: no cover - import guard
    get_vermes_home = None

logger = logging.getLogger(__name__)

# ── 策展白名单（产品决策，离线也永远置顶于推荐区） ──
PINNED_IDS = [
    "vbit", "agnes", "scnet", "deepseek", "xiaomi",
    "qwen", "zhipu", "baidu", "moonshot", "hunyuan",
    "ollama", "local", "custom",
]

# ── 主流 provider（聚合时优先展示，约 30 个） ──
MAINSTREAM_IDS = [
    "openai", "anthropic", "google", "gemini", "deepseek", "qwen",
    "zhipu", "moonshot", "glm", "baidu", "hunyuan", "groq", "together",
    "openrouter", "mistral", "xai", "perplexity", "cohere", "minimax",
    "stepfun", "yi", "baichuan", "nvidia", "fireworks", "ollama-cloud",
    "vllm", "lmstudio", "novita", "meta-llama", "ai21",
]


def _cache_mtime() -> float:
    try:
        if get_vermes_home is not None:
            p = get_vermes_home() / "models_dev_cache.json"
            if p.exists():
                return p.stat().st_mtime
    except Exception:  # noqa: BLE001
        pass
    return time.time()


def _extract_caps(entry: Dict[str, Any]) -> List[str]:
    """Extract capability tags from a single models.dev model entry.

    Mirrors ``agent.models_dev.get_model_capabilities`` field mapping:
      tool_call → tools, reasoning → reasoning, modalities.input(image) → vision.
    """
    caps: set = set()
    if entry.get("tool_call"):
        caps.add("tools")
    if entry.get("reasoning"):
        caps.add("reasoning")
    mods = entry.get("modalities")
    if isinstance(mods, dict):
        ins = mods.get("input")
        if isinstance(ins, list) and "image" in ins:
            caps.add("vision")
    elif entry.get("attachment"):
        caps.add("vision")
    return sorted(caps)


def generate_capability_manifest(refresh: bool = False) -> Dict[str, Any]:
    """Aggregate the models.dev cache into a curated capability manifest.

    Returns a dict with: updated_at, total_providers, total_models, pinned,
    mainstream (list of provider meta), longtail_count, longtail_groups
    (first-letter buckets). Tolerates an empty/missing cache via fallback.
    """
    try:
        data = fetch_models_dev(force_refresh=refresh)
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_models_dev failed: %s", e)
        data = {}

    if not data:
        return _fallback_manifest()

    total_providers = len(data)
    total_models = 0
    meta: Dict[str, Dict[str, Any]] = {}

    for pid, p in data.items():
        if not isinstance(p, dict):
            continue
        models = p.get("models") or {}
        if not isinstance(models, dict):
            models = {}
        total_models += len(models)

        caps: set = set()
        latest: List[str] = []
        for mid, m in models.items():
            if not isinstance(m, dict):
                continue
            latest.append(mid)
            caps.update(_extract_caps(m))

        meta[pid] = {
            "id": pid,
            "name": p.get("name") or pid,
            "model_count": len(models),
            "capabilities": sorted(caps),
            "latest_models": latest[:5],
        }

    mainstream = [meta[pid] for pid in MAINSTREAM_IDS if pid in meta]
    pinned_present = [pid for pid in PINNED_IDS if pid in meta]
    longtail = [
        v for v in meta.values()
        if v["id"] not in MAINSTREAM_IDS and v["id"] not in PINNED_IDS
    ]
    longtail_groups: Dict[str, int] = {}
    for v in longtail:
        key = (v["id"][0].upper()) if v["id"] else "#"
        longtail_groups[key] = longtail_groups.get(key, 0) + 1

    return {
        "updated_at": _cache_mtime(),
        "total_providers": total_providers,
        "total_models": total_models,
        "pinned": pinned_present,
        "mainstream": mainstream,
        "longtail_count": len(longtail),
        "longtail_groups": dict(sorted(longtail_groups.items())),
    }


def _fallback_manifest() -> Dict[str, Any]:
    return {
        "updated_at": time.time(),
        "total_providers": 0,
        "total_models": 0,
        "pinned": PINNED_IDS,
        "mainstream": [],
        "longtail_count": 0,
        "longtail_groups": {},
        "note": (
            "models.dev cache unavailable (offline / not yet fetched). "
            "The capability catalog will populate once the cache is available."
        ),
    }
