"""P0-A 通用 Outcome Verifier — 按名查 verify_fn 并执行。

设计原则：
  - fail-open：验证器自身出错不影响工具执行结果（只记日志）
  - 独立闸门：用自己的"按名查 verify_fn"判定，不依赖 _FILE_MUTATING_TOOLS 硬编码
  - 验证器自身也要被验证：verify_fn 走外证回读（DB 状态/SELECT），不自证

R1: is_error 不可信 —— handler 返回 ❌ 字符串而非抛错，is_error=False 即便写库真失败
    → verify_fn 必须验"功能结果"（DB 状态/外证回读），不能只看 is_error
R2: 独立闸门 —— 不依赖 _FILE_MUTATING_TOOLS 硬编码集合，按名查 verify_fn
R3: 验证器自身也要被验证 —— verify_fn 必须走三重回读 + 照真实调用方形态写契约测试
R4: 廉价 fail-open —— verify_fn 超时/异常返回 (True, "verifier error: ...")
R5: function_name == 注册名 —— 用 assert 钉死，防止名称漂移

用法（tool_executor.py:558/1100）：
    from harness.outcome_verifier import verify_tool_outcome
    ok, reason = verify_tool_outcome(function_name, function_args, function_result, is_error)
    if not ok:
        # 记录到 raw_events，不阻断返回
        logger.warning("outcome verified FAIL: %s %s → %s", function_name, reason, function_result[:200])
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger("vermes.harness.outcome_verifier")


def verify_tool_outcome(
    function_name: str,
    function_args: dict,
    function_result: str,
    is_error: bool,
) -> Tuple[bool, str]:
    """按名查 verify_fn 并执行。fail-open。

    Returns:
        (True, "") — outcome verified (或无 verify_fn，默认信任)
        (True, "verifier error: ...") — verify_fn 自身出错，fail-open
        (False, reason) — outcome NOT verified
    """
    # R5: function_name == 注册名
    assert isinstance(function_name, str) and function_name, "function_name must be non-empty string"

    try:
        from tools.registry import registry
        entry = registry.get_entry(function_name)
        if entry is None:
            # 工具不在 registry（可能是 MCP 工具或未注册），默认信任
            return (True, "")
        verify_fn: Optional[Callable] = getattr(entry, "verify_fn", None)
        if verify_fn is None:
            # 无 verify_fn = 不验证（默认行为，向后兼容）
            return (True, "")
        # R1: 不信任 is_error，让 verify_fn 自己验功能结果
        # R4: 廉价 fail-open
        ok, reason = verify_fn(function_name, function_args, function_result, is_error)
        if not isinstance(ok, bool) or not isinstance(reason, str):
            logger.warning(
                "verify_fn for %s returned non-bool/str: (%r, %r); treating as pass",
                function_name, ok, reason,
            )
            return (True, f"verifier returned bad type: {type(ok).__name__}/{type(reason).__name__}")
        return (ok, reason)
    except Exception as e:
        # R4: fail-open — 验证器自身出错不阻断工具结果
        logger.warning("verify_tool_outcome(%s) raised: %s", function_name, e)
        return (True, f"verifier error: {e}")


def record_tool_verify(
    agent: Any,
    function_name: str,
    ok: bool,
    reason: str,
) -> None:
    """P2 信号桥：把一次工具验证结果喂给任务级 Critic（只收集本回合）。

    - fail-open：任何异常只记 debug 日志，绝不阻断工具执行或 agent loop。
    - 写入 ``agent._recent_tool_verify``（每回合由 conversation_loop 重置为空
      列表，不跨回合累积）。CriticJudge 只读本回合信号（跨回合排名是 P4 的活）。
    - 列表上限保护：单回合工具调用数有限，但设 64 上限防异常膨胀。

    调用点：agent/tool_executor.py 并发路径(:567) 与串行路径(:1126) 的 verify
    钩子之后。
    """
    try:
        buf = getattr(agent, "_recent_tool_verify", None)
        if buf is None:
            buf = []
            try:
                agent._recent_tool_verify = buf
            except Exception:
                pass
        buf.append({
            "function_name": function_name,
            "ok": bool(ok),
            "reason": reason if isinstance(reason, str) else str(reason),
        })
        # 单回合上限保护（正常远不到，防异常膨胀）
        if len(buf) > 64:
            del buf[: len(buf) - 64]
    except Exception as e:  # noqa: BLE001 - 信号桥失败绝不阻断
        logger.debug("record_tool_verify(%s) failed: %s", function_name, e)


def compute_verified(
    vr_ok: bool,
    ov_ok: bool,
    file_landed: Optional[bool] = None,
) -> bool:
    """聚合已算出的验证 verdict，产出统一 ``verified`` 信号（纯函数，可测）。

    设计要点：
      - **不重调任何验证器**（无双重验证）。``vr_ok`` 来自 self_validator
        （tool_executor 已执行），``ov_ok`` 来自本模块 ``verify_tool_outcome``
        （tool_executor 已执行）。此处只做布尔聚合。
      - 写类工具（write_file/patch）额外要求结果证明确实落盘
        （``file_landed``，由调用方用 ``file_mutation_result_landed`` 计算后传入），
        否则"写回成功"是静默假成功。
      - fail-open：``file_landed`` 为 None（非写类工具或判定失败）时不强制为 False。

    反向验证（R5 解药）：test_verified_signal.py 覆盖 vr/ov/file_landed 各组合，
    且拷到本函数加入前必失败 → 证聚合逻辑真实生效。
    """
    verified = bool(vr_ok) and bool(ov_ok)
    if file_landed is not None:
        verified = verified and bool(file_landed)
    return verified
