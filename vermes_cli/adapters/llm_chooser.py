"""默认 llm_chooser：复用 Vermes agent 统一配置的 LLM 做工具选择（L2a 阶段二）。

设计纪律（守「薄」+ 模型无关 + 零独立配置）：
- 复用 `agent.auxiliary_client.call_llm`（走 "auto" 检测链 = 用户已配的那个 LLM），
  **不要求用户为 select_tool 单独配 API** —— 小白开箱即用，第一次就能跑。
- 用「文本输出工具名」而非原生 tool_choice，任何 chat 模型都能做，不替用户
  假设模型能力（DeepSeek/GPT/Gemini 通吃）。
- 未配置 / 调用失败 / 输出未命中候选集 → 返回 None → select_tool 降级启发式 argmax。
- 用户可注入自己的 llm_chooser 覆盖默认（select_tool 的 llm_chooser 参数已预留），
  实现「按域选模型 / 垂直域专用 prompt」等用户生态自发生长的差异化，框架不预设。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是工具选择器。给定用户意图和候选工具列表，从中选出唯一最匹配的工具。"
    "只回复该工具名，不要任何解释、标点或额外文字。"
)

_MAX_TOKENS = 64


def _format_candidates(tools) -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in tools)


def default_llm_chooser(tools, intent: str, ctx: Optional[dict] = None) -> Optional[str]:
    """用 agent 统一配置的 LLM 从候选工具中选一个，返回工具名；不可用返回 None。

    返回 None 语义 = 「LLM 不可用 / 未配置 / 失败」，调用方（select_tool）据此
    降级到启发式 argmax，而不是把「LLM 没答」误判成「LLM 选错」。

    签名与 select_tool 的 llm_chooser 契约一致：
    Callable[[list[ToolSummary], str, Optional[dict]], Optional[str]]
    """
    if not tools:
        return None
    try:
        from agent.auxiliary_client import call_llm
    except Exception as exc:  # noqa: BLE001 - agent 不可用时降级，不阻塞
        logger.debug("llm_chooser: agent.auxiliary_client 不可用: %s", exc)
        return None

    names = [t.name for t in tools]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"意图：{intent}\n\n候选工具：\n{_format_candidates(tools)}\n\n"
                "请回复唯一工具名："
            ),
        },
    ]
    try:
        resp = call_llm(messages=messages, temperature=0.0, max_tokens=_MAX_TOKENS)
        content = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - 未配置/网络失败 → 降级
        logger.debug("llm_chooser: LLM 调用失败，降级启发式: %s", exc)
        return None

    # 候选名校验：LLM 输出必须命中候选集（防编造/废话，消化 argmax 无门槛反模式）。
    # 按名字长度降序匹配，避免短名前缀误配长名（如 freecad_part vs freecad_part_fillet_3d）。
    for n in sorted(names, key=len, reverse=True):
        if n in content:
            return n
    logger.debug("llm_chooser: LLM 输出未命中候选集，降级: %r", content[:80])
    return None
