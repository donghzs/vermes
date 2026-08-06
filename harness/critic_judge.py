"""P2 任务级 Critic — ``CriticJudge``（实现 ``OutcomeJudge`` 协议）。

这是 CS329A「生成 → 验证(P0-A) → 筛选(P2) → 训练」闭环里缺失的最后一环：
在 P1 采样的 N 个最终答案候选里做**文本质量筛选**。P1 的 ``DefaultJudge`` 是
保守过滤器（不装懂），本类是有 rubric 的 LLM 法官，但通过 ``OutcomeJudge``
协议即插即用，P1 的 ``sample_best_of_n`` / ``OutcomeJudge`` 协议 **零改动**。

设计（与用户拍板的三分支一致，见报告 §五）：

分叉1 — 选优基础 = 混合选优（先确定性硬闸，再 LLM 法官）：
  - **硬闸**（代码层确定性）：本回合 ``recent_tool_verify`` 非空且 *全部*
    ``ok is False`` → 候选被外证证伪，LLM 无需判断，直接退化到 baseline
    ``(0, ...)`` + warning。这是「用户至少看到回复」的兜底，不是假装通过。
  - **LLM 法官**（部分 False / 空信号时走）：按 RUBRIC 硬排除 + 选优，输出
    JSON ``{winner_idx, reasoning}``。rubric 不含模糊「选最优答案」指令
    （自证陷阱入口），且强制「tool_verify=False 直接排除」「全排除返回 0」。

分叉2 — 信号桥 = 一并接通：``recent_tool_verify`` 由 P0-A Verifier 写入
（``record_tool_verify``，只收集本回合，不跨回合累积），此处只读。

分叉3 — 法官模型 = 可配置，默认复用主对话 provider：
  - 法官调用**必须走 ``agent._interruptible_api_call`` 同一条中断路径**，否则
    用户中断时 best-of-N 卡死。
  - ``critic_model`` 可指定更强/不同模型；缺省 ``None`` → 复用主调用 model。

R5 反向验证（三层，均「能拒绝、不假装通过」）：
  - 正向 A 真实 / B 编造 → 选 A
  - 反向 A/B 都编造（tool_verify 全 False）→ 返回 (0, "...unverifiable...")，
    不调 LLM、不选「最好的编造」
  - 回归 空 ``recent_tool_verify`` → 不崩，走 LLM（或 fail-open 退化）

全部失败路径 fail-open → ``(0, ...)`` baseline，绝不阻断 agent loop。
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("vermes.harness.critic_judge")


# ─── RUBRIC v1（极简：硬闸 + 「从幸存候选选最准确回答用户问题的」） ────────────

RUBRIC = """\
你是严格的答案筛选评审。给定针对【同一个用户问题】的 N 个候选最终答案，以及可选的\
工具验证信号（tool_verify）。

【硬排除规则（必须在任何质量比较之前应用）】
- 若某候选的验证信号标记为未通过（tool_verify ok=false），直接排除该候选，不得为其排序或挽留。
- 若所有候选都被上述规则排除，你必须返回 winner_idx=0，并在 reasoning 中说明「所有候选均未通过验证」。不得臆造胜者。

【选优规则（仅在通过排除的候选中比较）】
- 选择最准确、最完整地回答用户问题的候选。
- 优先选择「与已验证事实一致、无编造/无支撑断言」的候选，而不是「更流畅或更长的」候选。
- 若多个候选通过排除且无明显更优者，返回第一个通过排除的候选索引。

