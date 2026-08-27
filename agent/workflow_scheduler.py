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

import asyncio
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    runtime_checkable,
)

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


# ─────────────────────────────────────────────────────────────────────────────
# G1b 增量 (a)：WorkflowScheduler —— 串行版执行器（纯依赖门控 + 顺序执行）
#
# 设计边界（见 A2 设计稿 §0.7「并发安全边界」）：
#   ① 只读共享态：_make_context 只给步骤「快照式只读引用」，协程不得改 plan 结构。
#   ② 步骤私有态：每步的 StepExecResult 由该步协程独占写，兄弟协程不可见。
#   ③ 需串行化/合并（= LangGraph reducer）：
#        - plan 状态写回：self._state_lock 包裹「改 todo_states + backend.save」（reducer #1）
#        - 结果聚合：self._results_lock 包裹 append（reducer #2）
#        - 文件写入：file_lock(path) 按目标文件串行锁（reducer #3，并发切片启用）
#        - LLM/工具限流：self._sem = asyncio.Semaphore(max_concurrency)
#
# 静态 DAG 铁律（用户 refinement 3）：本调度器**绝不**在执行中途调用 LLM 重新拆步骤。
# 计划一旦由 chat.py 解析确定即冻结；调度器只是「依赖门控 + 执行 + 状态回写」。
# 动态重规划留给后续迭代。
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StepExecResult:
    """单步执行结果（步骤私有态，边界②）。由 step_executor 返回。"""

    status: str = "completed"  # completed / failed
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@runtime_checkable
class PlanBackend(Protocol):
    """plan 状态的持久化后端（reducer #1 的落点）。

    活链路里由 session_plan_store 适配（见 G1b 增量 b：chat.py 接线）；
    单测里用内存 fake 注入，不 mock 调度器本身。
    """

    async def load(self, session_id: str) -> Tuple[dict, Dict[str, str]]:
        """返回 (plan_json, todo_states)。todo_states: step_id -> 状态。"""
        ...

    async def save(
        self, session_id: str, plan_json: dict, todo_states: Dict[str, str]
    ) -> None:
        """原子保存 plan 状态（调用方须已持 self._state_lock）。"""
        ...


# step_executor: 给定 (step, ctx) 跑该步工作，返回 StepExecResult；抛异常 = 该步 failed。
StepExecFunc = Callable[[dict, dict], Awaitable[Optional[StepExecResult]]]


@dataclass
class WorkflowResult:
    session_id: str
    deadlocked: bool = False
    exec_order: List[str] = field(default_factory=list)
    results: List[Dict[str, Any]] = field(default_factory=list)


