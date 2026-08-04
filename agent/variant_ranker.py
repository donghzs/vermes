"""Variant ranker — GRPO-inspired group-relative ranking of processor variants.

Phase 4-B of the Lego-style refactoring. This is the "scoring" half of the
closed loop: it reads per-variant tool outcomes from ``raw_events`` (attributed
in P4-A via ``variant_hash``), computes a reward for each variant, then ranks
variants **within their processor group** using group-relative standardization
(the GRPO flavor — relative advantage, not absolute threshold).

Honest scope (per design §0 + risk notes):
  - The live reward signal is ``success_rate`` (the hard outcome we actually
    capture per-variant). ``thumbs`` (explicit user feedback) and ``re-ask``
    (implicit negative signal) are wired as pluggable weight hooks but default
    to 0 until their per-variant capture lands (re-ask detection deferred to
    P4.5). We do NOT pretend to use signals we don't have.
  - "GRPO" here = group-relative ranking for variant *selection* (evolutionary
    selection on harness config), NOT policy-gradient training. We cannot
    retrain the base model.

Cold start (ε-exploration, design 拍板 ⑥):
  - A variant with fewer than ``EXPLORATION_K`` samples is in "exploring" mode:
    its score is shrunk toward the group prior (Bayesian shrinkage) so a 1/1
    success doesn't dominate, and the promoter (P4-C) will NOT retire it.

Outputs are written back into the variant registry
(``variants/_registry.json``) on each variant entry:
  ``reward`` (raw success_rate), ``score`` (group-relative advantage),
  ``n_samples``, ``scored_at``, ``exploring`` (bool).

Trigger (design 拍板 ⑤): event-driven (≥ ``MIN_NEW_EVENTS`` new variant_hash
events since last scoring) + ``MIN_INTERVAL`` floor (debounce). The promoter
(P4-C) is invoked by the coordinator after a successful ranking.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────
MIN_NEW_EVENTS = 5          # need ≥ this many new variant_hash events to re-rank
MIN_INTERVAL_SECONDS = 300  # debounce floor (5 min) between rankings per processor
MIN_SAMPLES_TO_RANK = 3     # a variant needs ≥ this many samples to get a non-prior score
EXPLORATION_K = 5           # variants with < this many samples are "exploring" (not retired)
PRIOR_WEIGHT = 3            # Bayesian shrinkage weight toward group prior
ZSCORE_EPS = 1e-6


def _now_iso() -> str:
    return datetime.now().isoformat()


def _query_variant_stats(db_path: str, variant_hash: str) -> Dict[str, Any]:
    """Aggregate raw_events for a single variant_hash.

    Returns {n, success_count, error_count, success_rate, avg_duration}.
    Never raises — returns zeros on any failure (fail-open, but logged).
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        from agent.raw_event import ensure_raw_events_table
        ensure_raw_events_table(conn)
        row = conn.execute(
            """SELECT
                 COUNT(*) AS n,
                 COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END),0) AS ok,
                 COALESCE(AVG(duration),0) AS avg_dur
               FROM raw_events WHERE variant_hash = ?""",
            (variant_hash,),
        ).fetchone()
        conn.close()
        n = row["n"] or 0
        ok = row["ok"] or 0
        return {
            "n": n,
            "success_count": ok,
            "error_count": n - ok,
            "success_rate": (ok / n) if n > 0 else 0.0,
            "avg_duration": float(row["avg_dur"] or 0.0),
        }
    except Exception as e:
        logger.debug("variant_ranker stats query failed for %s: %s", variant_hash, e)
        return {"n": 0, "success_count": 0, "error_count": 0, "success_rate": 0.0, "avg_duration": 0.0}


