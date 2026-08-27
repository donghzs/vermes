"""工作流运行时：可被 chat / cron / webhook 复用的步骤执行体工厂（G6）。

提炼自 ``vermes_cli/blueprints/chat.py`` 的 ``_build_workflow_step_prompt`` /
``_make_step_agent`` / ``_make_workflow_step_executor``，使其脱离 blueprints 上下文，
供触发器（cron / webhook）在没有「父会话 history」的场景下复用同一套
「每步隔离 AIAgent + 静态 DAG」执行范式。

单一事实来源：chat.py 的本地同名函数已改为从此模块导入（别名不变）。

边界（与设计稿 §0.9 一致）：
  ① 只读共享态：step executor 只给步骤「快照式只读引用」，不得改 plan 结构。
  ② 步骤私有态：每步用唯一 ``session_id`` 构造隔离 AIAgent，互不覆盖。
  ③ 单步失败隔离：executor 抛异常 → 返回 failed，不击垮屏障。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from .workflow_scheduler import StepExecResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 步骤指令构造（B1：单步执行，不重规划）
# ─────────────────────────────────────────────────────────────────────────────

def build_workflow_step_prompt(step: dict) -> str:
    """B1 指令：限定 LLM 只跑这一步，不重新规划、不拆新步骤。

    G4 数据流：若本步有上游注入的 ``inputs``（来自已完成依赖步的 outputs），
    渲染为「已知前置产物」上下文，让 LLM 直接消费而非自行在上下文翻找。
    """
    parts = [
        "你正在执行一个已确定的多步计划中的【单一步骤】。\n",
        f"步骤标题：{step.get('title', '')}\n",
        f"步骤目标：{step.get('description', '')}\n",
        f"交付物：{step.get('deliverable', '')}\n",
        f"完成标准：{step.get('done_when', '')}\n",
    ]
    # G4：已知前置产物（上游步骤 outputs 已聚合到 step['inputs']，§0.12.2）
    inputs = step.get("inputs") or {}
    if inputs:
        parts.append(
            "\n【已知前置产物】以下是本步骤依赖的上游步骤产出，可直接使用，无需重新生成：\n"
        )
        for dep_id, dep_out in inputs.items():
            summary = dep_out.get("summary")
            if summary:
                parts.append(f"- 来自步骤「{dep_id}」：{summary}\n")
            # 截断 artifacts 防 context 爆量（§0.12.3），最多 3 条
            for art in (dep_out.get("artifacts") or [])[:3]:
                parts.append(f"  - 产物：{art}\n")
    parts.append(
        "要求：只完成这一个步骤并调用必要的工具，完成后停止。"
        "不要重新规划、不要拆解新的步骤、不要修改其他步骤。\n"
    )
    return "".join(parts)


def make_step_agent(parent_agent, step: dict, parent_session_id: str):
    """每步隔离 AIAgent 工厂（§0.9.2 核心改动）。

    约束② 步骤私有态：每步用唯一 ``session_id``（``{parent}__wf_{step_id}``）构造独立实例，
    各自 session DB / 轨迹文件互不覆盖。构造参数 + 8 个 SSE 回调 + 运行期属性全部从父 agent
    **原样**读取（chat.py:1176 真实构造口径，§0.9.0），保证 step agent 与父会话行为一致。
    约束① 只读共享态（history deepcopy）由 executor 负责，本工厂不碰共享态。
    """
    from run_agent import AIAgent

    kwargs = {
        "base_url": getattr(parent_agent, "base_url", None),
        "api_key": getattr(parent_agent, "api_key", None),
        "provider": getattr(parent_agent, "provider", None),
        "model": getattr(parent_agent, "model", None),
        "max_iterations": getattr(parent_agent, "max_iterations", 30),
        "quiet_mode": True,
        "verbose_logging": False,
        "platform": getattr(parent_agent, "platform", "web"),
        "enabled_toolsets": getattr(parent_agent, "enabled_toolsets", None),
        "disabled_toolsets": getattr(parent_agent, "disabled_toolsets", None),
        "ephemeral_system_prompt": getattr(parent_agent, "ephemeral_system_prompt", None),
        "reasoning_config": getattr(parent_agent, "reasoning_config", None),
        "session_id": f"{parent_session_id}__wf_{step['id']}",
        "parent_session_id": parent_session_id,
    }
    # 约束④ SSE 回调透传（流式/工具/计划/思考）——前端逐步进度仍可见（§0.9.4）。
    for _cb in (
        "status_callback",
        "plan_event_callback",
        "stream_delta_callback",
        "tool_progress_callback",
        "tool_start_callback",
        "tool_complete_callback",
        "thinking_callback",
        "reasoning_callback",
    ):
        _v = getattr(parent_agent, _cb, None)
        if _v is not None:
            kwargs[_cb] = _v
    step_agent = AIAgent(**kwargs)
    # 运行期属性透传（联网提示重组 / 交互模式硬约束，chat.py:1215-1217）
    _mode = getattr(parent_agent, "interaction_mode", None)
    if _mode is not None:
        step_agent.interaction_mode = _mode
    _evo = getattr(parent_agent, "_evo_base_prompt", None)
    if _evo:
        step_agent._evo_base_prompt = _evo
    return step_agent


# ─────────────────────────────────────────────────────────────────────────────
# 步骤执行体工厂（B1）：每步构造隔离 AIAgent 跑单步
# ─────────────────────────────────────────────────────────────────────────────

def make_sequential_executor(
    agent,
    parent_session_id: str,
    user_message: Optional[str] = None,
    conversation_history: Optional[list] = None,
    step_pool: Optional[concurrent.futures.Executor] = None,
) -> Callable[[dict, dict], Any]:
    """构造 B1 step_executor：每步在独立线程内构造**隔离** AIAgent（§0.9.2）。

    与 chat.py ``_make_workflow_step_executor`` 一致：每步由 ``make_step_agent``
    构造全新实例（唯一 session_id + deepcopy 只读 history），并发屏障内多个
    ``run_conversation`` 各自跑在独立线程 / 独立实例，无共享可变状态。

    - 无 history 时（cron / webhook）：seed=None，每步从空会话跑（工作流自身就是任务）。
    - 有 history 时（chat 续跑/转工作流）：deepcopy 只读快照。

    返回 async (step, ctx) -> StepExecResult，匹配 workflow_scheduler.StepExecFunc。
    """
    async def _exec(step: dict, ctx: dict):
        loop = asyncio.get_running_loop()
        prompt = build_workflow_step_prompt(step)

        def _run():
            step_agent = make_step_agent(agent, step, parent_session_id)
            # 约束① 只读共享态：严格 deepcopy，step 不得改父 history、兄弟步互不可见。
            seed = conversation_history[:-1] if (conversation_history and len(conversation_history) > 1) else None
            history = copy.deepcopy(seed) if seed is not None else None
            try:
                res = step_agent.run_conversation(
                    user_message=prompt,
                    conversation_history=history,
                    stream_callback=None,
                )
            except Exception as e:  # 单步失败隔离（边界：不击垮屏障）
                return StepExecResult(status="failed", error=str(e))
            final = (res or {}).get("final_response", "") or ""
            # G4：把本步产物摘要写入 outputs，供下游步骤 inputs 消费（§0.12.2 第4点）。
            # summary = 该步最终回复（B1 模式下「步骤产物」即其自然语言交付）。
            return StepExecResult(
                status="completed",
                outputs={"final_response": final, "summary": final},
            )

        try:
            # 丢独立线程池：run_conversation 同步阻塞，且须脱离调度器事件循环线程
            return await loop.run_in_executor(step_pool, _run)
        except Exception as e:  # 单步失败隔离（边界：不击垮屏障）
            return StepExecResult(status="failed", error=str(e))

    return _exec


# ─────────────────────────────────────────────────────────────────────────────
# 线程池（跑步骤级 run_conversation，避免与调度器事件循环同线程死锁）
# ─────────────────────────────────────────────────────────────────────────────

_wf_step_pool: Optional[concurrent.futures.ThreadPoolExecutor] = None
_wf_step_pool_lock = threading.Lock()


def get_step_pool() -> concurrent.futures.ThreadPoolExecutor:
    """模块级步骤线程池（与 chat.py 的 ``_get_wf_step_pool`` 同构，但独立于 blueprints）。"""
    global _wf_step_pool
    if _wf_step_pool is None or getattr(_wf_step_pool, "_shutdown", False):
        with _wf_step_pool_lock:
            if _wf_step_pool is None or getattr(_wf_step_pool, "_shutdown", False):
                _wf_step_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=4, thread_name_prefix="vermes-wf-trigger"
                )
    return _wf_step_pool


# ─────────────────────────────────────────────────────────────────────────────
# 触发器入口：同步跑一个已命名模板（cron / webhook 从阻塞/线程上下文调用）
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_workflow_result(result) -> Dict[str, Any]:
    """把 WorkflowResult 压成可投递摘要。"""
    lines: List[str] = []
    for r in (result.results or []):
        _sid = r.get("step_id", "?")
        _st = r.get("status", "?")
        lines.append(f"- 步骤 {_sid}: {_st}")
    if result.deadlocked:
        lines.append("- ⚠️ 检测到依赖死锁，下游未满足步骤已标记为 skipped")
    summary = "\n".join(lines) if lines else "(无步骤产出)"
    return {
        "final_response": summary,
        "summary": summary,
        "deadlocked": bool(result.deadlocked),
        "exec_order": list(result.exec_order or []),
    }


def run_workflow_template_sync(
    name: str,
    parent_agent,
    parent_session_id: str,
    user_message: Optional[str] = None,
    version: Optional[int] = None,
    step_pool: Optional[concurrent.futures.Executor] = None,
    concurrent: bool = False,
) -> Dict[str, Any]:
    """同步运行一个工作流模板（供 cron / webhook 触发器调用）。

    流程：实例化模板到 parent_session_id 的 plan → 用 parent_agent 构造每步隔离
    执行体 → 经既有 WorkflowScheduler 依赖门控执行（G1b/G2/G4 全复用）。
    内部 ``asyncio.run`` 新建事件循环；调用方须不在已有事件循环内（触发器线程满足）。
    """
    from .workflow_templates import run_template_async

    executor = make_sequential_executor(
        parent_agent,
        parent_session_id,
        user_message=user_message,
        step_pool=step_pool or get_step_pool(),
    )
    result = asyncio.run(
        run_template_async(
            name, parent_session_id, executor, concurrent=concurrent, version=version
        )
    )
    return _summarize_workflow_result(result)


# ─────────────────────────────────────────────────────────────────────────────
# 触发器父 agent 构造（webhook 无现成 agent，需自建一个最小 AIAgent）
# ─────────────────────────────────────────────────────────────────────────────

def build_agent(
    session_id: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    reasoning_config: Optional[Dict[str, Any]] = None,
    platform: str = "trigger",
):
    """构造用于跑工作流步骤的最小 AIAgent（供 webhook 等无现成 agent 的触发器）。

    凭证优先用显式传入；缺省时经 ``resolve_runtime_provider`` 解析当前 provider。
    仅携带工作流执行必需字段，不安全相关字段沿用 AIAgent 默认值。
    """
    from run_agent import AIAgent

    if api_key is None or provider is None:
        try:
            from vermes_cli.runtime_provider import resolve_runtime_provider
            rt = resolve_runtime_provider(requested=provider, explicit_base_url=base_url)
            provider = provider or rt.get("provider")
            api_key = api_key or rt.get("api_key")
            base_url = base_url or rt.get("base_url")
            model = model or rt.get("model")
        except Exception as e:
            logger.warning("build_agent: provider resolution failed (%s); using explicit/empty creds", e)

    return AIAgent(
        model=model or "",
        api_key=api_key,
        base_url=base_url,
        provider=provider,
        max_iterations=30,
        quiet_mode=True,
        verbose_logging=False,
        platform=platform,
        session_id=session_id,
        reasoning_config=reasoning_config,
        load_soul_identity=True,
        skip_memory=True,
    )
