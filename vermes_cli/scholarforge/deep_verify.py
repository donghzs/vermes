"""Tier3 深度验证 — 论断是否被所引论文的摘要支持。

挂在质量护栏 Tier3（仅显式全量检查 / 引用解析后）：不单独做工具。
流程：
  1. 提取本文论断（claims）。
  2. 对每条带 DOI 的引文，按 DOI 从 S2 取摘要（复用 abstract_backfill）。
  3. 用 LLM 判断该摘要支持哪些论断。
结果写回 section_quality（随质量报告一并落库）。

设计为纯函数 + 可注入 provider/llm，便于无网单测（mock provider + mock llm）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEEP_VERIFY_PROMPT = """你是一个严谨的学术论文评审助手。下面给出一篇【被引论文的摘要】和本文的【论断列表】。
请判断该摘要支持列表中的哪些论断。

【被引论文摘要】
{abstract}

【论断列表】
{claim_list}

请只输出 JSON：
{{"supported_claims": [支持的论断序号数组，如 [1,3]，无则 []], "confidence": 0.0-1.0, "reason": "一句话理由"}}"""


async def _judge(claims: List[str], abstract: str, llm=None) -> Dict[str, Any]:
    """用 LLM 判断摘要支持哪些论断。返回 {supported_claims, confidence, reason}。"""
    if not claims:
        return {"supported_claims": [], "confidence": 0.0, "reason": "无论断可校验"}
    fn = llm
    if fn is None:
        from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

        fn = lambda prompt: _call_llm(prompt, temperature=0.2, model=ANALYSIS_MODEL, json_mode=True)

    claim_list = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    try:
        resp = fn(_DEEP_VERIFY_PROMPT.format(abstract=abstract, claim_list=claim_list))
        # 兼容同步与异步 LLM（注入测试用同步假函数；生产用异步 _call_llm）
        if asyncio.iscoroutine(resp):
            resp = await resp
    except Exception as e:
        logger.warning("deep_verify llm failed: %s", e)
        return {"supported_claims": [], "confidence": 0.0, "reason": f"LLM 调用失败: {e}"}

    text = (resp or "").strip()
    # 兼容 ```json ... ``` 包裹
    if text.startswith("```"):
        text = text.strip("`")
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
        supported = [int(x) for x in data.get("supported_claims", []) if str(x).isdigit()]
        return {
            "supported_claims": supported,
            "confidence": float(data.get("confidence", 0.0)),
            "reason": str(data.get("reason", "")),
        }
    except Exception:
        return {"supported_claims": [], "confidence": 0.0, "reason": "无法解析评审结果"}


async def deep_verify_claims(
    claims: List[str],
    papers: List[Dict[str, Any]],
    provider=None,
    llm=None,
) -> List[Dict[str, Any]]:
    """Tier3 深度验证：每条论断是否被所引论文摘要支持。

    - 仅对带 DOI 的 papers 取摘要（fetch_abstract_by_doi，复用 S2）。
    - 每篇引文一次 LLM 调用，判断其摘要支持哪些论断。
    返回逐条 (claim, paper_doi, paper_title, supported, confidence, reason)。
    """
    from vermes_cli.scholarforge.abstract_backfill import fetch_abstract_by_doi

    claims = [str(c).strip() for c in (claims or []) if str(c).strip()]
    results: List[Dict[str, Any]] = []

    for p in papers or []:
        doi = (p.get("doi") or "").strip()
        title = p.get("title", "")
        if not doi:
            continue
        ab = fetch_abstract_by_doi(doi, provider=provider)
        if not ab.get("success") or not ab.get("abstract"):
            for c in claims:
                results.append({
                    "claim": c, "paper_doi": doi, "paper_title": title,
                    "supported": False, "confidence": 0.0,
                    "reason": "无法获取摘要，无法验证",
                })
            continue
        verdict = await _judge(claims, ab["abstract"], llm=llm)
        supported_set = set(verdict.get("supported_claims", []))
        for idx, c in enumerate(claims, 1):
            results.append({
                "claim": c, "paper_doi": doi, "paper_title": title,
                "supported": idx in supported_set,
                "confidence": verdict.get("confidence", 0.0),
                "reason": verdict.get("reason", ""),
            })
    return results


def format_deep_verify_report(results: List[Dict[str, Any]]) -> str:
    """按论断聚合的 Tier3 深度验证报告（Markdown）。"""
    if not results:
        return ""
    # 按论断分组
    by_claim: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        by_claim.setdefault(r["claim"], []).append(r)

    lines = ["### Tier3 深度验证：论断是否被引文摘要支持"]
    lines.append(f"共校验 {len(by_claim)} 条论断。")
    for claim, rows in by_claim.items():
        supporters = [r for r in rows if r.get("supported")]
        if supporters:
            names = ", ".join(f"*{r['paper_title']}*" for r in supporters)
            lines.append(f"- ✅ 「{claim}」获 {len(supporters)} 篇引文摘要支持：{names}")
        else:
            lines.append(f"- ⚠️ 「{claim}」无引文摘要支持（需补充可佐证的文献）")
    return "\n".join(lines)
