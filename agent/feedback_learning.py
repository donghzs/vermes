"""agent/feedback_learning.py — 显式用户反馈 → raw_events（H4.4 监督式自学习）

把用户/agent 的显式反馈（点赞/点踩、纠错）写入自进化系统的唯一真实源
raw_events，与工具成败事件走同一条「聚类 → 涌现洞察 → 注入系统提示」管线。
补「仅靠成败 + 审批」的监督式信号空白。

设计：
  - 纯写点，fail-open（写入失败/进化未激活都不阻塞工具或对话）。
  - 复用 agent.raw_event.record_raw_event（含 embedding 写入，可被语义召回）。
  - 反馈事件 tool_name 形如 feedback_thumbs_up / feedback_thumbs_down /
    feedback_correction；success 标记语义：点赞=1，点踩/纠错=0。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_VALID_KINDS = ("thumbs_up", "thumbs_down", "correction")


def record_user_feedback(
    kind: str,
    target: str,
    comment: str = "",
    *,
    agent: Any = None,
) -> bool:
    """Write an explicit user-feedback event into raw_events.

    Args:
        kind:    ``"thumbs_up"`` / ``"thumbs_down"`` / ``"correction"``.
        target:  what the feedback is about (tool name / message snippet / fact).
        comment: free-text elaboration (the corrected text for ``correction``).
        agent:   optional agent for ``session_id`` / ``turn_number``.

    Returns:
        ``True`` if the event was written, ``False`` if evolution is inactive
        or the write failed. Callers must NOT depend on success (fail-open).
    """
    if kind not in _VALID_KINDS:
        logger.warning("record_user_feedback: unknown kind %r", kind)
        return False
    try:
        from agent.evolution_manager import is_evolution_active

        if not is_evolution_active():
            return False
    except Exception:
        # If we cannot determine activity, still attempt the write (fail-open).
        pass

    # thumbs_down / correction are negative events → success = 0.
    is_error = kind in ("thumbs_down", "correction")
    session_id = getattr(agent, "session_id", "") if agent else ""
    turn_number = getattr(agent, "turn_counter", 0) if agent else 0
    tool_name = f"feedback_{kind}"
    args_preview = json.dumps(
        {"kind": kind, "target": target}, ensure_ascii=False
    )[:200]
    result_preview = (comment or target)[:500]

    try:
        from agent.raw_event import record_raw_event

        record_raw_event(
            tool_name=tool_name,
            tool_args={"kind": kind, "target": target, "comment": comment},
            result=result_preview,
            is_error=is_error,
            duration=0.0,
            session_id=session_id,
            turn_number=turn_number,
        )
        logger.info("[H4.4] user feedback recorded: %s on %r", kind, target)
        return True
    except Exception:
        logger.debug("record_user_feedback write failed", exc_info=True)
        return False
