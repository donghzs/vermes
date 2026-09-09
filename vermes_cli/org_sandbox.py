"""Org → Kanban 执行沙箱委派（Sprint D · ⑤ R3）。

把神魔堂（org 流水线）的 native/CLI 子任务执行**下沉**到蜂群
（kanban_swarm / kanban_db）已有的执行沙箱，而非就地 spawn。

设计原则（对齐任务书 §三/§四 硬约束）：
- **纯增量**：本模块不重建任何一侧；只复用 kanban_db.create_task 投递，
  复用 workspace / branch / heartbeat / claim / 超时 / 重试 全部既有机制。
- **ACP 不进沙箱**：异构 ACP agent 模型/运行时自持，只走原 acp 分支，
  dispatch_org_subtask_to_sandbox 对 transport=="acp" 直接 raise。
- **增强非主线 + 显式触发**：org 编排智能（拆解/审计/汇总）保留；
  只有调用方显式开启 use_sandbox 才委派，默认内联路径完全不动
  （不默认增加用户 token/经济负担）。
- **状态单向回填**：kanban task 是执行真相源；org 只订阅其 status/result，
  绝不回写 kanban 行 → 避免 org_tasks ↔ kanban tasks 双写竞争。

心跳节奏错配缓解：poll_sandbox_outcome 默认 interval(2s) ≤ 神魔堂
轮询周期(4s, BotRooms.vue loadOrgBoard)，org 不会比 UI 刷新更晚感知到
任务落地。
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Optional

from vermes_cli import kanban_db as kb


# --- org 子任务状态（kanban 终态 → org 子任务状态，严格子集） -------------
SANDBOX_ORG_DONE = "done"
SANDBOX_ORG_BLOCKED = "blocked"
SANDBOX_ORG_TIMEOUT = "timed_out"
SANDBOX_ORG_CRASHED = "crashed"
SANDBOX_ORG_GAVE_UP = "gave_up"
SANDBOX_ORG_STALE = "stale"

# 守护进程回收 / 蜂群自身定义的终态（与 kanban_db VALID_STATUSES 对齐）。
_TERMINAL_STATUSES = (
    "done",
    "blocked",
    "timed_out",
    "crashed",
    "gave_up",
    "stale",
    "archived",
)

# kanban status/outcome → org 子任务状态。
_STATUS_TO_ORG = {
    "done": SANDBOX_ORG_DONE,
    "completed": SANDBOX_ORG_DONE,
    "blocked": SANDBOX_ORG_BLOCKED,
    "timed_out": SANDBOX_ORG_TIMEOUT,
    "crashed": SANDBOX_ORG_CRASHED,
    "gave_up": SANDBOX_ORG_GAVE_UP,
    "stale": SANDBOX_ORG_STALE,
}


def _derive_idempotency_key(profile: object, instruction: str) -> str:
    """稳定的 per-sub-task 幂等键（instruction 用 md5，避免进程间 hash 随机化）。"""
    pid = profile.get("id") if isinstance(profile, dict) else str(profile)
    digest = hashlib.md5(instruction.encode("utf-8")).hexdigest()
    return f"org:{pid}:{digest}"


def dispatch_org_subtask_to_sandbox(
    conn,
    *,
    org_task_id: str,
    profile: object,
    instruction: str,
    transport: str,
    workspace_kind: str = "scratch",
    workspace_path: Optional[str] = None,
    branch_name: Optional[str] = None,
    max_runtime_seconds: Optional[int] = None,
    skills: Optional[list[str]] = None,
    board: Optional[str] = None,
) -> str:
    """把一个 native/CLI org 子任务投递到蜂群执行沙箱，返回 kanban task id。

    幂等：``org_task_id`` 作为 kanban ``idempotency_key``（前缀 ``org:``）。
    同一 org 子任务重复派发命中已存在 task，不新建重复沙箱 worker。

    transport=="acp" 直接抛 ValueError——调用方不得把 ACP agent 路由进沙箱。

    调用方负责开关连接（测试可传内存库）；本函数不自行开关连接。
    """
    if transport == "acp":
        raise ValueError("ACP agents must not enter the sandbox")
    assignee = profile.get("id") if isinstance(profile, dict) else str(profile)
    # branch_name 仅 worktree 态合法；其余态不传，避免 create_task 校验报错。
    effective_branch = branch_name if workspace_kind == "worktree" else None
    task_id = kb.create_task(
        conn,
        title=f"[神魔堂] {assignee} · {instruction[:60]}",
        body=instruction,
        assignee=assignee,
        created_by="shenmotang-org",
        workspace_kind=workspace_kind,
        workspace_path=workspace_path,
        branch_name=effective_branch,
        max_runtime_seconds=max_runtime_seconds,
        skills=skills,
        idempotency_key=f"org:{org_task_id}",
        board=board,
        # 不传 initial_status（默认 running）→ create_task 内部落成 ready，
        # 等待蜂群 dispatcher 领取；不传 "ready"（不在 VALID_INITIAL_STATUSES）。
    )
    return task_id


def map_sandbox_outcome_to_org(kanban_task) -> tuple[str, str]:
    """把 kanban task 的终态单向回填映射为 (org_status, output)。

    只消费沙箱状态，绝不回写 kanban 行（无双写竞争）。
    output 取自 task.result（worker 产出文本）；缺省回退到 status 枚举。
    """
    status = kanban_task.status
    output = (getattr(kanban_task, "result", None) or "").strip()
    if not output:
        output = status
    org_status = _STATUS_TO_ORG.get(status, SANDBOX_ORG_BLOCKED)
    return org_status, output


def poll_sandbox_outcome(
    conn,
    task_id: str,
    *,
    timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
    terminal: tuple[str, ...] = _TERMINAL_STATUSES,
) -> object:
    """有界轮询派发的沙箱任务直到终态。

    默认 interval(2s) ≤ 神魔堂轮询周期(4s)，org 不会比 UI 更晚感知落地。
    超时返回当前（可能非终态）task，由调用方按 blocked/stale 处理（fail-open，
    不影响其余并行子任务）。task 不存在返回 None。
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        task = kb.get_task(conn, task_id)
        if task is None:
            return None
        if task.status in terminal:
            return task
        time.sleep(max(0.1, interval_seconds))
    return kb.get_task(conn, task_id)