class WorkflowScheduler:
    """G1b 执行器（增量 a：串行；concurrent=True 时同层 steps 经 asyncio.gather 并发）。

    纯依赖门控 + 顺序/并发执行 + 状态回写。不含 LLM 调用、不含动态重规划。
    step 的实际工作由注入的 step_executor 完成（活链路里由 chat.py 接真执行体，
    见增量 b/c）。
    """

    def __init__(
        self,
        backend: PlanBackend,
        step_executor: StepExecFunc,
        max_concurrency: int = 4,
    ) -> None:
        self._backend = backend
        self._executor = step_executor
        self._max_concurrency = max_concurrency
        # 边界③ reducer 原语
        self._state_lock = asyncio.Lock()      # plan 状态写回互斥（reducer #1）
        self._results_lock = asyncio.Lock()    # 结果聚合互斥（reducer #2）
        self._sem = asyncio.Semaphore(max_concurrency)  # LLM/工具限流
        self._file_locks: Dict[str, asyncio.Lock] = {}   # 按路径文件锁（reducer #3）
        self._file_locks_guard = asyncio.Lock()
        self._results: List[Dict[str, Any]] = []

    def file_lock(self, path: str) -> asyncio.Lock:
        """边界③ #3：按目标文件路径的串行锁。同文件串行、异文件并发。

        供 step_executor 写文件时使用；增量 a 单测不触发，并发切片启用。
        """
        if path not in self._file_locks:
            self._file_locks[path] = asyncio.Lock()
        return self._file_locks[path]

    async def execute(
        self, session_id: str, concurrent: bool = False
    ) -> WorkflowResult:
        plan_json, todo_states = await self._backend.load(session_id)
        order: List[str] = []
        while True:
            ready = compute_ready_steps(plan_json, todo_states)
            if not ready:
                if is_plan_deadlocked(plan_json, todo_states):
                    # G2 地基：上游 failed/skipped → 下游标 skipped（静态传播，不重规划）
                    self._apply_deadlock_skip(plan_json, todo_states)
                    await self._persist(session_id, plan_json, todo_states)
                    return WorkflowResult(
                        session_id, deadlocked=True, exec_order=order, results=list(self._results)
                    )
                # 无就绪且不卡死 → 全完成或不一致态，退出
                break
            if concurrent:
                await asyncio.gather(
                    *[self._run_step(session_id, plan_json, todo_states, sid, order) for sid in ready]
                )
            else:
                for sid in ready:
                    await self._run_step(session_id, plan_json, todo_states, sid, order)
        return WorkflowResult(
            session_id, deadlocked=False, exec_order=order, results=list(self._results)
        )

    async def _run_step(
        self,
        session_id: str,
        plan_json: dict,
        todo_states: Dict[str, str],
        step_id: str,
        order: List[str],
    ) -> None:
        # reducer #1：标记 running + 记录执行顺序 + 落盘，全程持锁（原子）
        async with self._state_lock:
            todo_states[step_id] = "running"
            order.append(step_id)
            await self._persist(session_id, plan_json, todo_states)
        try:
            step = next(s for s in plan_json["steps"] if s["id"] == step_id)
            ctx = self._make_context(plan_json, step)  # 边界①只读快照
            async with self._sem:                        # 边界③ LLM/工具限流
                result = await self._executor(step, ctx)
            status = result.status if result else "completed"
            outputs = (result.outputs if result else {}) or {}
            # reducer #1：写结果状态 + 合并 outputs + 落盘，持锁
            async with self._state_lock:
                todo_states[step_id] = status
                if outputs:
                    step.setdefault("outputs", {}).update(outputs)
                await self._persist(session_id, plan_json, todo_states)
            # reducer #2：结果聚合
            async with self._results_lock:
                self._results.append(
                    {"step_id": step_id, "status": status, "outputs": outputs}
                )
        except Exception as e:  # 单步失败隔离：不击垮屏障
            async with self._state_lock:
                todo_states[step_id] = "failed"
                await self._persist(session_id, plan_json, todo_states)
            async with self._results_lock:
                self._results.append(
                    {"step_id": step_id, "status": "failed", "error": str(e)}
                )

    async def _persist(
        self, session_id: str, plan_json: dict, todo_states: Dict[str, str]
    ) -> None:
        """reducer #1 落点。调用方须已持 self._state_lock（不重复加锁，避免重入死锁）。"""
        await self._backend.save(session_id, plan_json, todo_states)

    def _make_context(self, plan_json: dict, step: dict) -> dict:
        """边界①：只读共享态快照。返回 plan 结构与本步 goal；协程不得改 plan_json。"""
        return {
            "step": step,
            "plan_readonly": plan_json,
            "dependencies": step.get("dependencies") or [],
        }

    def _apply_deadlock_skip(
        self, plan_json: dict, todo_states: Dict[str, str]
    ) -> None:
        """G2 地基：把依赖链上存在 failed/skipped/cancelled/interrupted 的步骤标 skipped。

        静态传播（不重规划）。幽灵依赖视为满足（与 compute_ready_steps 一致）。
        """
        by_id = _steps_by_id(plan_json)
        changed = True
        while changed:
            changed = False
            for sid, step in by_id.items():
                st = todo_states.get(sid) or step.get("status", "pending")
                if st in ("completed", "skipped", "cancelled", "interrupted"):
                    continue
                for dep in (step.get("dependencies") or []):
                    dstep = by_id.get(dep)
                    if dstep is None:
                        continue  # 幽灵依赖放行
                    dst = todo_states.get(dep) or dstep.get("status", "pending")
                    if dst in ("failed", "skipped", "cancelled", "interrupted"):
                        todo_states[sid] = "skipped"
                        changed = True
                        break
