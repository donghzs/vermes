"""
ScholarForge — 研究选题拆解 (Research Map)

把一个模糊研究方向拆成：
1. 研究问题树（核心问题 → 子问题）
2. 共识/分歧/空白（领域现状）
3. 可验证假设（可直接进入方法设计）

LLM 只调 1 次，输出结构化 JSON，格式化为 Markdown 报告。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("scholarforge.research_map")

_SYS = (
    "你是一个资深学术顾问，擅长将模糊研究方向拆解为可执行的研究问题。"
    "只输出 JSON，不要解释或 Markdown 代码块包裹。"
)

_PROMPT_TEMPLATE = """请将以下研究方向拆解为结构化的研究地图：

【研究方向】{topic}
{context_hint}

请输出严格 JSON（不要 ```json 包裹），格式如下：
{{
  "core_question": "一句话核心研究问题",
  "sub_questions": [
    {{"question": "子问题", "aspect": "理论/方法/应用/评估"}}
  ],
  "consensus": ["该领域已达成共识的要点1", "共识2"],
  "controversies": [
    {{"point": "争议点", "positions": ["立场A", "立场B"]}}
  ],
  "gaps": ["尚未被充分研究空白1", "空白2"],
  "hypotheses": [
    {{
      "hypothesis": "可验证假设",
      "type": "差异/相关/因果",
      "key_variables": ["自变量", "因变量", "控制变量"],
      "testable": true
    }}
  ],
  "recommended_approaches": ["建议研究路径1", "路径2"]
}}

要求：
1. 子问题 3-6 个，覆盖理论/方法/应用/评估
2. 共识/争议/空白各 2-4 条，基于该领域文献的常见判断
3. 假设 2-4 条，每条标明类型和关键变量
4. 研究路径 2-3 条，给出大致方向"""


async def research_map(topic: str, context: str = "") -> str:
    """研究选题拆解。

    参数:
        topic: 研究方向/大题目，如 "大语言模型在教育中的应用"
        context: 可选补充上下文（用户已有文献、已有方法、限制条件等）

    返回:
        Markdown 格式的研究地图
    """
    if not topic.strip():
        return "❌ 请提供研究方向。"

    from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

    context_hint = f"\n【补充上下文】{context}" if context.strip() else ""

    prompt = _PROMPT_TEMPLATE.format(topic=topic, context_hint=context_hint)

    try:
        raw = await _call_llm(prompt, _SYS, temperature=0.2, model=ANALYSIS_MODEL)
    except Exception as e:
        logger.error("LLM call failed during research_map: %s", e)
        return f"❌ 研究选题拆解失败: {str(e)[:200]}"

    # 提取 JSON
    data = _parse_json(raw)
    if data is None:
        return "ℹ️ 未能从 LLM 响应中解析出结构化研究地图。请重试或调整研究方向描述。"

    return _format_report(data, topic)


def _parse_json(raw: str) -> dict | None:
    """从 LLM 响应中提取 JSON 对象，抗格式漂移。"""
    # 优先 ```json 块
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # 裸 { ... }
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        raw = m.group(0)

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse research_map JSON: %s", e)
        return None


def _format_report(data: dict, topic: str) -> str:
    """将解析后的 JSON 格式化为 Markdown 研究地图。"""
    lines = [
        f"## 🗺️ 研究地图：{topic}",
        "",
    ]

    # 核心问题
    cq = data.get("core_question", "")
    if cq:
        lines += ["### 🎯 核心研究问题", "", f"> {cq}", ""]

    # 子问题树
    subs = data.get("sub_questions", [])
    if subs:
        lines += ["### 🌿 研究问题树", ""]
        for i, sq in enumerate(subs, 1):
            q = sq.get("question", "") if isinstance(sq, dict) else str(sq)
            a = sq.get("aspect", "") if isinstance(sq, dict) else ""
            tag = f" `#{a}`" if a else ""
            lines.append(f"{i}. {q}{tag}")
        lines.append("")

    # 共识
    consensus = data.get("consensus", [])
    if consensus:
        lines += ["### ✅ 领域共识", ""]
        for c in consensus:
            lines.append(f"- {c}")
        lines.append("")

    # 争议
    controversies = data.get("controversies", [])
    if controversies:
        lines += ["### ⚡ 争议与分歧", ""]
        for cv in controversies:
            if isinstance(cv, dict):
                point = cv.get("point", "")
                positions = cv.get("positions", [])
                lines.append(f"- **{point}**")
                for p in positions:
                    lines.append(f"  - {p}")
            else:
                lines.append(f"- {cv}")
        lines.append("")

    # 研究空白
    gaps = data.get("gaps", [])
    if gaps:
        lines += ["### 🔍 研究空白", ""]
        for g in gaps:
            lines.append(f"- {g}")
        lines.append("")

    # 可验证假设
    hypotheses = data.get("hypotheses", [])
    if hypotheses:
        lines += ["### 🧪 可验证假设", ""]
        for i, h in enumerate(hypotheses, 1):
            if isinstance(h, dict):
                hyp = h.get("hypothesis", "")
                htype = h.get("type", "")
                variables = h.get("key_variables", [])
                testable = h.get("testable", True)
                tag = f" `{htype}`" if htype else ""
                check = "✅ 可验证" if testable else "⚠️ 验证难度高"
                lines.append(f"{i}. {hyp}{tag} — {check}")
                if variables:
                    lines.append(f"   变量: {', '.join(variables)}")
            else:
                lines.append(f"{i}. {h}")
        lines.append("")

    # 推荐路径
    approaches = data.get("recommended_approaches", [])
    if approaches:
        lines += ["### 📌 推荐研究路径", ""]
        for a in approaches:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines)
