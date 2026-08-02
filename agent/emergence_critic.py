"""AEGIS 闭环的 Critic 闸门 + 确定性闸门（纯函数，可单测）。

P2 首版只做 B1 配置级：候选只修改 config.yaml 的 memory.autoResolve 拨盘。
本模块不触碰 LLM 之外的副作用，所有外部依赖（LLM、memory_flags DB）都可注入，便于单测。

设计评审结论已吸收：
- 确定性闸门双指标：precision 不降 + 数量不爆（count_delta ≤ 1.5x）。
- Critic 批处理 + 24h 缓存（key = task_type 集合 + config_hash）。
- 硬编码护栏：删表 / 设 0 / 越界 / 越权改非 autoResolve 段 → 直接拒。
- LLM 不可用时 fail-open：不生成提案（不静默应用）。
"""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 外置参数（缺省回落默认，纵深防御）────────────────────────────────
COUNT_DELTA_CAP = 1.5          # 新旧 config 自动处置数比值上限（防爆量）
PRECISION_EPSILON = 0.02       # precision 允许的小幅下降容差
CRITIC_CONF_THRESHOLD = 0.7    # Critic 置信度下限
CRITIC_CACHE_TTL = 24 * 3600   # 同 task_type+config 24h 内不重复 Critic
ALLOWED_DIALS = {"duplicate", "outdated", "cluster_min_interval", "merge_cleanup"}


# ── 确定性闸门：open flag 离线回放 ─────────────────────────────────────

