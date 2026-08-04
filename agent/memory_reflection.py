"""
FLAG 记忆反思引擎 — 复用 curator idiom（空闲门控 + fork 辅助 agent + 持久化状态）

核心原则：
1. 只读原 memories 表，只 INSERT INTO memory_flags（绝不 LLM 触发的 UPDATE/DELETE）
2. fail-open：反思失败/超时 → 静默跳过，不影响主会话
3. 空闲触发 + 批处理 + 采样，成本可控
"""
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vermes_constants import get_vermes_home

logger = logging.getLogger(__name__)

# ── 持久化状态 ─────────────────────────────────────────────────────

def _reflection_state_path() -> Path:
    """镜像 curator .curator_state"""
    return get_vermes_home() / ".reflection_state"


def _load_state() -> Dict:
    """加载反思状态"""
    path = _reflection_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"last_run_at": None, "paused": False, "last_summary": ""}


def _save_state(state: Dict):
    """保存反思状态"""
    path = _reflection_state_path()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ── 空闲门控 ───────────────────────────────────────────────────────

def get_reflection_min_idle_hours() -> float:
    """复用 curator 空闲阈值（或新增配置）"""
    # 直接复制 curator 逻辑，避免循环 import
    DEFAULT_MIN_IDLE_HOURS = 0.5
    try:
        from vermes_constants import get_vermes_home
        cfg_path = get_vermes_home() / "curator_config.json"
        if cfg_path.exists():
            import json as _json
            cfg = _json.loads(cfg_path.read_text())
            return float(cfg.get("min_idle_hours", DEFAULT_MIN_IDLE_HOURS))
    except Exception:
        pass
    return DEFAULT_MIN_IDLE_HOURS


def _is_idle_enough() -> bool:
    """检查是否足够空闲（基于上次反思时间）"""
    state = _load_state()
    if state.get("paused"):
        return False

    last_run = state.get("last_run_at")
    if not last_run:
        return True  # 从未运行过

    try:
        last_dt = datetime.fromisoformat(last_run)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed_hours >= get_reflection_min_idle_hours()
    except Exception:
        return True  # 状态损坏时允许运行


# ── 数据库 Schema ──────────────────────────────────────────────────

