# -*- coding: utf-8 -*-
"""grounded_citation tool — 主张溯源锚点（对任意 agent 开放）。

复用 scholarforge 的通用引用匹配层（`grounded_citation.ground_claims`），
让 agent 在产出事实性陈述时能自动核对每条主张是否命中真实来源，
直接加固事实可信度（不依赖 [n] 占位符、不限于论文写作）。
"""
from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import registry

GROUNDED_CITATION_SCHEMA = {
    "name": "grounded_citation",
    "description": (
        "对一组事实性主张(claim)做溯源核验：为每条主张提取检索词、联网检索真实来源、"
        "逐条判定是否有真实文献/页面支撑，返回结构化溯源报告。"
        "适用场景：AI 生成的事实性陈述需要挂溯源锚点时，例如「X 于 Y 年提出 Z」这类"
        "需要真实出处支撑的断言。复用 ScholarForge 的引用匹配管线（粗排 + LLM 精排 + 阈值），"
        "免费源（OpenAlex/Crossref/arXiv/Semantic Scholar 等）驱动，无需额外凭证。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {"type": "string"},
                "description": "待溯源的主张列表，每条一句话（例如 [\"AlphaFold 在 2020 年 CASP14 中夺冠\", \"...\"]）",
            },
            "min_score": {
                "type": "number",
                "description": "支撑判定阈值（0-1，默认 0.3，与 ScholarForge 引用匹配同源）",
            },
        },
        "required": ["claims"],
    },
}


async def _handle_grounded_citation(args: Dict[str, Any]) -> str:
    """Handler for the ``grounded_citation`` tool."""
    args = args or {}
    raw_claims = args.get("claims")
    if isinstance(raw_claims, str):
        # 允许 JSON 数组字符串，或按行分割的纯文本
        try:
            raw_claims = json.loads(raw_claims)
        except (json.JSONDecodeError, ValueError):
            raw_claims = [ln.strip() for ln in raw_claims.split("\n") if ln.strip()]
    if not isinstance(raw_claims, list):
        return json.dumps(
            {"success": False, "error": "claims 必须是字符串数组"}, ensure_ascii=False
        )
    claims = [str(c).strip() for c in raw_claims if str(c).strip()]
    if not claims:
        return json.dumps(
            {"success": False, "error": "claims 不能为空"}, ensure_ascii=False
        )

    min_score = args.get("min_score")
    try:
        min_score = float(min_score) if min_score is not None else 0.3
    except (TypeError, ValueError):
        min_score = 0.3
    min_score = max(0.0, min(min_score, 1.0))

    try:
        from vermes_cli.scholarforge.grounded_citation import (
            ground_claims,
            format_grounded_report,
        )
        # 显式注入 scholarforge 的 LLM 调用，使关键词提取 + 精排都走分析模型
        # 注④：_call_llm/ANALYSIS_MODEL 是 scholarforge 私有函数，是「通用溯源层
        # 真挪到 agent/」时的真实耦合点（plugins/grounded_citation_auto 同此依赖）。
        from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

        async def _llm(prompt, **kw):
            return await _call_llm(
                prompt,
                temperature=kw.get("temperature", 0.2),
                model=kw.get("model") or ANALYSIS_MODEL,
            )

        results = await ground_claims(
            claims, llm_call_fn=_llm, min_score=min_score
        )
        report = format_grounded_report(results)
        return json.dumps(
            {
                "success": True,
                "report": report,
                "results": results,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps(
            {"success": False, "error": f"溯源失败: {str(e)[:200]}"},
            ensure_ascii=False,
        )


registry.register(
    name="grounded_citation",
    toolset="literature",
    schema=GROUNDED_CITATION_SCHEMA,
    handler=_handle_grounded_citation,
    is_async=True,
    emoji="🔗",
    description="对事实性主张做溯源核验（复用 ScholarForge 引用匹配管线，免费源驱动）",
)