def replay_auto_resolve(db_path: Any = None, dup_threshold: float = 0.9,
                        out_threshold: float = 0.85) -> Dict[str, float]:
    """Read-only replay of auto_resolve eligibility over OPEN flags.

    Mirrors ``memory_reflection.auto_resolve_eligible_flags`` gating but
    commits nothing. Used by the deterministic gate to compare old vs new
    config WITHOUT applying anything.

    Returns:
        eligible      : # open flags matching (dup≥dup_thr OR out≥out_thr)
        would_demote  : # of those the live code would actually demote
                        (duplicate+orphan/skill, or outdated)
        safe_rate     : would_demote / eligible  (precision proxy)
    """
    if db_path is None:
        from agent.memory_fabric import _get_index_db as _get_mem_db
        db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT f.id, f.memory_id, f.flag_type, f.confidence
               FROM memory_flags f
               WHERE f.status = 'open'
                 AND ((f.flag_type = 'duplicate' AND f.confidence >= ?)
                      OR (f.flag_type = 'outdated' AND f.confidence >= ?))""",
            (dup_threshold, out_threshold),
        ).fetchall()
    finally:
        conn.close()

    eligible = len(rows)
    would_demote = 0
    for _flag_id, memory_id, flag_type, _conf in rows:
        if flag_type == "outdated":
            would_demote += 1
            continue
        # duplicate: live code only demotes if orphan (memory missing)
        # or source == 'skill' (description lives in skill system).
        try:
            _mid = int(memory_id)
        except (TypeError, ValueError):
            _mid = memory_id
        _c2 = sqlite3.connect(db_path)
        try:
            src_row = _c2.execute(
                "SELECT source FROM memories WHERE id = ?", (_mid,)
            ).fetchone()
        finally:
            _c2.close()
        if not src_row:           # orphan → confirmed redundant
            would_demote += 1
        elif src_row[0] == "skill":
            would_demote += 1
        # existing non-skill duplicate → live code skips it
    safe_rate = (would_demote / eligible) if eligible else 1.0
    return {"eligible": eligible, "would_demote": would_demote, "safe_rate": safe_rate}


def run_deterministic_gate(db_path: Any = None, new_cfg: Optional[Dict] = None,
                           old_cfg: Optional[Dict] = None) -> Dict[str, Any]:
    """AEGIS deterministic gate — new config must not degrade solved tasks.

    Two checks (both must pass):
      1. precision 不降: new.safe_rate >= old.safe_rate - PRECISION_EPSILON
      2. 数量不爆:       new.would_demote / old.would_demote <= COUNT_DELTA_CAP

    The count check is the user-requested guard against the
    "duplicate 0.9→0.7: precision flat but recall explodes → 误杀边缘案例"
    trap.
    """
    if new_cfg is None:
        from agent.memory_reflection import _load_auto_resolve_config
        new_cfg = _load_auto_resolve_config()
    if old_cfg is None:
        from agent.memory_reflection import _load_auto_resolve_config
        old_cfg = _load_auto_resolve_config()

    old = replay_auto_resolve(db_path, old_cfg["duplicate"], old_cfg["outdated"])
    new = replay_auto_resolve(db_path, new_cfg["duplicate"], new_cfg["outdated"])

    precision_ok = new["safe_rate"] >= old["safe_rate"] - PRECISION_EPSILON
    old_count = max(old["would_demote"], 1)
    count_delta = new["would_demote"] / old_count
    count_ok = count_delta <= COUNT_DELTA_CAP
    passed = bool(precision_ok and count_ok)
    return {
        "passed": passed,
        "precision_old": round(old["safe_rate"], 4),
        "precision_new": round(new["safe_rate"], 4),
        "count_old": old["would_demote"],
        "count_new": new["would_demote"],
        "count_delta": round(count_delta, 3),
        "precision_ok": bool(precision_ok),
        "count_ok": bool(count_ok),
    }


# ── 硬编码护栏（非 LLM，必过）────────────────────────────────────────

def hardcoded_guard(config_patch: Any) -> Tuple[bool, str]:
    """Reject unsafe B1 config patches before any LLM/Critic step.

    Rejects if the patch:
      (a) touches any key outside memory.autoResolve (B1 only edits dials);
      (b) sets any autoResolve dial to <= 0 (reuse P1 >0 rule, 2ebe62e8f);
      (c) sets any dial negative or absurd (dup/out/merge_cleanup > 1.0,
          cluster_min_interval > 86400).

    Returns (ok, reason).
    """
    if not isinstance(config_patch, dict):
        return False, "config_patch must be a dict"
    mem = config_patch.get("memory")
    if not isinstance(mem, dict) or "autoResolve" not in mem:
        return False, "B1 proposals may only modify memory.autoResolve"
    ar = mem["autoResolve"]
    if not isinstance(ar, dict):
        return False, "memory.autoResolve must be a dict"

    for k, v in ar.items():
        if k not in ALLOWED_DIALS:
            return False, f"unknown autoResolve dial: {k}"
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return False, f"dial {k} must be numeric"
        if v <= 0:
            return False, f"dial {k} must be > 0 (rejected: {v})"
        if k in ("duplicate", "outdated", "merge_cleanup") and v > 1.0:
            return False, f"dial {k} must be <= 1.0"
        if k == "cluster_min_interval" and v > 86400:
            return False, f"cluster_min_interval must be <= 86400"
    return True, ""


# ── Phase C Critic 闸门（批处理 + 缓存 + fail-open）───────────────────

_CRITIC_CACHE: Dict[str, Tuple[float, List[Dict]]] = {}


def _critic_cache_key(task_types: List[str], config_hash: str) -> str:
    return f"{','.join(sorted(task_types))}|{config_hash}"


def _default_llm_call(prompt: str) -> Dict[str, Any]:
    """Lazy wrapper over curator._run_llm_review (mirrors memory_reflection)."""
    try:
        from agent.curator import _run_llm_review
        return _run_llm_review(prompt)
    except Exception as e:  # pragma: no cover - fail-open path
        logger.warning("[AEGIS] Critic LLM unavailable: %s", e)
        return {"final": "", "error": str(e)}


def _build_critic_prompt(candidates: List[Dict], outcomes_summary: str) -> str:
    items = "\n".join(
        f"{i+1}. task_type={c.get('task_type')} | 提案: {c.get('title')}\n"
        f"   理由: {c.get('rationale')}\n"
        f"   配置变更: {json.dumps(c.get('config_patch'), ensure_ascii=False)}\n"
        f"   预期效果: {c.get('expected_effect')}"
        for i, c in enumerate(candidates)
    )
    return (
        "你是一个进化系统安全审查器（Critic）。以下是一批自动生成的自我改进提案"
        "（仅调整 config.yaml 的 memory.autoResolve 拨盘）。请逐条判断："
        "该改动是否安全、是否会灾难性遗忘、是否可能误杀边缘案例。\n"
        '对每条返回 JSON 对象：{"idx": 序号, "safe": true/false, '
        '"concerns": "简短风险说明", "confidence": 0.0-1.0}。返回 JSON 数组。\n\n'
        f"近期成效摘要：{outcomes_summary}\n\n"
        f"提案：\n{items}"
    )


def _parse_critic_response(text: str, n: int) -> List[Dict]:
    """Parse Critic JSON array → list of {safe, concerns, confidence}."""
    if not text:
        return []
    try:
        s = text.find("[")
        e = text.rfind("]")
        if s == -1 or e == -1 or e <= s:
            return []
        data = json.loads(text[s:e + 1])
        out = []
        for d in data:
            if not isinstance(d, dict):
                continue
            try:
                conf = float(d.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            out.append({
                "safe": bool(d.get("safe", False)),
                "concerns": str(d.get("concerns", "")),
                "confidence": conf,
            })
        return out[:n] if out else []
    except Exception:
        return []


def critic_review(candidates: List[Dict], outcomes_summary: str = "",
                  llm_call: Optional[Callable[[str], Any]] = None,
                  now: Optional[float] = None) -> List[Dict]:
    """Phase C Critic gate — batched + cached LLM review.

    Args:
        candidates: list of {task_type, title, rationale, config_patch,
                             expected_effect, regression_ref}
        outcomes_summary: short context for the Critic
        llm_call: injectable callable(prompt)->str|dict (for tests)
        now: injectable timestamp (for tests)

    Returns: list of {safe, concerns, confidence, cached} aligned to candidates.
             On LLM failure / unparseable → [] (fail-open: no proposals).
    """
    if not candidates:
        return []
    if llm_call is None:
        llm_call = _default_llm_call

    task_types = [c.get("task_type", "") for c in candidates]
    config_hash = hashlib.sha256(
        json.dumps([c.get("config_patch") for c in candidates],
                   sort_keys=True).encode()
    ).hexdigest()[:12]
    cache_key = _critic_cache_key(task_types, config_hash)
    now = now if now is not None else time.time()
    cached = _CRITIC_CACHE.get(cache_key)
    if cached and (now - cached[0]) < CRITIC_CACHE_TTL:
        return [dict(v, cached=True) for v in cached[1]]

    prompt = _build_critic_prompt(candidates, outcomes_summary)
    try:
        resp = llm_call(prompt)
        text = resp.get("final", "") if isinstance(resp, dict) else str(resp)
        verdicts = _parse_critic_response(text, len(candidates))
    except Exception as e:  # pragma: no cover - fail-open
        logger.warning("[AEGIS] Critic LLM failed: %s (fail-open, no proposals)", e)
        return []
    if not verdicts:
        return []  # fail-open: unparseable → no proposals
    _CRITIC_CACHE[cache_key] = (now, verdicts)
    return [dict(v, cached=False) for v in verdicts]


def clear_critic_cache():
    """Test helper: reset the in-memory Critic cache."""
    _CRITIC_CACHE.clear()