def score_variant(processor_id: str, variant_hash: str, db_path: str) -> Dict[str, Any]:
    """Compute the raw reward for one variant.

    Reward = success_rate (live signal). thumbs / re-ask are pluggable hooks
    that default to 0 (their per-variant capture is not yet wired — see module
    docstring). ``avg_duration`` is recorded for observation only, NOT in reward
    (design 拍板 ②: avoid rewarding "fast but wrong").
    """
    stats = _query_variant_stats(db_path, variant_hash)
    reward = stats["success_rate"]
    # Hooks for future signals (kept explicit so the "field≠wired" trap is visible):
    thumbs_balance = 0.0   # TODO(P4.5): per-variant thumbs_up - thumbs_down, when captured
    reask_penalty = 0.0    # TODO(P4.5): per-variant re-ask rate, when detection lands
    # When wired, reward = w1*success_rate + w2*thumbs_balance + w3*(1-reask_penalty)
    return {
        "variant_hash": variant_hash,
        "reward": reward,
        "n_samples": stats["n"],
        "success_count": stats["success_count"],
        "avg_duration": stats["avg_duration"],
        "thumbs_balance": thumbs_balance,
        "reask_penalty": reask_penalty,
    }


def rank_variants(processor_id: str, db_path: str) -> List[Dict[str, Any]]:
    """Score + group-relative-rank all variants of a processor.

    Writes score/advantage/scored_at/n_samples/exploring back into each
    variant entry of the registry. Returns the ranked list (best first).

    Group-relative (GRPO flavor): advantage = (reward - group_mean) / (group_std + eps).
    Cold-start: variants with n_samples < MIN_SAMPLES_TO_RANK get a prior-shrunk
    reward and exploring=True (promoter won't retire them).
    """
    from agent.variant_store import _load_registry, _save_registry, get_active_variant_hash

    registry = _load_registry(processor_id)
    variants = registry.get("variants", [])
    active_hash = registry.get("active_hash", "")

    if not variants:
        return []

    # 1) Score each variant
    scored: List[Dict[str, Any]] = []
    for v in variants:
        h = v.get("hash", "")
        if not h:
            continue
        s = score_variant(processor_id, h, db_path)
        scored.append({**v, **{
            "reward": s["reward"],
            "n_samples": s["n_samples"],
            "avg_duration": s["avg_duration"],
        }})

    if not scored:
        return []

    # 2) Bayesian shrinkage for low-sample variants (cold start)
    #    prior = active variant's reward (or group mean if no active)
    rewards = [x["reward"] for x in scored if x["n_samples"] >= MIN_SAMPLES_TO_RANK]
    prior = 0.0
    if rewards:
        prior = sum(rewards) / len(rewards)
    elif active_hash:
        # fall back to active's reward even if it's low-sample
        active_entry = next((x for x in scored if x["hash"] == active_hash), None)
        if active_entry:
            prior = active_entry["reward"]

    for x in scored:
        n = x["n_samples"]
        if n < MIN_SAMPLES_TO_RANK:
            # shrink toward prior
            shrunk = (n * x["reward"] + PRIOR_WEIGHT * prior) / (n + PRIOR_WEIGHT)
            x["reward_shrunk"] = shrunk
            x["exploring"] = n < EXPLORATION_K
        else:
            x["reward_shrunk"] = x["reward"]
            x["exploring"] = False

    # 3) Group-relative advantage (z-score over shrunk rewards)
    shrunk_rewards = [x["reward_shrunk"] for x in scored]
    mean = sum(shrunk_rewards) / len(shrunk_rewards) if shrunk_rewards else 0.0
    var = sum((r - mean) ** 2 for r in shrunk_rewards) / len(shrunk_rewards) if shrunk_rewards else 0.0
    std = var ** 0.5
    for x in scored:
        x["score"] = (x["reward_shrunk"] - mean) / (std + ZSCORE_EPS)
        x["scored_at"] = _now_iso()

    # 4) Sort best first (highest score)
    scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    # 5) Write back to registry (merge scored fields into variant entries)
    #    Preserve non-scored fields (hash, archived_at, author, pinned, superseded_at).
    by_hash = {x["hash"]: x for x in scored}
    for v in registry["variants"]:
        h = v.get("hash", "")
        if h in by_hash:
            s = by_hash[h]
            v["reward"] = s.get("reward", 0.0)
            v["score"] = s.get("score", 0.0)
            v["n_samples"] = s.get("n_samples", 0)
            v["scored_at"] = s.get("scored_at", "")
            v["exploring"] = s.get("exploring", False)
            v["avg_duration"] = s.get("avg_duration", 0.0)
    registry["last_scored_at"] = _now_iso()
    _save_registry(processor_id, registry)

    return scored


