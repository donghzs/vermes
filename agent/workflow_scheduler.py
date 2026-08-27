"""Workflow DAG 调度内核（纯函数，可单测、零 mock）。

G1a 基础切片 —— 拓扑就绪判定引擎。

家底核实结论（见 A2 设计稿 §0.5 / chat.py:1391-1412）：
Vermes 活链路里计划是**被动追踪覆盖层**——步骤状态由解析 LLM 流式文本
得到（tools.todo_tool.compute_active_step_ordinal + plan_step_update），
**没有执行器、没有调度循环**。本模块是 G1 并发调度器的「正确性地基」：
给定 plan（含 M1a 已贯通的 dependencies）+ 当前 step 状态，算出
「哪些步骤就绪可并发拉起 / 拓扑层级 / 是否卡死」。不含任何执行逻辑，
并发执行后端（delegate/swarm 或子 agent 协程）在后续切片接入。

状态词表与活链路一致（chat.py / session_plan_store）：
  pending / in_progress / completed / cancelled / interrupted
（G2 将追加 skipped，本引擎对 deps 的「满足」判定仅认 completed。）
"""

from __future__ import annotations

from typing import Dict, List, Optional

# deps 视为「已满足」的状态：仅 completed 解锁下游。
_SATISFIED = ("completed",)
# 终态（不再参与调度，也不阻塞——被跳过/取消的步骤不挡下游，
# 交由 G2 的 skip 传播负责；本引擎仅做就绪判定，不擅自改图）。
_TERMINAL = ("completed", "cancelled", "skipped", "interrupted")


def _effective_status(
    step: dict,
    todo_states: Optional[Dict[str, str]],
) -> str:
    """步骤的有效状态：优先用实时 todo_states（活链路权威来源），否则 step["status"]。"""
    if todo_states:
        s = todo_states.get(step.get("id"))
        if s:
            return s
    return step.get("status", "pending")


def _steps_by_id(plan: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for s in plan.get("steps", []) or []:
        sid = s.get("id")
        if sid:
            out[sid] = s
    return out


def compute_ready_steps(plan: dict, todo_states: Optional[Dict[str, str]] = None) -> List[str]:
    """返回当前所有依赖已满足且自身仍待执行的步骤 id 列表（同层可并发）。

    判定：
      - 自身有效状态 ∈ {pending} 才考虑（in_progress/completed/... 不算就绪）
      - 每个依赖 dep 的有效状态 ∈ _SATISFIED（默认仅 completed）才解锁
      - 依赖指向不存在的步骤 id → 视为已满足（不阻断于幽灵依赖），仅静默放行

    反向验证：若把 deps 检查去掉（恒返回所有 pending），则依赖未完成的下游会被
    误判就绪 → 调用方测试（构造 s2 依赖 s1、s1 未完成）必红。
    """
    by_id = _steps_by_id(plan)
    ready: List[str] = []
    for sid, step in by_id.items():
        if _effective_status(step, todo_states) != "pending":
            continue
        deps = step.get("dependencies") or []
        if not isinstance(deps, list):
            deps = []
        _all_done = True
        for dep in deps:
            _dep_step = by_id.get(dep)
            if _dep_step is None:
                # 幽灵依赖：放行（不阻断）
                continue
            if _effective_status(_dep_step, todo_states) not in _SATISFIED:
                _all_done = False
                break
        if _all_done:
            ready.append(sid)
    return ready


def topological_levels(plan: dict) -> List[List[str]]:
    """按 dependencies 把 steps 切成拓扑层级（同层可并发）。

    返回 List[List[step_id]]，index 0 为最底层（无依赖）。
    存在环 → 抛 ValueError（调用方应 fail-open 捕获，不让 chat 主循环崩）。

    反向验证：构造环 s1→s2→s1，调用必抛 ValueError；去掉环检测则静默返回错误分层。
    """
    by_id = _steps_by_id(plan)
    # 入度 = 未满足的依赖数（仅统计存在的 dep）
    indeg: Dict[str, int] = {sid: 0 for sid in by_id}
    adj: Dict[str, List[str]] = {sid: [] for sid in by_id}
    for sid, step in by_id.items():
        deps = step.get("dependencies") or []
        if not isinstance(deps, list):
            deps = []
        for dep in deps:
            if dep in by_id and dep != sid:
                indeg[sid] += 1
                adj[dep].append(sid)
    levels: List[List[str]] = []
    remaining = dict(indeg)
    while remaining:
        # 本层 = 入度 0 的节点
        layer = [sid for sid, d in remaining.items() if d == 0]
        if not layer:
            raise ValueError(
                f"plan has dependency cycle among steps: {sorted(remaining.keys())}"
            )
        levels.append(sorted(layer))
        _next: Dict[str, int] = {}
        for sid in layer:
            for nxt in adj[sid]:
                remaining[nxt] -= 1
            del remaining[sid]
        _next = remaining
        remaining = _next
    return levels


def is_plan_deadlocked(plan: dict, todo_states: Optional[Dict[str, str]] = None) -> bool:
    """卡死检测（G2 死锁洞察的地基）：存在未终态步骤，但当前无就绪步骤。

    即：还有活儿没干完，却没有任何一步能开始（依赖永远不满足 / 上游卡住）。
    注意：仅依赖图层面判定，不区分上游是 failed 还是 in_progress（后者由
    超时/interrupted 逻辑在 chat.py 处理）。
    """
    by_id = _steps_by_id(plan)
    has_unfinished = False
    for sid, step in by_id.items():
        st = _effective_status(step, todo_states)
        if st not in _TERMINAL:
            has_unfinished = True
            break
    if not has_unfinished:
        return False
    return len(compute_ready_steps(plan, todo_states)) == 0


def steps_unlocked_by(plan: dict, completed_step_id: str, todo_states: Optional[Dict[str, str]] = None) -> List[str]:
    """给定某步刚 completed，返回由此新解锁（变就绪）的步骤 id 列表。

    供增量调度器在「一步完成」事件后只拉起真正新就绪的步骤，而非每轮全量扫描。
    """
    # 完成「前」：该步视为尚未完成（用原始 todo_states，不标记 completed）
    _before = dict(todo_states or {})
    ready_before = set(compute_ready_steps(plan, _before))
    # 完成「后」：该步已 completed
    _after = dict(todo_states or {})
    _after[completed_step_id] = "completed"
    ready_after = set(compute_ready_steps(plan, _after))
    return sorted(ready_after - ready_before)