def ensure_reflection_schema(db_path: Optional[Path] = None):
    """创建 memory_flags 表（幂等）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = db_path or _get_mem_db()
    if isinstance(db_path, str):
        db_path = Path(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_flags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id   TEXT NOT NULL,
                flag_type   TEXT NOT NULL,
                confidence  REAL DEFAULT 0.0,
                evidence    TEXT,
                status      TEXT NOT NULL DEFAULT 'open',
                created_at  TEXT NOT NULL,
                source      TEXT DEFAULT 'reflection'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_flags_status
            ON memory_flags(status, created_at)
        """)
        # 增量列（幂等：列已存在则跳过）
        for _col, _ctype in (("resolution", "TEXT"), ("resolved_at", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE memory_flags ADD COLUMN {_col} {_ctype}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


# ── 核心入口 ────────────────────────────────────────────────────────

def maybe_run_reflection(
    *,
    idle_for_seconds: Optional[float] = None,
    on_summary: Optional[Callable[[str], None]] = None,
) -> None:
    """空闲门控 + 触发反思（镜像 curator.maybe_run_curator）

    Args:
        idle_for_seconds: 调用方提供的空闲秒数；None 表示不检查。
        on_summary: 反思完成后的回调。
    """
    state = _load_state()
    if state.get("paused"):
        logger.debug("[Reflection] Paused, skip")
        return

    if not _is_idle_enough():
        logger.debug("[Reflection] Not idle enough, skip")
        return

    # Idle gating: only enforce when the caller provided a measurement.
    if idle_for_seconds is not None:
        min_idle_s = get_reflection_min_idle_hours() * 3600.0
        if idle_for_seconds < min_idle_s:
            return

    try:
        run_reflection_review()
        if on_summary:
            on_summary("Reflection completed")
    except Exception as e:
        logger.warning(f"[Reflection] Reflection failed: {e} (fail-open)")


def run_reflection_review():
    """执行反思（镜像 curator.run_curator_review）

    R1: 矛盾校核（复用 decision_tracker._check_contradiction）
    R2: 四类 LLM 校核（stale/contradiction_with_new/scope_creep/redundant）
    R3: P2 AEGIS 闭环提案引擎（挂 _scan_evolution_proposals，独立 3600s 间隔）
    R4: 待实现（continuity_facade 已注入 + /resolve_flag）
    """
    ensure_reflection_schema()
    logger.info("[Reflection] Starting reflection review...")

    flags_created = 0

    # R1: 矛盾校核
    try:
        flags_created += _scan_contradictions()
    except Exception as e:
        logger.warning(f"[Reflection] R1 contradiction scan failed: {e}")

    # R2: 四类 LLM 校核（stale / contradiction_with_new / scope_creep / redundant）
    try:
        flags_created += _scan_llm_flags()
    except Exception as e:
        logger.warning(f"[Reflection] R2 LLM scan failed: {e}")

    # R3: P2 AEGIS 闭环提案引擎（独立间隔，不与反思同频）
    try:
        _scan_evolution_proposals()
    except Exception as e:
        logger.warning(f"[Reflection] R3 AEGIS scan failed: {e}")

    # 更新状态
    state = _load_state()
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_summary"] = f"Reflection completed: {flags_created} flag(s) created"
    _save_state(state)

    logger.info("[Reflection] Reflection review done: %d flag(s)", flags_created)


# ── R3: P2 AEGIS 闭环提案引擎 ──────────────────────────────────────────

AEGIS_INTERVAL_S = 3600          # 独立扫描间隔（不跟反思 600s 同频）
AEGIS_MIN_SR_SLOPE_PP = -10.0    # 退化点斜率阈值（固定默认，发现灵敏度不外置）
AEGIS_MIN_SAMPLES = 20           # 退化点最小样本（固定默认）
PROPOSAL_EXPIRE_DAYS = 7         # 提案过期天数（外置友好，但固定默认）
# L1 自动 apply 后的撤回窗口。上限受 EmergentChangePipeline.MAX_BACKUPS_PER_FILE=5
# 约束：同一文件被连续 apply 超过 5 次后，早期备份会被清理，届时撤回将失败并
# 返回明确错误（而不是错误地还原成别的快照）。
L1_RETRACT_WINDOW_H = 24
# T4 幅度护栏：即便双闸门都过，单个数值 dial 的相对变化超过该比例仍强制降级到
# L2 待审队列。闸门衡量的是"效果没变差"，衡量不了"改动有多激进"——一次把阈值
# 从 0.9 拉到 0.5 也可能在离线回放里"precision 不降"，但那已经是换了策略而不是
# 微调。分层模型三维度之三（幅度）：≤10% L0 / 10~20% L1 / >20% L2。
L1_MAX_RELATIVE_DELTA = 0.20


def _aegis_should_run() -> bool:
    """Independent 3600s gating for the AEGIS scan (separate from reflection)."""
    state = _load_state()
    last = state.get("last_aegis_run_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_dt).total_seconds() >= AEGIS_INTERVAL_S
    except Exception:
        return True


def _aegis_mark_run():
    state = _load_state()
    state["last_aegis_run_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)


def _discover_regression_points() -> List[Dict]:
    """Phase A: 从 v_outcomes 发现退化点（固定默认灵敏度，不外置）。

    Returns list of {task_type, baseline_sr, recent_sr, delta, n}.
    """
    try:
        from agent.evolution_manager import get_self_model_db, _get_conn

        db = get_self_model_db()
        if not db.exists():
            return []
        conn = _get_conn(str(db))
        rows = conn.execute(
            """SELECT tool,
                      SUM(CASE WHEN timestamp >= datetime('now','-7 days') THEN 1 ELSE 0 END) AS n7,
                      SUM(CASE WHEN timestamp >= datetime('now','-7 days') THEN success ELSE 0 END) AS s7,
                      SUM(CASE WHEN timestamp >= datetime('now','-30 days') THEN 1 ELSE 0 END) AS n30,
                      SUM(CASE WHEN timestamp >= datetime('now','-30 days') THEN success ELSE 0 END) AS s30
               FROM v_outcomes
               GROUP BY tool"""
        ).fetchall()
        points = []
        for tool, n7, s7, n30, s30 in rows:
            if not n30 or n30 < AEGIS_MIN_SAMPLES:
                continue
            base_sr = (s30 / n30 * 100) if n30 else 0
            recent_sr = (s7 / n7 * 100) if n7 else base_sr
            delta = recent_sr - base_sr
            if delta <= AEGIS_MIN_SR_SLOPE_PP:
                points.append({
                    "task_type": tool,
                    "baseline_sr": round(base_sr, 1),
                    "recent_sr": round(recent_sr, 1),
                    "delta": round(delta, 1),
                    "n": int(n30),
                })
        return points
    except Exception as e:
        logger.warning("[AEGIS] discover_regression_points failed: %s", e)
        return []


def _generate_b1_candidates(regression_points: List[Dict], llm_call=None) -> List[Dict]:
    """Phase B: 只生成 B1 配置级候选（调 autoResolve 拨盘）。

    Returns list of candidate dicts aligned to regression_points.
    LLM 不可用时返回 []（fail-open）。
    """
    if not regression_points:
        return []
    if llm_call is None:
        try:
            from agent.curator import _run_llm_review
            llm_call = _run_llm_review
        except Exception:
            return []

    items = "\n".join(
        f"{i+1}. task_type={p['task_type']} | 基线成功率={p['baseline_sr']}% "
        f"近7d={p['recent_sr']}% | 降幅={p['delta']}pp | 样本={p['n']}"
        for i, p in enumerate(regression_points)
    )
    prompt = (
        "你是一个进化架构师。以下工具/任务类型出现成功率退化。"
        "请为每条退化点生成一个自我改进提案：仅调整 config.yaml 的 "
        "memory.autoResolve 拨盘（可选 duplicate / outdated / cluster_min_interval / "
        "merge_cleanup 之一），目标是在不误杀边缘案例的前提下缓解退化。\n"
        "对每条返回 JSON 对象："
        '{"idx": 序号, "title": "简短标题", "rationale": "理由", '
        '"config_patch": {"memory": {"autoResolve": {"<dial>": <值>}}}, '
        '"expected_effect": "预期效果"}。返回 JSON 数组。\n\n'
        f"退化点：\n{items}"
    )
    try:
        resp = llm_call(prompt)
        text = resp.get("final", "") if isinstance(resp, dict) else str(resp)
        s = text.find("[")
        e = text.rfind("]")
        if s == -1 or e == -1 or e <= s:
            return []
        data = json.loads(text[s:e + 1])
        cands = []
        for d in data:
            if not isinstance(d, dict):
                continue
            patch = d.get("config_patch")
            cands.append({
                "task_type": regression_points[int(d.get("idx", 0)) - 1]["task_type"]
                if isinstance(d.get("idx"), int) else "",
                "title": str(d.get("title", "")),
                "rationale": str(d.get("rationale", "")),
                "config_patch": patch,
                "expected_effect": str(d.get("expected_effect", "")),
            })
        return cands
    except Exception as e:
        logger.warning("[AEGIS] B1 candidate generation failed: %s (fail-open)", e)
        return []


def _scan_evolution_proposals(llm_call=None):
    """P2 AEGIS 闭环：A 退化点 → B 候选 → C Critic → D 确定性闸门 → 入队。

    整体 fail-open：任何阶段异常都不影响反思 daemon；LLM 不可用则无提案。
    独立 3600s 间隔（不跟反思同频）。
    """
    if not _aegis_should_run():
        logger.debug("[AEGIS] skipped (within interval)")
        return 0

    # 过期陈旧提案（评审补充 a）
    try:
        from agent.evolution_manager import expire_stale_proposals
        expired = expire_stale_proposals(PROPOSAL_EXPIRE_DAYS)
        if expired:
            logger.info("[AEGIS] expired %d stale proposal(s)", expired)
    except Exception as e:
        logger.debug("[AEGIS] expire_stale_proposals failed: %s", e)

    created = 0
    try:
        # Phase A
        points = _discover_regression_points()
        if not points:
            logger.debug("[AEGIS] no regression points")
            return 0

        # Phase B (B1 only)
        candidates = _generate_b1_candidates(points, llm_call=llm_call)
        if not candidates:
            logger.debug("[AEGIS] no candidates generated")
            return 0

        # Phase C — Critic（批处理 + 缓存 + 硬编码护栏）
        from agent.emergence_critic import (
            critic_review, hardcoded_guard, run_deterministic_gate,
        )
        from agent.evolution_manager import record_proposal
        from vermes_cli.config import get_config_path as _cfg_path

        verdicts = critic_review(candidates, outcomes_summary="", llm_call=llm_call)
        if not verdicts:
            logger.debug("[AEGIS] Critic produced no verdicts (fail-open)")
            return 0

        # Phase D — 确定性闸门（仅对 Critic 通过者）
        for cand, v in zip(candidates, verdicts):
            # C1: 硬编码护栏（删表/设0/越界/越权段）
            ok, reason = hardcoded_guard(cand.get("config_patch"))
            if not ok:
                logger.info("[AEGIS] rejected by hard guard: %s — %s", cand.get("title"), reason)
                continue
            # C2: Critic 否决或低置信
            if not v.get("safe") or v.get("confidence", 0) < 0.7:
                logger.info("[AEGIS] critic rejected: %s — %s (conf=%.2f)",
                            cand.get("title"), v.get("concerns"), v.get("confidence", 0))
                continue
            # D: 确定性闸门（precision 不降 + 数量不爆）
            try:
                new_cfg = _merge_auto_resolve_patch(cand["config_patch"])
                gate = run_deterministic_gate(new_cfg=new_cfg)
            except Exception as e:
                logger.warning("[AEGIS] deterministic gate failed: %s", e)
                continue
            if not gate["passed"]:
                logger.info("[AEGIS] deterministic gate failed: %s — %s",
                            cand.get("title"), gate)
                continue
            # ── 分层：过了双闸门的低风险提案自动 apply，没过的才入待审队列
            # 用户可随时在 EvolutionPanel 撤回已自动 apply 的提案
            # T4 幅度护栏：改动过猛（>20%）即便闸门都过也强制人工审
            over, over_reason = _exceeds_magnitude(cand.get("config_patch"))
            if over:
                logger.info("[AEGIS] magnitude guard → L2 queue: %s — %s",
                            cand.get("title"), over_reason)
            # T6：自动 apply 的基线是 L1（写前备份 + 24h 可撤回）。
            # tier_mode=conservative 收紧成 L2 —— 配置也要先问一次。
            _tier, _tier_why = _config_apply_tier()
            if _tier != "L1" and not over:
                over_reason = _tier_why
                logger.info("[AEGIS] tier_mode → L2 queue: %s", cand.get("title"))
            if _is_auto_apply_enabled() and not over and _tier == "L1":
                # 自动 apply：写 config.yaml + 记录为 auto_applied
                applied = _auto_apply_proposal(cand, v, gate, str(_cfg_path()))
                if applied:
                    created += 1
                    logger.info("[AEGIS] auto-applied: %s", cand.get("title"))
                else:
                    # 自动 apply 失败（写入异常等）→ 降级进待审队列
                    pid = record_proposal(
                        phase="A→B→C→D",
                        task_type=cand.get("task_type", ""),
                        title=cand.get("title", ""),
                        rationale=cand.get("rationale", ""),
                        target_kind="config",
                        target_path=str(_cfg_path()),
                        config_patch=cand.get("config_patch"),
                        critic_verdict=v,
                        deterministic_result=gate,
                        status="proposed",
                    )
                    if pid:
                        created += 1
                        logger.info("[AEGIS] auto-apply failed, queued #%s: %s", pid, cand.get("title"))
            else:
                # auto_apply=false / 幅度过大 / tier_mode 收紧 → 进待审队列（L2）
                _gate = dict(gate)
                if over or _tier != "L1":
                    _gate["tier"] = "L2"
                    _gate["tier_reason"] = over_reason
                pid = record_proposal(
                    phase="A→B→C→D",
                    task_type=cand.get("task_type", ""),
                    title=cand.get("title", ""),
                    rationale=cand.get("rationale", ""),
                    target_kind="config",
                    target_path=str(_cfg_path()),
                    config_patch=cand.get("config_patch"),
                    critic_verdict=v,
                    deterministic_result=_gate,
                    status="proposed",
                )
                if pid:
                    created += 1
                    logger.info("[AEGIS] proposal #%s queued: %s", pid, cand.get("title"))
    finally:
        _aegis_mark_run()
    return created


def _config_apply_tier() -> tuple:
    """config 自动 apply 的有效档位，返回 ``(tier, reason)``。

    基线 L1：改动可逆（写前 .bak + 面板 24h 撤回）且有通知。用户的
    ``approvals.tier_mode`` 可以收紧到 L2（先问再改）。

    刻意**不**让 autonomous 放宽 T4 幅度护栏：那条护栏的定位是「即便双闸门
    都过也要人看一眼」的硬底线，而 >20% 的改动虽然技术上可逆，实际却要靠
    用户在 24h 内自己察觉记忆质量变差才会去撤 —— 那种「可逆」是纸面上的。
    """
    try:
        from tools.approval import effective_tier
        tier = effective_tier("L1", reversible=True)
    except Exception:
        return "L1", ""
    if tier == "L1":
        return "L1", ""
    return tier, "tier_mode=conservative：配置自动调整也先经人工确认"


def _max_relative_delta() -> float:
    """幅度护栏阈值。外置到 `evolution.autoApplyMaxDelta`，沿用 P1 的 >0 注入护栏
    （写 0 或负数一律回落默认，防止"把护栏设成 0 = 永远不拦"这类自我放行）。"""
    try:
        from vermes_cli.config import load_config
        raw = load_config().get("evolution", {}).get("autoApplyMaxDelta", None)
        val = float(raw)
        if val > 0:
            return val
    except Exception:
        pass
    return L1_MAX_RELATIVE_DELTA


def _flatten_numeric(d, prefix=""):
    """Yield ``(dotted_key, float_value)`` for every numeric leaf in a dict."""
    if not isinstance(d, dict):
        return
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            yield from _flatten_numeric(v, key)
        elif isinstance(v, bool):
            continue  # bool 是 int 的子类，但"翻转开关"不适用相对幅度
        elif isinstance(v, (int, float)):
            yield key, float(v)


def _exceeds_magnitude(config_patch) -> tuple:
    """T4 幅度护栏：patch 中任一数值 dial 的相对变化是否超过阈值。

    返回 ``(exceeded: bool, reason: str)``。

    只看数值型叶子：相对变化 = ``|new - old| / max(|old|, eps)``。
    - 当前配置里**不存在**该键（新增 dial）→ 无基准，按保守处理判为超限；
    - 布尔/字符串等非数值改动不参与本护栏（由 hardcoded_guard 与 Critic 负责）；
    - 任何异常一律 fail-safe 判为超限（宁可多让人看一眼，也不静默放行激进改动）。
    """
    if not isinstance(config_patch, dict) or not config_patch:
        return False, ""
    try:
        from vermes_cli.config import load_config
        cur = load_config()
    except Exception as e:
        return True, f"无法读取当前配置以计算幅度({e})，保守降级到人工审"

    # autoResolve 子树的"当前值"必须走 _load_auto_resolve_config()——它做了
    # 短名/长名别名归一 + >0 护栏 + 硬编码兜底，直接查 raw config 会在用户
    # 没写该键时误判成"新增 dial 无基准"。
    try:
        _ar_effective = _load_auto_resolve_config()
    except Exception:
        _ar_effective = {}

    limit = _max_relative_delta()
    worst = None
    for key, new_val in _flatten_numeric(config_patch):
        old_val = None
        if key.startswith("memory.autoResolve."):
            _leaf = key.split(".")[-1]
            if _leaf in _ar_effective:
                old_val = float(_ar_effective[_leaf])
        if old_val is None:
            node = cur
            try:
                for part in key.split("."):
                    node = node[part]
                if isinstance(node, bool) or not isinstance(node, (int, float)):
                    continue
                old_val = float(node)
            except Exception:
                return True, f"{key}: 当前配置无此项，新增 dial 无基准可比 → 人工审"
        denom = abs(old_val) if abs(old_val) > 1e-9 else 1e-9
        delta = abs(new_val - old_val) / denom
        if worst is None or delta > worst[1]:
            worst = (key, delta, old_val, new_val)
    if worst and worst[1] > limit:
        key, delta, old_val, new_val = worst
        return True, (f"{key}: {old_val:g} → {new_val:g}"
                      f"（相对变化 {delta:.0%} > 上限 {limit:.0%}）")
    return False, ""


def _is_auto_apply_enabled() -> bool:
    """Check if auto-apply is enabled for low-risk B1 config proposals.

    Fail-CLOSED on purpose.  The two failure modes cost wildly different
    amounts: wrongly falling back to auto-apply silently rewrites the user's
    config with no notification path (see config.py "evolution.auto_apply"),
    while wrongly falling back to the review queue costs one click.  A config
    read that raises is precisely the moment we know least about the user's
    intent, so it must not be the moment we act most autonomously.
    """
    try:
        from vermes_cli.config import load_config
        cfg = load_config()
        return bool(cfg.get("evolution", {}).get("auto_apply", False))
    except Exception:
        logger.warning(
            "[evolution] auto_apply 配置读取失败 → 保守降级，提案改走待审队列")
        return False  # fail-closed: queue it, don't apply it


def _auto_apply_proposal(cand: dict, critic_verdict: dict,
                          gate_result: dict, config_path: str) -> bool:
    """Auto-apply a B1 config proposal that passed all gates.

    Writes config.yaml via EmergentChangePipeline (backup + raw_event + rollback).
    Records the proposal as 'auto_applied' in evolution_proposals.
    Returns True on success.
    """
    try:
        from vermes_cli.config import load_config
        from agent.emergent_change import get_pipeline, ChangeProposal
        from agent.evolution_manager import record_proposal
        import yaml

        config_patch = cand.get("config_patch", {})
        merged = _deep_merge_config(load_config(), config_patch)
        new_content = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False)

        proposal_obj = ChangeProposal(
            source="aegis-auto",
            target_path=config_path,
            content=new_content,
            description=f"[AEGIS 自动] {cand.get('title', '')}",
            metadata={"auto_applied": True, "critic": critic_verdict},
            initiator="system",  # system = auto, not agent-initiated
        )
        result = get_pipeline().apply_change(proposal_obj, force=True)
        if result.committed:
            # 绑定 *本次* 变更的备份路径：apply_change 已把它放在 result 里。
            # 不能事后靠"目录里最新的 .bak"反推——连续 apply 时最新备份属于
            # 后一次变更，且 _cleanup_old_backups 只保留 5 份。
            _deadline = (datetime.now(timezone.utc)
                         + timedelta(hours=L1_RETRACT_WINDOW_H)).isoformat()
            _bak = getattr(result, "backup_path", None)
            _pid = record_proposal(
                phase="A→B→C→D (auto)",
                task_type=cand.get("task_type", ""),
                title=cand.get("title", ""),
                rationale=cand.get("rationale", ""),
                target_kind="config",
                target_path=config_path,
                config_patch=config_patch,
                critic_verdict=critic_verdict,
                deterministic_result=gate_result,
                status="auto_applied",
                bak_path=_bak,
                retract_deadline=_deadline,
                applied_by="agent",
            )
            # T5：通知用户。L1 = 静默执行 + **告知** + 可撤回；少了告知这一半，
            # 自动 apply 就是在背着用户改他的 config —— 记账失败不回滚变更
            # （账本是旁路），但 record_change 内部会 warning。
            try:
                from agent.change_ledger import (
                    record_change, KIND_CONFIG_AUTO_APPLY, TIER_L1, REF_PROPOSAL,
                )
                record_change(
                    kind=KIND_CONFIG_AUTO_APPLY,
                    tier=TIER_L1,
                    title=cand.get("title", "") or "自动调整配置",
                    summary=cand.get("rationale", ""),
                    detail={"config_patch": config_patch,
                            "expected_effect": cand.get("expected_effect", ""),
                            "critic": critic_verdict},
                    target_path=config_path,
                    bak_path=_bak,
                    retract_deadline=_deadline,
                    ref_kind=REF_PROPOSAL,
                    ref_id=_pid,
                )
            except Exception as e:
                logger.warning("[AEGIS] change ledger notify failed: %s", e)
            return True
        return False
    except Exception as e:
        logger.warning("[AEGIS] auto-apply failed: %s", e)
        return False


def _deep_merge_config(base: dict, patch: dict) -> dict:
    """Recursively merge patch into a copy of base."""
    import copy
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_config(out[k], v)
        else:
            out[k] = v
    return out


def _merge_auto_resolve_patch(patch: Dict) -> Dict:
    """Build the full new autoResolve config by applying patch over current."""
    cfg = _load_auto_resolve_config()
    ar = (patch or {}).get("memory", {}).get("autoResolve", {})
    for k, v in ar.items():
        if k in cfg:
            cfg[k] = float(v)
    return cfg


# ── R1: 矛盾校核 ────────────────────────────────────────────────

def _scan_contradictions() -> int:
    """扫描 @decision 标签的记忆，检测两两矛盾。

    复用 decision_tracker._check_contradiction 纯函数。
    只读 memories 表，只 INSERT INTO memory_flags。

    Returns:
        新增 flag 数量
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db
    from agent.decision_tracker import (
        _check_contradiction,
        _extract_decision_keywords,
    )

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 读取所有 @decision 记忆（lifecycle_tag=decision）
        rows = conn.execute(
            """SELECT id, fts_content, pointer, scope
               FROM memories
               WHERE lifecycle_tag = 'decision'
               ORDER BY id DESC
               LIMIT 100""",
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        logger.debug("[Reflection] R1: <2 decision memories, skip")
        return 0

    # 提取关键词
    decisions = []
    for row in rows:
        mem_id = str(row[0])
        content = row[1] or ""
        keywords = _extract_decision_keywords(content)
        if keywords:  # 跳过无关键词的
            decisions.append({
                "id": mem_id,
                "content": content,
                "keywords": keywords,
            })

    flags_created = 0
    # 两两比对（O(n²) 但 n≤100，可接受）
    for i in range(len(decisions)):
        for j in range(i + 1, len(decisions)):
            new = decisions[i]  # 较新的（DESC 排序）
            old = decisions[j]  # 较旧的

            reason = _check_contradiction(
                new_decision=new["content"],
                old_decision=old["content"],
                new_keywords=new["keywords"],
                old_keywords=old["keywords"],
            )
            if reason:
                flag_id, is_new = write_flag(
                    memory_id=new["id"],
                    flag_type="contradiction",
                    evidence=f"与记忆 #{old['id']} 矛盾: {reason}",
                    confidence=0.7,
                )
                if is_new:
                    flags_created += 1
                    logger.info(
                        "[Reflection] R1: contradiction found "
                        "between #%s and #%s: %s",
                        new["id"], old["id"], reason,
                    )

    logger.info("[Reflection] R1: scanned %d decisions, %d flags created",
                len(decisions), flags_created)
    return flags_created


# ── R2: 四类 LLM 校核 ────────────────────────────────────────────────

# R2 四类问题 → memory_flags.flag_type 映射
_R2_FLAG_TYPES = {
    "stale": "outdated",
    "outdated": "outdated",
    "contradiction": "contradiction",
    "contradiction_with_new": "contradiction",
    "scope_creep": "scope_creep",
    "redundant": "duplicate",
    "duplicate": "duplicate",
}


def _reflection_llm_review(prompt: str) -> Dict:
    """惰性复用 curator._run_llm_review（fork 辅助 agent）。

    失败返回带 error 的结构，由调用方 fail-open 处理。
    测试可通过 monkeypatch 替换本函数，避免真实 LLM 调用。
    """
    try:
        from agent.curator import _run_llm_review
        return _run_llm_review(prompt)
    except Exception as e:
        logger.warning(f"[Reflection] LLM review call failed: {e}")
        return {"final": "", "summary": "", "error": str(e)}


def _build_r2_prompt(content: str) -> str:
    """构造 R2 四类校核 prompt"""
    return (
        "你是一个记忆质量审查器。判断以下记忆是否存在四类问题之一；"
        "若有，返回 JSON 数组，每项格式："
        '{"class": "stale|contradiction_with_new|scope_creep|redundant", '
        '"confidence": 0.0-1.0, "evidence": "简短理由"}。\n'
        "分类说明：stale=事实可能已过时；contradiction_with_new=与已知新信息矛盾；"
        "scope_creep=适用范围被不当泛化；redundant=与其他已知记忆重复。\n"
        "若无问题返回 []。\n\n记忆内容：\n"
        + content[:2000]
    )


def _parse_r2_response(text: str) -> List[Dict]:
    """从 LLM 返回解析 JSON 数组（fail-open：解析失败返回 []）"""
    if not text:
        return []
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        data = json.loads(text[start:end + 1])
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception:
        pass
    return []


def _scan_llm_flags(limit: int = 50) -> int:
    """R2: 四类 LLM 一致性校核。

    读取近期记忆（跳过 @decision/@preference，已由 R1 覆盖），逐条调 LLM
    分类四类问题：stale / contradiction_with_new / scope_creep / redundant。
    只读 memories 表，只 INSERT memory_flags。fail-open。

    Returns:
        新增 flag 数量（仅 is_new）
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 排除已有 false_positive flag 的记忆——用户已标记为误报，
        # 不应再被 LLM 反复扫描和重新创建 flag
        rows = conn.execute(
            """SELECT m.id, m.fts_content, m.pointer, m.scope
               FROM memories m
               WHERE (m.lifecycle_tag IS NULL
                      OR m.lifecycle_tag NOT IN ('decision', 'preference'))
                 AND m.id NOT IN (
                   SELECT DISTINCT memory_id
                   FROM memory_flags
                   WHERE status = 'resolved' AND resolution = 'false_positive'
                 )
               ORDER BY m.id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.debug("[Reflection] R2: no candidate memories, skip")
        return 0

    flags_created = 0
    for row in rows:
        mem_id = str(row[0])
        content = row[1] or ""
        if len(content) < 20:
            continue  # 过短记忆无意义
        result = _reflection_llm_review(_build_r2_prompt(content))
        if result.get("error"):
            continue  # fail-open：LLM 失败跳过本条
        for issue in _parse_r2_response(result.get("final", "")):
            ftype = _R2_FLAG_TYPES.get(str(issue.get("class", "")).lower())
            if not ftype:
                continue
            try:
                conf = float(issue.get("confidence", 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            flag_id, is_new = write_flag(
                memory_id=mem_id,
                flag_type=ftype,
                evidence=str(issue.get("evidence", ""))[:500],
                confidence=conf,
            )
            if is_new:
                flags_created += 1
                logger.info(
                    "[Reflection] R2: %s flag for #%s: %s",
                    ftype, mem_id, str(issue.get("evidence", ""))[:80]
                )
    logger.info("[Reflection] R2: scanned %d memories, %d flags created",
                len(rows), flags_created)
    return flags_created


# ── 辅助函数 ────────────────────────────────────────────────────────────

def get_open_flags(limit: int = 50) -> List[Dict]:
    """获取 open 状态的 flags（供 continuity_facade 注入 + 前端面板）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute(
                """SELECT f.id, f.memory_id, f.flag_type, f.confidence, f.evidence,
                          f.created_at, m.source
                   FROM memory_flags f
                   LEFT JOIN memories m ON CAST(f.memory_id AS INTEGER) = m.id
                   WHERE f.status = 'open'
                   ORDER BY f.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        except Exception:
            # P2-10: memories 表不存在时回退到不 JOIN
            rows = conn.execute(
                """SELECT id, memory_id, flag_type, confidence, evidence, created_at
                   FROM memory_flags
                   WHERE status = 'open'
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "flag_type": r[2],
                "confidence": r[3],
                "evidence": r[4],
                "created_at": r[5],
                "source": r[6] if len(r) > 6 else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


def format_flags_for_context(flags: List[Dict]) -> str:
    """格式化 flags 供 continuity_facade 注入"""
    if not flags:
        return ""

    # 按类型分组
    by_type = {}
    for f in flags:
        t = f["flag_type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(f)

    lines = ["[Reflection] 潜在记忆问题："]
    for flag_type, items in by_type.items():
        type_names = {
            "contradiction": "矛盾",
            "outdated": "过时",
            "scope_creep": "范围漂移",
            "hallucination": "幻觉",
            "duplicate": "重复",
        }
        lines.append(f"  - {type_names.get(flag_type, flag_type)}: {len(items)} 条")

    lines.append("  建议：使用 /resolve_flag <id> <demote|merge|false_positive> 处理")
    return "\n".join(lines)


def write_flag(memory_id: str, flag_type: str, evidence: str, confidence: float = 0.8) -> tuple:
    """写入单条 flag（去重：同 memory_id + flag_type 已 open 则跳过）

    Returns:
        (flag_id, is_new) — flag_id 为数据库 ID；is_new 表示是否本次新增。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 去重检查：跳过已 open 的，也跳过已标记为 false_positive 的 resolved flag
        existing = conn.execute(
            """SELECT id FROM memory_flags
               WHERE memory_id = ? AND flag_type = ?
               AND (status = 'open'
                    OR (status = 'resolved' AND resolution = 'false_positive'))""",
            (memory_id, flag_type),
        ).fetchone()
        if existing:
            logger.debug(f"[Reflection] Flag already exists for {memory_id}/{flag_type}")
            return (existing[0], False)

        # 插入新 flag
        cursor = conn.execute(
            """INSERT INTO memory_flags
               (memory_id, flag_type, confidence, evidence, status, created_at, source)
               VALUES (?, ?, ?, ?, 'open', ?, 'reflection')""",
            (memory_id, flag_type, confidence, evidence, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return (cursor.lastrowid, True)
    finally:
        conn.close()


# ── R4: resolve_flag ────────────────────────────────────────────────

def get_flag(flag_id: int) -> Optional[Dict]:
    """读取单条 flag（供 API/CLI 校验）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT id, memory_id, flag_type, confidence, evidence,
                      status, resolution, resolved_at, created_at
               FROM memory_flags WHERE id = ?""",
            (flag_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "memory_id": row[1], "flag_type": row[2],
            "confidence": row[3], "evidence": row[4], "status": row[5],
            "resolution": row[6], "resolved_at": row[7], "created_at": row[8],
        }
    finally:
        conn.close()


def resolve_flag(flag_id: int, resolution: str) -> bool:
    """标记 flag 为已解决，并（按 resolution 类型）联动演进原记忆。

    铁律边界说明：铁律禁的是**无差别改写原 memories**；这里对 demote 类
    flag 的精确降级（把被标记记忆的 lifecycle_tag 置 ephemeral）属于用户
    显式意图（/resolve_flag demote = "这条记忆降权/弃用"），是合法演进，
    与"反思引擎静默改写记忆"不同。

    Args:
        flag_id: flag 主键
        resolution: demote | merge | false_positive
    Returns:
        是否成功（flag 存在且原为 open 才更新）
    """
    if resolution not in ("demote", "merge", "false_positive"):
        logger.warning("[Reflection] Invalid resolution: %s", resolution)
        return False

    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 先取 memory_id（demote 时需联动原记忆）；flag 不存在直接返回 False
        _row = conn.execute(
            "SELECT memory_id FROM memory_flags WHERE id = ? AND status = 'open'",
            (flag_id,),
        ).fetchone()
        if not _row:
            return False
        _memory_id = _row[0]

        cur = conn.execute(
            """UPDATE memory_flags
               SET status = 'resolved', resolution = ?, resolved_at = ?
               WHERE id = ? AND status = 'open'""",
            (resolution, datetime.now(timezone.utc).isoformat(), flag_id),
        )
        if cur.rowcount == 0:
            return False

        # ── P3-⑩: demote 联动降级原记忆 → lifecycle_tag='ephemeral' ──
        # fail-open：memories 表缺失/该行不存在/类型不匹配都不应阻断 flag 解决。
        # P1-5 fix: demote 时保存原始 lifecycle_tag，供 restore 精确还原。
        if resolution == "demote":
            try:
                try:
                    _mid = int(_memory_id)
                except (TypeError, ValueError):
                    _mid = _memory_id
                # 读取原始 lifecycle_tag
                _orig = conn.execute(
                    "SELECT lifecycle_tag FROM memories WHERE id = ?", (_mid,)
                ).fetchone()
                _orig_tag = _orig[0] if _orig else "reference"
                _uc = conn.execute(
                    "UPDATE memories SET lifecycle_tag='ephemeral' WHERE id = ?",
                    (_mid,),
                ).rowcount
                # 保存原始 tag 到 flag 行（确保列存在）
                try:
                    conn.execute(
                        "ALTER TABLE memory_flags ADD COLUMN prev_lifecycle_tag TEXT"
                    )
                except Exception:
                    pass  # 列已存在
                conn.execute(
                    "UPDATE memory_flags SET prev_lifecycle_tag = ? WHERE id = ?",
                    (_orig_tag, flag_id),
                )
                logger.info(
                    "[Reflection] resolve_flag demote: flag=%s memory=%s → "
                    "lifecycle_tag='ephemeral' (was '%s', %d row(s))",
                    flag_id, _memory_id, _orig_tag, _uc,
                )
            except Exception:
                logger.warning(
                    "[Reflection] resolve_flag demote: memories 联动降级失败"
                    "（flag 已 resolved，非致命）",
                    exc_info=True,
                )

        # ── merge 真合并：删除被标记的 skill 重复记忆 ──
        # 技能描述每轮经 available_skills 注入，memories 表留它们是冗余。
        # merge 时删除 memory 行（仅 source=skill），flag 保留为合并记录。
        # fail-open：memories 表缺失/行不存在/非 skill 源都不阻断 flag 解决。
        if resolution == "merge":
            try:
                try:
                    _mid = int(_memory_id)
                except (TypeError, ValueError):
                    _mid = _memory_id
                _dc = conn.execute(
                    "DELETE FROM memories WHERE id = ? AND source = 'skill'",
                    (_mid,),
                ).rowcount
                if _dc > 0:
                    logger.info(
                        "[Reflection] resolve_flag merge: flag=%s memory=%s → deleted skill duplicate (%d row)",
                        flag_id, _memory_id, _dc,
                    )
            except Exception:
                logger.warning(
                    "[Reflection] resolve_flag merge: memory cleanup failed"
                    "（flag 已 resolved，非致命）",
                    exc_info=True,
                )

        conn.commit()
        return True
    finally:
        conn.close()


# ── R5: restore_flag ────────────────────────────────────────────────

def restore_flag(flag_id: int) -> bool:
    """用户反降级：恢复被 demote 的记忆权重 + 重开 flag。

    三路语义：
      demote       → flag→open + lifecycle_tag→prev_lifecycle_tag（精确还原）
      merge        → flag→open（记忆已被合并清理，重开 flag 但不恢复已删 memory）
      false_positive → flag→open（只重开审视，不改 lifecycle_tag）

    P1-5 fix: 不再硬编码 reference，从 flag.prev_lifecycle_tag 读取原始值。
    若 prev_lifecycle_tag 不存在/为空（旧数据），回退到 reference（硬编码兜底）。

    fail-open：memories 表缺失/行不存在/类型不符不阻断 flag 重开。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        _row = conn.execute(
            "SELECT memory_id, resolution FROM memory_flags "
            "WHERE id = ? AND status = 'resolved'",
            (flag_id,),
        ).fetchone()
        if not _row:
            logger.warning("[Reflection] restore_flag: flag %s not found or not resolved", flag_id)
            return False

        _memory_id, _resolution = _row[0], _row[1]

        # demote 恢复：联动把 lifecycle_tag 改回原始值
        if _resolution == "demote":
            try:
                try:
                    _mid = int(_memory_id)
                except (TypeError, ValueError):
                    _mid = _memory_id
                # P1-5 fix: 从 flag 行读取原始 tag，不硬编码 reference
                try:
                    _prev = conn.execute(
                        "SELECT prev_lifecycle_tag FROM memory_flags WHERE id = ?",
                        (flag_id,),
                    ).fetchone()
                    _restore_tag = (_prev and _prev[0]) or "reference"
                except Exception:
                    _restore_tag = "reference"  # 旧库无列，回退到 reference
                _uc = conn.execute(
                    "UPDATE memories SET lifecycle_tag=? WHERE id = ?",
                    (_restore_tag, _mid),
                ).rowcount
                logger.info(
                    "[Reflection] restore_flag: flag=%s memory=%s → lifecycle_tag='%s' (%d row(s))",
                    flag_id, _memory_id, _restore_tag, _uc,
                )
            except Exception:
                logger.warning(
                    "[Reflection] restore_flag: memories 联动恢复失败（非致命）",
                    exc_info=True,
                )

        # 所有类型：flag 退回 open
        conn.execute(
            "UPDATE memory_flags SET status='open', resolution=NULL, resolved_at=NULL "
            "WHERE id = ?",
            (flag_id,),
        )
        conn.commit()
        logger.info("[Reflection] restore_flag: flag=%s resolution=%s → reopened", flag_id, _resolution)
        return True
    except Exception:
        logger.warning("[Reflection] restore_flag failed", exc_info=True)
        return False
    finally:
        conn.close()


def get_resolved_flags(limit: int = 200) -> List[Dict]:
    """获取 resolved 状态的 flags（供"已解决"视图展示+恢复操作）。"""
    from agent.memory_fabric import _get_index_db as _get_mem_db, _init_db as _fab_init

    db_path = _get_mem_db()  # Path
    _fab_init(db_path)  # P2-10 fix: 确保表存在（传 Path）
    db_path_str = str(db_path)

    conn = sqlite3.connect(db_path_str)
    try:
        # P2-5 fix: 尝试带 prev_lifecycle_tag（旧库无列时 fail-open 返回 None）
        try:
            rows = conn.execute(
                """SELECT id, memory_id, flag_type, confidence, evidence,
                          created_at, resolution, resolved_at, prev_lifecycle_tag
                   FROM memory_flags
                   WHERE status = 'resolved'
                   ORDER BY resolved_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            has_prev = True
        except Exception:
            rows = conn.execute(
                """SELECT id, memory_id, flag_type, confidence, evidence,
                          created_at, resolution, resolved_at
                   FROM memory_flags
                   WHERE status = 'resolved'
                   ORDER BY resolved_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            has_prev = False
        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "flag_type": r[2],
                "confidence": r[3],
                "evidence": r[4],
                "created_at": r[5],
                "resolution": r[6],
                "resolved_at": r[7],
                "prev_lifecycle_tag": r[8] if has_prev and len(r) > 8 else None,
            }
            for r in rows
        ]
    finally:
        conn.close()


# ── 行动环闭合：自动降级 ──────────────────────────────────────────────

def _load_auto_resolve_config():
    """Load auto-resolve thresholds from config.yaml → normalize to dict.

    Returns dict with keys: duplicate, outdated, cluster_min_interval.
    Missing keys / missing file → hardcoded defaults (backward-compatible).
    """
    defaults = {
        "duplicate": 0.9,
        "outdated": 0.85,
        "cluster_min_interval": 60,
        "merge_cleanup": 0.7,
    }
    try:
        from vermes_cli.config import load_config

        cfg = load_config()
        mem = cfg.get("memory", {})
        ar = mem.get("autoResolve", {})
        if isinstance(ar, dict) and ar:
            # 兼容旧的长键名（config.py 默认段一度写的是这套名字，而这里只读
            # 短名 → 用户改了长名却毫无效果）。短名优先，长名作别名回落。
            for _short, _legacy in (
                ("duplicate", "duplicate_confidence"),
                ("outdated", "outdated_confidence"),
                ("cluster_min_interval", "cluster_min_interval_s"),
                ("merge_cleanup", "merge_cleanup_confidence"),
            ):
                if _short not in ar and _legacy in ar:
                    ar = dict(ar)
                    ar[_short] = ar[_legacy]
            for k in defaults:
                # Guard: only strictly-positive values are valid. A configured
                # `0` would (a) flatten the cluster death-interval floor and
                # reintroduce Bug 1, or (b) force auto-demote / mass-delete of
                # every flag (duplicate/outdated/merge_cleanup = 0). 0 → keep
                # the safe default.
                if k in ar and isinstance(ar[k], (int, float)) and ar[k] > 0:
                    defaults[k] = float(ar[k])
            logger.debug(
                "[Reflection] auto_resolve config: duplicate≥%.2f outdated≥%.2f "
                "cluster_min_interv=%ds merge_cleanup≥%.2f",
                defaults["duplicate"], defaults["outdated"],
                int(defaults["cluster_min_interval"]), defaults["merge_cleanup"],
            )
    except Exception:
        pass  # fail-open: hardcoded defaults survive any config error
    return defaults


def auto_resolve_eligible_flags() -> int:
    """自动降级高置信度 flags：涌现闭环，复用 resolve_flag(P3-⑩)。

    置信度分级（安全护栏：ephemeral-only 降权，绝不 volatile 删除）：
      ≥<duplicate> duplicate + source=skill → 自动 demote（技能描述已在 skill 系统可查）
      ≥<outdated> outdated → 自动 demote（过时信息降权，可恢复）
      ≥0.7 contradiction / scope_creep → 仅写 flag，不自动处理

    阈值从 config.yaml memory.autoResolve 段读取，缺项回落硬编码默认值。

    Returns: 自动处理的 flag 数量。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    thresholds = _load_auto_resolve_config()
    dup_threshold = thresholds["duplicate"]
    out_threshold = thresholds["outdated"]

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    # P2-11 fix: 先收集 eligible ids，关闭外层连接后再逐个 resolve
    # 避免嵌套连接写同库的 database is locked 风险
    conn = sqlite3.connect(db_path)
    try:
        eligible = conn.execute(
            """SELECT id, memory_id, flag_type, confidence
               FROM memory_flags
               WHERE status = 'open'
                 AND (flag_type = 'duplicate' AND confidence >= ?
                      OR flag_type = 'outdated' AND confidence >= ?)""",
            (dup_threshold, out_threshold),
        ).fetchall()
    finally:
        conn.close()

    resolved_count = 0
    for flag_id, memory_id, flag_type, confidence in eligible:
        # 对 skill-source 的 duplicate，确认源是 skill 才自动 demote
        # 记忆已删 = 确认冗余（orphan），直接 resolve 为 demote
        if flag_type == "duplicate" and confidence >= dup_threshold:
            try:
                _mid = int(memory_id)
            except (TypeError, ValueError):
                _mid = memory_id
            # 独立连接检查 source
            _conn2 = sqlite3.connect(db_path)
            try:
                src_row = _conn2.execute(
                    "SELECT source FROM memories WHERE id = ?", (_mid,)
                ).fetchone()
            finally:
                _conn2.close()
            # 记忆已不存在 → 确认冗余（orphan），直接 resolve
            if not src_row:
                ok = resolve_flag(flag_id, "demote")
                if ok:
                    resolved_count += 1
                    logger.info(
                        "[Reflection] auto_resolve: flag=%s duplicate+orphan → demote",
                        flag_id,
                    )
            # source=skill → 技能描述已在 skill 系统可查，安全降权
            elif src_row[0] == "skill":
                ok = resolve_flag(flag_id, "demote")
                if ok:
                    resolved_count += 1
                    logger.info(
                        "[Reflection] auto_resolve: flag=%s duplicate+skill → demote",
                        flag_id,
                    )

        elif flag_type == "outdated" and confidence >= out_threshold:
            ok = resolve_flag(flag_id, "demote")
            if ok:
                resolved_count += 1
                logger.info(
                    "[Reflection] auto_resolve: flag=%s outdated → demote",
                    flag_id,
                )

    if resolved_count > 0:
        logger.info("[Reflection] auto_resolve: %d flags auto-resolved", resolved_count)
    return resolved_count


# ── 存量清扫：清理已合并/重复的 skill 记忆 ──────────────────────────────

def cleanup_merged_skill_memories(db_path: str | None = None) -> int:
    """一次性清扫：删除已 resolved-merge 和 open-duplicate 指向的 skill 记忆。

    覆盖两类存量：
    1. resolved + resolution='merge' 的 flag 指向的 skill memory（87 条历史空转）
    2. open + flag_type='duplicate' + confidence≥<cleanup> 的 flag 指向的 skill memory

    只删 source='skill' 的行（安全护栏：非 skill 记忆不动）。
    幂等：重复调用零效果（已删的行不会再匹配）。

    merge_cleanup 阈值从 config.yaml memory.autoResolve.merge_cleanup 读取，
    缺省回落 0.7。

    Args:
        db_path: 可选，记忆库路径。None 时自动取 _get_index_db()。

    Returns: 删除的 memory 行数。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    thresholds = _load_auto_resolve_config()
    merge_cleanup_conf = thresholds["merge_cleanup"]

    if db_path is None:
        db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 收集所有待清理的 memory_id
        ids_to_delete = set()
        # 1. resolved-merge
        for row in conn.execute(
            "SELECT memory_id FROM memory_flags "
            "WHERE status='resolved' AND resolution='merge' AND memory_id IS NOT NULL"
        ).fetchall():
            ids_to_delete.add(row[0])
        # 2. open-duplicate conf>=merge_cleanup threshold (from config, default 0.7)
        merge_threshold = merge_cleanup_conf
        for row in conn.execute(
            "SELECT memory_id FROM memory_flags "
            "WHERE status='open' AND flag_type='duplicate' AND confidence >= ? "
            "AND memory_id IS NOT NULL",
            (merge_threshold,),
        ).fetchall():
            ids_to_delete.add(row[0])

        if not ids_to_delete:
            return 0

        # 只删 source='skill' 的（安全护栏）
        placeholders = ",".join("?" * len(ids_to_delete))
        deleted = conn.execute(
            f"DELETE FROM memories WHERE id IN ({placeholders}) AND source = 'skill'",
            list(ids_to_delete),
        ).rowcount
        conn.commit()
        if deleted > 0:
            logger.info(
                "[Reflection] cleanup_merged_skill_memories: deleted %d skill duplicates "
                "(from %d flagged ids)",
                deleted, len(ids_to_delete),
            )
        return deleted
    except Exception:
        logger.warning("[Reflection] cleanup_merged_skill_memories failed", exc_info=True)
        return 0
    finally:
        conn.close()
