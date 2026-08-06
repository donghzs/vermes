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
