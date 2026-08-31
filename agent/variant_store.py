"""Variant store — hash-indexed version archive for processors.

Phase 3 of the Lego-style refactoring.  ``governance.hash`` (Phase 1) was
computed but never consumed; this module is its first real consumer.

When a processor YAML is about to be overwritten, the approval flow
(``emergent_change.apply_change``) calls ``snapshot_variant()`` to archive
the old content under ``variants/<hash>.yaml`` before writing the new
version.  The old version can then be listed, diffed, rolled back to, or
pinned — all keyed by its canonical hash.

On-disk layout::

    ~/.vermes/processors/<id>/
      processor.yaml          ← active variant (loader reads ONLY this)
      variants/
        _registry.json        ← metadata (timestamps, author, pinned)
        sha256_<hash>.yaml     ← archived variant (full YAML copy)
        sha256_<hash>.yaml

Design constraints:
  - ``processor.yaml`` is the single active entry → loader/watcher unchanged.
  - ``variants/`` is passive archive → watcher must skip it.
  - Rollback = copy variant → ``processor.yaml`` → goes through approval.
  - The existing ``.bak`` mechanism is untouched; this is an additive layer.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_HASH_PREFIX = "sha256:"
_VARIANTS_SUBDIR = "variants"
_REGISTRY_NAME = "_registry.json"


# ── Path helpers ───────────────────────────────────────────────────────

def _get_user_dir() -> Path:
    """Return the user processors directory (~/.vermes/processors/)."""
    from vermes_constants import get_vermes_home
    return get_vermes_home() / "processors"


def _processor_id_from_path(target_path: str) -> Optional[str]:
    """Extract the processor id from a path under ~/.vermes/processors/.

    Supports both layouts:
      ~/.vermes/processors/<id>/processor.yaml → <id>
      ~/.vermes/processors/<id>.yaml           → <id>
    Returns ``None`` if the path is not under the processors directory.
    """
    try:
        tp = Path(target_path).resolve()
        user_dir = _get_user_dir().resolve()
        if not tp.is_relative_to(user_dir):
            return None
        rel = tp.relative_to(user_dir)
        parts = rel.parts
        if len(parts) >= 2 and parts[-1] == "processor.yaml":
            return parts[0]
        if len(parts) == 1 and parts[0].endswith(".yaml"):
            return parts[0][:-5]
        return None
    except Exception:
        return None


def _processor_dir(processor_id: str) -> Path:
    """Return the processor directory (~/.vermes/processors/<id>/)."""
    return _get_user_dir() / processor_id


def _variants_dir(processor_id: str) -> Path:
    """Return the variants subdirectory for a processor id."""
    return _processor_dir(processor_id) / _VARIANTS_SUBDIR


def _registry_path(processor_id: str) -> Path:
    """Return the _registry.json path for a processor id."""
    return _variants_dir(processor_id) / _REGISTRY_NAME


def _active_yaml_path(processor_id: str) -> Path:
    """Return the active processor.yaml path for a processor id."""
    return _processor_dir(processor_id) / "processor.yaml"


def _variant_file_path(processor_id: str, hash_val: str) -> Path:
    """Return the on-disk path for a specific variant."""
    safe = hash_val.replace(":", "_")  # sha256:abc → sha256_abc
    return _variants_dir(processor_id) / f"{safe}.yaml"


# ── Hash computation ───────────────────────────────────────────────────

def _compute_hash(content: str) -> str:
    """Compute the canonical manifest hash of a processor YAML content.

    Delegates to ``compute_manifest_hash`` when the content parses as YAML;
    falls back to raw sha256 for non-YAML or unparseable content.
    """
    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict):
            from agent.prompt_processor_loader import compute_manifest_hash
            return compute_manifest_hash(data)
    except Exception:
        pass
    return _HASH_PREFIX + hashlib.sha256(content.encode("utf-8")).hexdigest()


# ── Registry I/O ──────────────────────────────────────────────────────

def _load_registry(processor_id: str) -> Dict[str, Any]:
    """Load the variant registry, or return an empty skeleton."""
    path = _registry_path(processor_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning("Failed to load variant registry %s: %s", path, e)
    return {
        "processor_id": processor_id,
        "active_hash": "",
        "variants": [],
    }


def get_active_variant_hash(processor_id: str) -> Optional[str]:
    """Return the active variant hash for a processor, or None.

    Public P4 accessor used by tool_executor (outcome attribution) and
    variant_ranker. Returns None when the processor has no variant
    tracking (builtins, unmodified user processors) so callers can treat
    None as "no variant context".
    """
    try:
        h = _load_registry(processor_id).get("active_hash", "")
        return h or None
    except Exception:
        return None


def _save_registry(processor_id: str, registry: Dict[str, Any]) -> None:
    """Atomically write the registry JSON."""
    path = _registry_path(processor_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── Public API ────────────────────────────────────────────────────────

def snapshot_variant(
    processor_path: str,
    old_content: str,
    author: str = "system",
    note: str = "",
) -> Optional[str]:
    """Archive old processor content as a variant before overwrite.

    Called from ``emergent_change.apply_change`` *after* the ``.bak`` backup
    is created but *before* the new content is written.  If ``processor_path``
    is not under ``~/.vermes/processors/``, returns ``None`` (no-op).

    Returns the computed hash of the archived content, or ``None``.
    """
    proc_id = _processor_id_from_path(processor_path)
    if proc_id is None:
        return None  # not a processor — no variant archive

    hash_val = _compute_hash(old_content)
    vpath = _variant_file_path(proc_id, hash_val)

    # Don't re-archive if the same hash already exists (idempotent).
    if not vpath.exists():
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text(old_content, encoding="utf-8")

    registry = _load_registry(proc_id)
    now = datetime.now().isoformat(timespec="seconds")

    # Mark any previously-active variant as superseded.
    for v in registry["variants"]:
        if v.get("hash") == registry.get("active_hash") and not v.get("superseded_at"):
            v["superseded_at"] = now

    # Add this variant if not already tracked.
    existing = next((v for v in registry["variants"] if v["hash"] == hash_val), None)
    if existing is None:
        registry["variants"].append({
            "hash": hash_val,
            "created_at": now,
            "superseded_at": now,  # immediately superseded (it's being replaced)
            "author": author,
            "note": note,
            "pinned": False,
        })
    elif not existing.get("superseded_at"):
        existing["superseded_at"] = now

    # The new active hash will be set by the caller after the new content
    # is written; we don't know it yet.  But we clear the stale active_hash
    # so list_variants shows the old one as superseded.
    registry["active_hash"] = ""  # will be updated by update_active_hash()

    _save_registry(proc_id, registry)
    logger.info("Variant archived: %s → %s", proc_id, hash_val)
    return hash_val


def update_active_hash(processor_path: str, new_content: str) -> Optional[str]:
    """After new content is written, record its hash as the active variant.

    Called from ``emergent_change.apply_change`` *after* the write succeeds.
    """
    proc_id = _processor_id_from_path(processor_path)
    if proc_id is None:
        return None

    hash_val = _compute_hash(new_content)
    registry = _load_registry(proc_id)
    now = datetime.now().isoformat(timespec="seconds")

    # Ensure this hash is in the registry as the active variant.
    existing = next((v for v in registry["variants"] if v["hash"] == hash_val), None)
    if existing is None:
        registry["variants"].append({
            "hash": hash_val,
            "created_at": now,
            "superseded_at": None,
            "author": "system",
            "note": "active version",
            "pinned": False,
        })
    else:
        existing["superseded_at"] = None  # it's now active

    registry["active_hash"] = hash_val
    _save_registry(proc_id, registry)
    return hash_val


def list_variants(processor_id: str) -> List[Dict[str, Any]]:
    """List all variants for a processor id.

    Returns a list of dicts with keys: hash, created_at, superseded_at,
    author, note, pinned, active (bool).

    The active variant lives in ``processor.yaml`` (not in ``variants/``),
    so it's always included even if no variant file exists for it.
    """
    registry = _load_registry(processor_id)
    active = registry.get("active_hash", "")
    active_path = _active_yaml_path(processor_id)
    result = []
    for v in registry.get("variants", []):
        is_active = (v["hash"] == active)
        # Active variant lives in processor.yaml — always present.
        # Non-active variants must have their file on disk.
        if is_active:
            if not active_path.exists():
                continue
        else:
            vpath = _variant_file_path(processor_id, v["hash"])
            if not vpath.exists():
                continue
        entry = dict(v)
        entry["active"] = is_active
        result.append(entry)
    # Sort: active first, then most recent.
    result.sort(key=lambda x: (not x["active"], x.get("created_at", "")), reverse=True)
    return result


def get_variant_content(processor_id: str, hash_val: str) -> Optional[str]:
    """Read the raw content of a specific variant."""
    vpath = _variant_file_path(processor_id, hash_val)
    if vpath.exists():
        return vpath.read_text(encoding="utf-8")
    return None


def diff_variants(processor_id: str, target_hash: str) -> Optional[str]:
    """Unified diff of a target variant vs the active processor.yaml.

    Returns the diff text, or ``None`` if the variant or active file
    doesn't exist.
    """
    target_content = get_variant_content(processor_id, target_hash)
    if target_content is None:
        return None
    active_path = _active_yaml_path(processor_id)
    if not active_path.exists():
        return None
    active_content = active_path.read_text(encoding="utf-8")
    diff = difflib.unified_diff(
        target_content.splitlines(keepends=True),
        active_content.splitlines(keepends=True),
        fromfile=f"variant:{target_hash[:16]}",
        tofile="active:processor.yaml",
    )
    return "".join(diff)


def rollback_variant(processor_id: str, target_hash: str) -> Optional[str]:
    """Swap: archive current active → variants/, copy target → processor.yaml.

    Returns the new active content, or ``None`` if the target variant
    doesn't exist.  The caller is responsible for routing this through the
    approval flow (this function only does the file swap + registry update).
    """
    target_content = get_variant_content(processor_id, target_hash)
    if target_content is None:
        return None

    active_path = _active_yaml_path(processor_id)

    # Archive current active before overwriting.
    if active_path.exists():
        current_content = active_path.read_text(encoding="utf-8")
        current_hash = _compute_hash(current_content)
        cvpath = _variant_file_path(processor_id, current_hash)
        if not cvpath.exists():
            cvpath.parent.mkdir(parents=True, exist_ok=True)
            cvpath.write_text(current_content, encoding="utf-8")

        registry = _load_registry(processor_id)
        now = datetime.now().isoformat(timespec="seconds")
        existing = next((v for v in registry["variants"] if v["hash"] == current_hash), None)
        if existing is None:
            registry["variants"].append({
                "hash": current_hash,
                "created_at": now,
                "superseded_at": now,
                "author": "system",
                "note": "archived before rollback",
                "pinned": False,
            })
        elif not existing.get("superseded_at"):
            existing["superseded_at"] = now

    # Write target to active.
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(target_content, encoding="utf-8")

    # Update registry.
    registry = _load_registry(processor_id)
    now = datetime.now().isoformat(timespec="seconds")
    for v in registry["variants"]:
        if v["hash"] == target_hash:
            v["superseded_at"] = None  # now active
        elif not v.get("superseded_at"):
            v["superseded_at"] = now
    registry["active_hash"] = target_hash
    _save_registry(processor_id, registry)

    logger.info("Rollback: %s → %s", processor_id, target_hash)
    return target_content


def pin_variant(processor_id: str, hash_val: str, pinned: bool = True) -> bool:
    """Pin or unpin a variant (pinned variants are exempt from GC)."""
    registry = _load_registry(processor_id)
    for v in registry["variants"]:
        if v["hash"] == hash_val:
            v["pinned"] = pinned
            _save_registry(processor_id, registry)
            return True
    return False


def delete_variant(processor_id: str, hash_val: str) -> bool:
    """Delete a variant file + registry entry.

    Refuses to delete the active variant or a pinned variant.
    Returns True if deleted, False if refused or not found.
    """
    registry = _load_registry(processor_id)
    if hash_val == registry.get("active_hash"):
        return False  # can't delete active

    v = next((v for v in registry["variants"] if v["hash"] == hash_val), None)
    if v is None:
        return False
    if v.get("pinned"):
        return False  # pinned

    # Delete file.
    vpath = _variant_file_path(processor_id, hash_val)
    if vpath.exists():
        vpath.unlink()

    # Remove from registry.
    registry["variants"] = [x for x in registry["variants"] if x["hash"] != hash_val]
    _save_registry(processor_id, registry)
    return True


def gc_variants(processor_id: str, max_variants: int = 10) -> int:
    """Garbage-collect: delete oldest non-pinned variants over the limit.

    Returns the number of variants deleted.
    """
    registry = _load_registry(processor_id)
    variants = registry.get("variants", [])

    # Filter to non-pinned, non-active, superseded.
    deletable = [
        v for v in variants
        if not v.get("pinned")
        and v["hash"] != registry.get("active_hash")
        and v.get("superseded_at")
    ]

    if len(deletable) <= max_variants:
        return 0  # under limit

    # Sort by superseded_at ascending (oldest first).
    deletable.sort(key=lambda x: x.get("superseded_at", ""))
    to_delete = deletable[:len(deletable) - max_variants]

    for v in to_delete:
        vpath = _variant_file_path(processor_id, v["hash"])
        if vpath.exists():
            vpath.unlink()
        variants.remove(v)

    _save_registry(processor_id, registry)
    deleted = len(to_delete)
    if deleted:
        logger.info("GC: deleted %d stale variant(s) for %s", deleted, processor_id)
    return deleted


# ── Convenience: snapshot + GC in one call ─────────────────────────────

def snapshot_and_gc(
    processor_path: str,
    old_content: str,
    author: str = "system",
    note: str = "",
    max_variants: int = 10,
) -> Optional[str]:
    """Archive old content as variant, then run GC.  Returns the hash."""
    hash_val = snapshot_variant(processor_path, old_content, author, note)
    if hash_val is not None:
        proc_id = _processor_id_from_path(processor_path)
        if proc_id:
            gc_variants(proc_id, max_variants)
    return hash_val


# ── P4-C: outcome-driven auto promotion (governance-gated) ─────────────

PROMOTE_DELTA = 0.15  # best variant's score must exceed active's by this (z-score units)


def _tier_from_content(content: str) -> str:
    """Decide governance tier for a promotion from the variant's own content.

    Mirrors Phase 2.5 P1: ``handler.inline`` forces L2 (self-proving
    governance — an inline tool must not auto-promote). Only an explicit
    ``governance.risk_tier: L1`` (and no inline) qualifies for auto-landing.
    Anything else → L2 (propose, wait for human).
    """
    try:
        data = yaml.safe_load(content) or {}
        if not isinstance(data, dict):
            return "L2"
        handler = data.get("handler") or {}
        if isinstance(handler, dict) and "inline" in handler:
            return "L2"
        gov = data.get("governance") or {}
        risk_tier = gov.get("risk_tier", "L2") if isinstance(gov, dict) else "L2"
        return "L1" if risk_tier == "L1" else "L2"
    except Exception:
        return "L2"  # unparseable → safest


def promote_best_variant(processor_id: str, db_path: str = "") -> Dict[str, Any]:
    """Promote the best-scoring variant to active, if it clearly beats the current.

    Governance (design 拍板 ④, ties to Phase 2.5 self-proving-governance lesson):
      - L1 processor (no inline, risk_tier=L1): auto-land via
        ``emergent_change.apply_change(force=True)`` — leaves a self_modify
        raw_event + change_ledger entry. The apply_change snapshot hook
        (Phase 3) archives the outgoing active automatically.
      - L2 / inline processor: does NOT auto-land. Creates an evolution
        proposal (status="proposed") for human approval via the existing
        ``POST /api/evolution/proposals/{id}/apply`` endpoint.

    Refuses to promote:
      - exploring variants (n_samples < EXPLORATION_K) — cold-start budget
      - variants whose score doesn't exceed active's by PROMOTE_DELTA
      - when there's no active variant to compare against

    Returns a decision dict: {action, processor_id, target_hash, tier,
    committed/proposed, reason}.
    """
    from agent.variant_ranker import get_variant_scores, EXPLORATION_K

    decision: Dict[str, Any] = {
        "action": "none", "processor_id": processor_id,
        "target_hash": "", "tier": "", "reason": "",
    }

    registry = _load_registry(processor_id)
    active_hash = registry.get("active_hash", "")
    if not active_hash:
        decision["reason"] = "no active variant to compare against"
        return decision

    scored = get_variant_scores(processor_id)
    if not scored:
        decision["reason"] = "no scored variants"
        return decision

    active_entry = next((v for v in scored if v["hash"] == active_hash), None)
    active_score = active_entry.get("score", 0.0) if active_entry else 0.0

    # Best non-active, non-exploring variant
    for cand in scored:
        if cand["hash"] == active_hash:
            continue
        if cand.get("exploring", False) or cand.get("n_samples", 0) < EXPLORATION_K:
            continue
        if cand.get("score", 0.0) - active_score < PROMOTE_DELTA:
            continue
        # Candidate qualifies — attempt promotion
        target_hash = cand["hash"]
        variant_content = get_variant_content(processor_id, target_hash)
        if variant_content is None:
            decision["reason"] = f"variant {target_hash[:16]} content missing"
            return decision

        tier = _tier_from_content(variant_content)
        decision["tier"] = tier
        decision["target_hash"] = target_hash
        active_path = str(_active_yaml_path(processor_id))

        if tier == "L1":
            # Auto-land through the change pipeline (force bypasses cold-start
            # gate; snapshot hook archives outgoing active; ledger records it).
            try:
                from agent.emergent_change import get_pipeline, ChangeProposal
                proposal = ChangeProposal(
                    source="variant_selector",
                    target_path=active_path,
                    content=variant_content,
                    description=(
                        f"auto-promote variant {target_hash[:16]} "
                        f"(score {cand['score']:.2f} vs active {active_score:.2f})"
                    ),
                    initiator="system",
                    metadata={"variant_promotion": True, "target_hash": target_hash},
                )
                result = get_pipeline().apply_change(proposal, force=True)
                decision["action"] = "promoted" if result.committed else "blocked"
                decision["committed"] = result.committed
                decision["reason"] = result.error if not result.committed else (
                    f"promoted {target_hash[:16]} (L1 auto, Δscore={cand['score']-active_score:.2f})"
                )
                logger.info("Variant auto-promote %s → %s (%s)", processor_id, target_hash[:16], decision["reason"])
            except Exception as e:
                decision["action"] = "error"
                decision["reason"] = f"L1 promote failed: {e}"
                logger.warning("Variant promote failed %s: %s", processor_id, e)
        else:
            # L2 / inline → propose, don't auto-land
            try:
                from agent.evolution_manager import record_proposal
                diff = diff_variants(processor_id, target_hash) or ""
                pid = record_proposal(
                    phase="variant_selector",
                    task_type="variant_promotion",
                    title=f"晋升变体 {target_hash[:16]} → {processor_id}",
                    rationale=(
                        f"变体 {target_hash[:16]} 得分 {cand['score']:.2f} 高于 active "
                        f"{active_score:.2f}（Δ={cand['score']-active_score:.2f}，"
                        f"n={cand.get('n_samples',0)}）。L2/inline processor 需人工确认。"
                    ),
                    target_kind="processor",
                    target_path=active_path,
                    candidate_diff=diff,
                    deterministic_result={"target_hash": target_hash, "tier": tier},
                    status="proposed",
                )
                decision["action"] = "proposed"
                decision["proposal_id"] = pid
                decision["reason"] = (
                    f"proposed {target_hash[:16]} (L2/inline, proposal #{pid})"
                )
                logger.info("Variant propose %s → %s (L2/inline)", processor_id, target_hash[:16])
            except Exception as e:
                decision["action"] = "error"
                decision["reason"] = f"L2 propose failed: {e}"
                logger.warning("Variant propose failed %s: %s", processor_id, e)
        return decision

    decision["reason"] = "no variant beats active by Δ"
    return decision



def list_active_variants_summary() -> List[Dict[str, Any]]:
    """List all processors that currently have an active variant.

    P4-D: Used by evolution_manager.build_evolution_prompt() to inject
    active variant info into the system prompt, closing the loop between
    variant evolution and prompt injection.
    """
    base = _get_user_dir() / "processors"
    if not base.exists():
        return []
    summaries = []
    for proc_dir in base.iterdir():
        if not proc_dir.is_dir():
            continue
        registry_path = proc_dir / "registry.json"
        if not registry_path.exists():
            continue
        try:
            import json as _json
            reg = _json.loads(registry_path.read_text(encoding="utf-8"))
            active_hash = reg.get("active_hash", "")
            if active_hash:
                variants = reg.get("variants", [])
                active_v = next((v for v in variants if v.get("hash") == active_hash), {})
                summaries.append({
                    "processor_id": proc_dir.name,
                    "active_hash": active_hash[:16],
                    "n_variants": len(variants),
                    "score": active_v.get("score", 0.0),
                    "n_samples": active_v.get("n_samples", 0),
                })
        except Exception:
            continue
    return summaries