def run_org_subtask_in_sandbox(
    profile: object,
    instruction: str,
    transport: str,
    *,
    org_task_id: Optional[str] = None,
    workspace_kind: str = "scratch",
    max_runtime_seconds: Optional[int] = None,
    skills: Optional[list[str]] = None,
    poll_timeout_seconds: float = 300.0,
    interval_seconds: float = 2.0,
) -> str:
    """编排封装：开连接 → 投递 → 有界轮询 → 映射 → 关连接，返回 org 子任务产出。

    供 chat.py ``_org_runner_factory`` 在 opt-in 时调用。ACP 由调用方保证不进。
    失败/未完成返回带 ``[沙箱...]`` 前缀的标记串，org 据此记 blocked，
    不影响其余并行子任务（回滚独立性）。
    """
    if transport == "acp":
        raise ValueError("ACP agents must not enter the sandbox")
    key = org_task_id or _derive_idempotency_key(profile, instruction)
    conn = kb.connect()
    try:
        tid = dispatch_org_subtask_to_sandbox(
            conn,
            org_task_id=key,
            profile=profile,
            instruction=instruction,
            transport=transport,
            workspace_kind=workspace_kind,
            max_runtime_seconds=max_runtime_seconds,
            skills=skills,
        )
        task = poll_sandbox_outcome(
            conn, tid,
            timeout_seconds=poll_timeout_seconds,
            interval_seconds=interval_seconds,
        )
        if task is None:
            return "[沙箱执行失败] 任务不存在（可能被回收）"
        org_status, output = map_sandbox_outcome_to_org(task)
        if org_status == SANDBOX_ORG_DONE:
            return output or ""
        return f"[沙箱执行未完成: {org_status}] {output}"
    finally:
        conn.close()


def org_sandbox_enabled(_ctx: object) -> bool:
    """opt-in 判定（三路兜底，默认 False = 内联路径不动）。

    1) 运维显式触发：env VERMES_ORG_SANDBOX 真值；
    2) 未来 UI 写入：OrgContext.use_sandbox 属性；
    3) dict ctx 兼容：_ctx.get("use_sandbox")。
    """
    if os.environ.get("VERMES_ORG_SANDBOX"):
        return True
    if hasattr(_ctx, "use_sandbox") and getattr(_ctx, "use_sandbox"):
        return True
    if isinstance(_ctx, dict) and _ctx.get("use_sandbox"):
        return True
    return False
