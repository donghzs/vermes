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

# ── 前端 curated id -> models.dev 真实 key 映射 ──
# 前端 Settings.vue 的 providers 数组用「产品内部 id」（如 qwen/zhipu/moonshot），
# 而 models.dev 缓存的 key 是厂商原生命名（alibaba/zhipuai/moonshotai…）。
# 审计发现：MAINSTREAM_IDS 直接用前端 id 去查 models.dev，30 个只命中 15 个，
# 导致能力目录只渲染 7 个 provider、8 个国产入口从目录块消失。
# 修复：聚合时按此映射取真实 key 的能力数据，但输出 meta「id」仍用前端 curated id，
# 使前端 capProviders（byId[m.id] 匹配 providers.value）能正确渲染为可配置 ProviderCard。
# 实跑核对（~/.vermes/models_dev_cache.json，2026-08-28）：baidu/hunyuan/yi/baichuan
# 在 models.dev 无对应条目（MISS），无能力数据可显示，诚实跳过（仍在国产组可配）。
# ollama/local/custom 是本地/自定义 provider，不映射任何云端 key。
PROVIDER_TO_MODELS_DEV = {
    "qwen": "alibaba",
    "zhipu": "zhipuai",
    "glm": "zhipuai",
    "moonshot": "moonshotai",
    "gemini": "google",
    "together": "togetherai",
    "fireworks": "fireworks-ai",
    "novita": "novita-ai",
    "meta-llama": "meta",
}


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


def build_provider_capability_index() -> Dict[str, List[str]]:
    """Return ``{provider_id: sorted capability tags}`` for ALL providers.

    Used by the P0-2 runtime ``CapabilityGateway`` to resolve a model's
    capability profile per agent step (O(1) lookup, never per-step I/O).
    Keys include both raw ``models.dev`` ids and the curated ids in
    ``PROVIDER_TO_MODELS_DEV`` (so the gateway can look up by either the wire
    provider id or the frontend curated id). ``ollama``/``local``/``custom``
    are seeded with empty capability lists so local-model steps resolve
    fail-open instead of being flagged as "unknown".

    fail-open: on a missing/unreadable cache returns ``{}`` — the gateway then
    records the step with ``capabilities=None`` rather than raising.
    """
    try:
        data = fetch_models_dev(force_refresh=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("build_provider_capability_index: fetch_models_dev failed: %s", e)
        return {}
    if not data:
        return {}

    idx: Dict[str, List[str]] = {}
    for pid, p in data.items():
        if not isinstance(p, dict):
            continue
        caps: set = set()
        models = p.get("models") or {}
        if not isinstance(models, dict):
            models = {}
        for m in models.values():
            if isinstance(m, dict):
                caps.update(_extract_caps(m))
        idx[pid] = sorted(caps)

    # Expose curated ids alongside raw models.dev ids.
    for cid, rk in PROVIDER_TO_MODELS_DEV.items():
        if rk in idx and cid not in idx:
            idx[cid] = idx[rk]
    for pid in ("ollama", "local", "custom"):
        idx.setdefault(pid, [])
    return idx


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

    def resolve_real_key(cid: str) -> str | None:
        """把前端 curated id 解析为 models.dev 真实 key（同名则直接命中）。"""
        if cid in meta:
            return cid
        rk = PROVIDER_TO_MODELS_DEV.get(cid)
        if rk and rk in meta:
            return rk
        return None

    # 已纳入策展（主流+置顶）的 models.dev 真实 key 集合，用于下游长尾排除
    curated_real_keys: set = set()

    mainstream = []
    for cid in MAINSTREAM_IDS:
        rk = resolve_real_key(cid)
        if rk is None:
            continue
        curated_real_keys.add(rk)
        entry = dict(meta[rk])
        entry["id"] = cid  # 输出用前端 curated id，对齐前端 providers.value
        mainstream.append(entry)

    pinned_present = []
    for pid in PINNED_IDS:
        rk = resolve_real_key(pid)
        if rk is not None:
            curated_real_keys.add(rk)
            pinned_present.append(pid)

    longtail = [v for k, v in meta.items() if k not in curated_real_keys]
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