def should_rank(processor_id: str, db_path: str) -> bool:
    """Trigger gate: enough new variant_hash events + MIN_INTERVAL elapsed.

    Counts raw_events whose variant_hash belongs to this processor's variants
    AND timestamp > registry.last_scored_at. Event-driven + debounce floor.
    """
    from agent.variant_store import _load_registry

    registry = _load_registry(processor_id)
    variants = registry.get("variants", [])
    if not variants:
        return False

    last = registry.get("last_scored_at", "")
    # MIN_INTERVAL floor
    if last:
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
            if elapsed < MIN_INTERVAL_SECONDS:
                return False
        except Exception:
            pass  # bad timestamp → don't block on interval

    # Count new events for this processor's variant hashes since last scoring
    hashes = [v.get("hash") for v in variants if v.get("hash")]
    if not hashes:
        return False
    placeholders = ",".join("?" * len(hashes))
    try:
        conn = sqlite3.connect(db_path)
        from agent.raw_event import ensure_raw_events_table
        ensure_raw_events_table(conn)
        if last:
            row = conn.execute(
                f"SELECT COUNT(*) FROM raw_events WHERE variant_hash IN ({placeholders}) AND timestamp > ?",
                (*hashes, last),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(*) FROM raw_events WHERE variant_hash IN ({placeholders})",
                hashes,
            ).fetchone()
        conn.close()
        n_new = row[0] if row else 0
        return n_new >= MIN_NEW_EVENTS
    except Exception as e:
        logger.debug("variant_ranker should_rank failed for %s: %s", processor_id, e)
        return False


def get_variant_scores(processor_id: str) -> List[Dict[str, Any]]:
    """Read-only accessor: return current scored variant entries (best first).

    Used by evolution_injector (P4-D) to assemble the prompt block without
    re-scoring.
    """
    from agent.variant_store import _load_registry

    registry = _load_registry(processor_id)
    variants = [v for v in registry.get("variants", []) if v.get("scored_at")]
    variants.sort(key=lambda v: v.get("score", 0.0), reverse=True)
    return variants


# ── Coordinator (P4-E trigger wiring) ──────────────────────────────────

def run_variant_evolution(processor_id: str, db_path: str) -> Dict[str, Any]:
    """Coordinate one processor: gate → rank → promote.

    Returns a summary {processor_id, ranked, promotion}. Ranking only runs
    when ``should_rank`` passes (event-driven + MIN_INTERVAL). Promotion
    delegates to ``variant_store.promote_best_variant`` (governance-gated).
    """
    summary: Dict[str, Any] = {"processor_id": processor_id, "ranked": False, "promotion": None}
    try:
        if not should_rank(processor_id, db_path):
            return summary
        rank_variants(processor_id, db_path)
        summary["ranked"] = True
        from agent.variant_store import promote_best_variant
        summary["promotion"] = promote_best_variant(processor_id, db_path)
        logger.info(
            "Variant evolution %s: ranked=yes promote=%s",
            processor_id, summary["promotion"].get("action") if summary["promotion"] else "none",
        )
    except Exception as e:
        # Never break the emergence chain over variant evolution.
        logger.debug("run_variant_evolution failed for %s: %s", processor_id, e)
        summary["error"] = str(e)
    return summary


def run_variant_evolution_for_all(db_path: str) -> List[Dict[str, Any]]:
    """Run variant evolution for every processor that has a variant registry.

    Enumerates ``~/.vermes/processors/*/variants/_registry.json``. Called from
    the emergence chain (P4-E). Fail-open per processor.
    """
    results: List[Dict[str, Any]] = []
    try:
        from agent.variant_store import _get_user_dir, _REGISTRY_NAME, _VARIANTS_SUBDIR
        user_dir = _get_user_dir()
        if not user_dir.exists():
            return results
        for proc_dir in user_dir.iterdir():
            if not proc_dir.is_dir():
                continue
            registry = proc_dir / _VARIANTS_SUBDIR / _REGISTRY_NAME
            if not registry.exists():
                continue
            processor_id = proc_dir.name
            results.append(run_variant_evolution(processor_id, db_path))
    except Exception as e:
        logger.debug("run_variant_evolution_for_all failed: %s", e)
    return results