【输出格式】
你【只能】输出单个 JSON 对象，不要输出任何说明文字、不要使用 markdown 代码块：
{"winner_idx": <int, 0-based 所选候选索引>, "reasoning": "<一句话，说明你应用了哪条排除/选优规则>"}
"""


# ─── 文本抽取 / 格式化（防御式） ───────────────────────────────────────────────

def _extract_text(response: Any) -> str:
    """从 provider 响应抽取文本内容。兼容属性访问与 dict 两种形状。"""
    try:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return ""
        choice = choices[0]
        if isinstance(choice, dict):
            msg = choice.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else ""
        else:
            msg = getattr(choice, "message", None)
            content = getattr(msg, "content", None) if msg is not None else None
        if content is None:
            content = ""
        return content if isinstance(content, str) else str(content)
    except Exception:  # noqa: BLE001 - 抽取失败不阻断
        return ""


def _extract_user_question(base_api_kwargs: dict) -> str:
    """从主调用 messages 抽取最后一条 user 消息，作为法官判断「回答问题」的锚。"""
    try:
        messages = base_api_kwargs.get("messages") or []
        for m in reversed(messages):
            if not isinstance(m, dict):
                continue
            if m.get("role") != "user":
                continue
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # multimodal content parts
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                if parts:
                    return "\n".join(parts)
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _format_candidates(candidates: List[Any], tool_verify: list, user_question: str) -> str:
    """拼装法官 user 消息：用户问题 + 候选 + 验证信号。"""
    lines: List[str] = []
    if user_question:
        lines.append("【用户问题】")
        lines.append(user_question)
        lines.append("")
    lines.append("【候选最终答案】")
    for c in candidates:
        idx = getattr(c, "index", "?")
        content = getattr(c, "content", "") or ""
        # 截断过长内容，避免压垮法官窗口（候选本身已在前一步生成）
        if len(content) > 4000:
            content = content[:4000] + " …[truncated]"
        lines.append(f"[{idx}] {content}")
    lines.append("")
    if tool_verify:
        lines.append("【本回合工具验证信号】（ok=false 的候选被硬排除）")
        for e in tool_verify:
            if not isinstance(e, dict):
                continue
            fn = e.get("function_name", "?")
            ok = e.get("ok")
            reason = e.get("reason", "") or ""
            lines.append(f"- {fn}: ok={ok} {reason}")
        lines.append("")
    lines.append("按系统指令输出 JSON。")
    return "\n".join(lines)


def _parse_judge_json(content: str, n: int) -> Tuple[Optional[int], str]:
    """解析法官 JSON。任何异常/越界 → 返回 (None, 退化原因)，由调用方 fail-open。"""
    if not content or not content.strip():
        return None, "judge returned empty content"
    # 容忍 markdown 代码块包裹 / 前后杂文本
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, f"judge output has no JSON object: {content[:120]!r}"
    blob = text[start : end + 1]
    try:
        data = json.loads(blob)
    except Exception as e:  # noqa: BLE001
        return None, f"judge JSON parse error: {e}"
    if not isinstance(data, dict):
        return None, f"judge JSON is not an object: {type(data).__name__}"
    winner = data.get("winner_idx")
    if not isinstance(winner, int) or isinstance(winner, bool):
        return None, f"judge winner_idx missing/non-int: {winner!r}"
    if not (0 <= winner < n):
        return None, f"judge winner_idx out of range: {winner} (n={n})"
    reasoning = data.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, str) else str(reasoning)
    return winner, reasoning


class CriticJudge:
    """P2 任务级 Critic。实现 ``OutcomeJudge.judge``，即插即用替换 DefaultJudge。

    线程安全：仅读取 ``agent._interruptible_api_call`` 与传入的 ``context``，
    不修改共享状态（信号桥写入在 ``outcome_verifier.record_tool_verify``）。
    """

    def __init__(
        self,
        agent: Any,
        critic_model: Optional[str] = None,
        base_api_kwargs: Optional[dict] = None,
    ) -> None:
        self._agent = agent
        self._critic_model = critic_model
        self._base = base_api_kwargs or {}

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def judge(self, candidates: List[Any], context: dict) -> Tuple[int, str]:
        """返回 (winner_idx, reasoning)。fail-open：任何异常 → (0, ...)。"""
        if not candidates:
            return 0, "no candidates; degraded to baseline"

        tv: list = (context.get("recent_tool_verify") or []) if isinstance(context, dict) else []

        # 硬闸：本回合全部验证失败 → 候选被外证证伪，LLM 无需判断，退化 baseline
        if tv and all(isinstance(e, dict) and e.get("ok") is False for e in tv):
            logger.warning(
                "CriticJudge: all %d tool_verify results are False "
                "(candidates contain unverifiable claims); degrade to baseline index 0",
                len(tv),
            )
            return (
                0,
                "all tool_verify results are False (candidates contain unverifiable "
                "claims); degraded to baseline index 0 — not selecting a fabricated answer",
            )

        # LLM 法官：部分 False / 空信号时走 rubric 选优
        try:
            api_kwargs = self._build_judge_kwargs(candidates, tv)
            resp = self._agent._interruptible_api_call(api_kwargs)
            content = _extract_text(resp)
            winner_idx, reasoning = _parse_judge_json(content, n=len(candidates))
            if winner_idx is None:
                logger.warning("CriticJudge parse/degrade: %s", reasoning)
                return 0, f"critic judge invalid output → baseline: {reasoning}"
            return winner_idx, reasoning
        except Exception as e:  # noqa: BLE001 - fail-open
            logger.warning("CriticJudge judge failed, degraded to baseline: %s", e)
            return 0, f"critic judge error → baseline: {e}"

    # ── 内部：构建法官请求 ─────────────────────────────────────────────────────

    def _build_judge_kwargs(self, candidates: List[Any], tool_verify: list) -> dict:
        kwargs = dict(self._base)  # clone，不污染主调用
        kwargs["model"] = self._critic_model or self._base.get("model")
        kwargs["temperature"] = 0
        kwargs["stream"] = False
        user_question = _extract_user_question(self._base)
        kwargs["messages"] = [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": _format_candidates(candidates, tool_verify, user_question)},
        ]
        # 法官调用不需要 tools / tool_choice（它是纯评审）
        kwargs.pop("tools", None)
        kwargs.pop("tool_choice", None)
        kwargs.pop("functions", None)
        return kwargs
