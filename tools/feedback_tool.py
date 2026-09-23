"""tools/feedback_tool.py — 显式用户反馈工具（H4.4 监督式自学习）

暴露 ``thumbs`` / ``submit_correction`` 两个工具，让 agent 能把用户/自身的
显式反馈写入自进化系统。所有反馈经 agent.feedback_learning.record_user_feedback
落库到 raw_events，进入与工具成败事件相同的聚类→洞察→注入管线。

工具属 ``feedback`` toolset，始终可用（check_fn=None）。agent import 延迟到
函数体内，避免模块加载期循环依赖。
"""

import json
import logging
from typing import Any, Dict

from tools.registry import registry

logger = logging.getLogger(__name__)

THUMBS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "thumbs",
        "description": (
            "记录用户对最近一次回复或工具结果的显式反馈（点赞/点踩）。"
            "这是监督式学习信号，帮助系统从显式偏好中自我改进。"
            "feedback='up' 表示满意或正确，'down' 表示不满意或有误。"
            "可选 comment 给出原因。target 指明反馈对象（工具名/最近回复片段/主题）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "feedback": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "up=正面反馈，down=负面反馈",
                },
                "target": {
                    "type": "string",
                    "description": "反馈对象：工具名、最近回复的片段或主题",
                },
                "comment": {
                    "type": "string",
                    "description": "可选：反馈原因或补充说明",
                },
            },
            "required": ["feedback", "target"],
        },
    },
}

CORRECTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "submit_correction",
        "description": (
            "当用户纠正了某个事实、意图或工具结果时，记录这次纠错。"
            "correction 是用户认为正确的内容；target 是被纠正的对象。"
            "纠错是高质量监督信号，直接改善后续相关任务的准确性。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "被纠正的对象：工具名、事实陈述或错误结论",
                },
                "correction": {
                    "type": "string",
                    "description": "用户认为正确的内容",
                },
            },
            "required": ["target", "correction"],
        },
    },
}


def _resolve_thumbs_kind(raw: str) -> str:
    """Normalize the thumbs direction argument.

    Accepts the documented ``"up"``/``"down"`` values plus legacy/UI
    aliases (``like``/``dislike``, ``like``/``dislike``, numeric 1/0)
    so caller-side schema drift degrades gracefully instead of
    silently recording a meaningless thumbs_down with an empty target.
    """
    v = (raw or "").strip().lower()
    if v in ("up", "like", "1", "positive"):
        return "thumbs_up"
    return "thumbs_down"


def _resolve_feedback_target(target: str, **kwargs: Any) -> str:
    """Fill in a meaningful target when the caller omitted one.

    Falls back to ctx (task_id/session_id) injected by the tool
    dispatcher so a down-vote with no explicit target still carries
    enough signal to be useful to the evolution pipeline.
    """
    if target:
        return target
    ctx = kwargs.get("ctx") or {}
    for key in ("task_id", "session_id"):
        val = ctx.get(key) if hasattr(ctx, "get") else None
        if val:
            return f"target=<{key}:{val}>"
    # Last resort: the dispatcher always passes task_id/session_id as
    # bare kwargs even when they are empty strings — at least echo the
    # session id so the event is not completely opaque.
    for key in ("session_id", "task_id"):
        val = kwargs.get(key)
        if val:
            return f"target=<{key}:{val}>"
    return "(unspecified)"


def _resolve_agent(kwargs: Any):
    """Extract the agent object from dispatcher kwargs, if present.

    ``model_tools.handle_function_call`` does not forward an ``agent``
    kwarg into ``registry.dispatch`` (it only builds ``ctx``), so this
    is typically ``None`` and ``record_user_feedback`` degrades to
    empty ``session_id``/``turn_number`` — acceptable for now, but we
    keep the lookup so a future dispatcher change picks it up for free.
    """
    return kwargs.get("agent")


def thumbs(feedback: str, target: str, comment: str = "", **kwargs: Any) -> str:
    """记录点赞/点踩反馈，落库到 raw_events。"""
    kind = _resolve_thumbs_kind(feedback)
    target = _resolve_feedback_target(target, **kwargs)
    try:
        from agent.feedback_learning import record_user_feedback

        agent = _resolve_agent(kwargs)
        ok = record_user_feedback(kind, target, comment, agent=agent)
        if ok:
            return json.dumps({"success": True, "feedback": kind, "target": target})
        return json.dumps(
            {
                "success": False,
                "error": "feedback recording skipped (evolution inactive or write failed)",
            }
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("thumbs tool error: %s", exc)
        return json.dumps({"success": False, "error": str(exc)})


def submit_correction(target: str, correction: str, **kwargs: Any) -> str:
    """记录用户纠错，落库到 raw_events。"""
    target = _resolve_feedback_target(target, **kwargs)
    try:
        from agent.feedback_learning import record_user_feedback

        agent = _resolve_agent(kwargs)
        ok = record_user_feedback("correction", target, correction, agent=agent)
        if ok:
            return json.dumps({"success": True, "target": target, "correction": correction})
        return json.dumps(
            {
                "success": False,
                "error": "correction recording skipped (evolution inactive or write failed)",
            }
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("submit_correction tool error: %s", exc)
        return json.dumps({"success": False, "error": str(exc)})


registry.register(
    name="thumbs",
    toolset="feedback",
    schema=THUMBS_SCHEMA,
    handler=lambda args, **kw: thumbs(
        feedback=args.get("feedback", ""),
        target=args.get("target", ""),
        comment=args.get("comment", ""),
        **kw,
    ),
    emoji="👍",
)

registry.register(
    name="submit_correction",
    toolset="feedback",
    schema=CORRECTION_SCHEMA,
    handler=lambda args, **kw: submit_correction(
        target=args.get("target", ""),
        correction=args.get("correction", ""),
        **kw,
    ),
    emoji="✏️",
)
