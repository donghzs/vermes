"""
ScholarForge — 主张-证据审查流水线 (Claim-Audit Pipeline)

设计原则：
- LLM 只调一次抽取全部 Claim
- 三个 validator 各跑一次（detect/citation 全文批量；stats 仅 statistical claim）
- 只编排，不重写任何 validator
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("scholarforge.claim_audit")

# Lazy imports — avoid top-level binding so test patches on validators module take effect.
def _import_validators():
    from vermes_cli.scholarforge.validators import (
        check_statistics_consistency,
        detect_design_flaws,
        format_citation_report,
        format_design_report,
        verify_citation_authenticity,
    )
    return (
        check_statistics_consistency,
        detect_design_flaws,
        format_citation_report,
        format_design_report,
        verify_citation_authenticity,
    )

_SYS = (
    "你是一个严谨的方法学审稿人。只输出 JSON，不要解释或 Markdown 代码块包裹。"
)


async def _extract_claims(paper_text: str) -> list[dict]:
    """调用 LLM 从论文中抽取核心主张(Claim)。

    返回 dict 列表，每条含:
      claim, type, section, evidence_quote, citations, stats
    """
    from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

    prompt = (
        '请从论文中逐段抽取"核心主张(Claim)"——作者明确断言且需被证据支撑的陈述。\n'
        "跳过背景、综述中他人工作、纯方法步骤。每条给：\n"
        "- claim: 主张原文(一句话)\n"
        "- type: empirical(实证)/statistical(含统计量)/methodological(方法设计)/theoretical(理论)\n"
        "- section: 章节名\n"
        "- evidence_quote: 支撑该主张的附近原文\n"
        '- citations: 该处引用编号列表如 [1,3]，无则 []\n'
        "- stats: 若 type=statistical 抽文中效应量/检验量"
        "(eta_squared/cohens_d/t_value/df/f_value/df_error/p_value/"
        "n_group1/n_group2/mean_diff/pooled_sd)，否则 {}\n"
        "输出严格 JSON 数组。\n"
        f"论文：\n{paper_text[:9000]}"
    )
    try:
        raw = await _call_llm(prompt, _SYS, temperature=0.2, model=ANALYSIS_MODEL)
    except Exception as e:
        logger.error("LLM call failed during claim extraction: %s", e)
        return []

    # 尝试从回复中提取 JSON 数组
    # 优先匹配 ```json ... ``` 代码块，再 fallback 到裸 [ ... ]
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            raw = m.group(0)
        else:
            logger.warning("No JSON array found in LLM response")
            return []

    try:
        claims = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse claims JSON: %s", e)
        return []

    if not isinstance(claims, list):
        return []

    return [
        c
        for c in claims
        if isinstance(c, dict) and c.get("claim")
    ]


def _check_design_overlap(claim_text: str, flaws: list) -> str:
    """软匹配：claim 文本与 DesignFlaw.evidence/description 的中文二元组重叠≥2。

    v1 近似方案；后续可给 DesignFlaw 加 section 字段做精确匹配。
    """
    if not flaws:
        return "✅ 未检出相关缺陷"

    claim_bigrams = set(re.findall(r"[\u4e00-\u9fff]{2,}", claim_text))
    if not claim_bigrams:
        return "✅ 未检出相关缺陷"

    for f in flaws:
        hay = f"{f.evidence} {f.description}"
        hay_bigrams = set(re.findall(r"[\u4e00-\u9fff]{2,}", hay))
        if len(claim_bigrams & hay_bigrams) >= 2:
            return f"⚠️ 受影响({f.category})"

    return "✅ 未检出相关缺陷"


async def review_claims(
    paper_text: str,
    references: list[dict] | None = None,
    design_info: dict | None = None,
    enable_online: bool = True,
) -> str:
    """主张-证据审查流水线。

    参数:
        paper_text: 论文全文
        references: 文献列表 [{title, authors, year, venue, doi}]
        design_info: 可选的结构化设计信息
        enable_online: 是否在线验证引用（CI 设 False）

    返回:
        Markdown 审查报告
    """
    # Step 1: LLM 抽取全部 Claim（只调一次）
    claims = await _extract_claims(paper_text)
    if not claims:
        return "ℹ️ 未能从论文中抽取到可审查的核心主张。"

    # Step 2: 三个 validator 各跑一次
    _check_stats, _detect_flaws, _fmt_cit, _fmt_design, _verify_cit = _import_validators()

    # 2a: 设计缺陷（全文跑 1 次）
    flaws = _detect_flaws(paper_text, design_info or {})

    # 2b: 引用真实性（references 批量跑 1 次）
    cit_checks: list = []
    if references:
        cit_checks = await _verify_cit(
            references, enable_online=enable_online
        )
    cit_by_ref = {c.ref_num: c for c in cit_checks}

    # 2c: 统计一致性（仅 statistical claim 跑，确定性纯函数）
    # 预跑所有 statistical claim 的 stats（每个独立调，但纯函数无网络/LLM）
    stat_results: dict[int, list] = {}
    for i, c in enumerate(claims):
        if c.get("type") == "statistical" and c.get("stats"):
            stat_results[i] = _check_stats(c["stats"])

    # Step 3: 逐条 claim 拼证据链
    rows: list[tuple] = []
    weak_count = 0

    for idx, c in enumerate(claims):
        # 引用支撑
        citations = c.get("citations") or []
        if not citations:
            cit_s = "— 无引用"
        else:
            cit_parts = []
            for ref in citations:
                try:
                    ref_int = int(ref)
                except (ValueError, TypeError):
                    ref_int = ref
                chk = cit_by_ref.get(ref_int)
                if chk is None:
                    cit_parts.append(f"⚠️ 引用[{ref}]未提供文献列表")
                    weak_count += 1
                elif chk.verified:
                    cit_parts.append(f"✅ [{ref}]真实")
                else:
                    cit_parts.append(f"⚠️ [{ref}]未验证({chk.source})")
                    weak_count += 1
            cit_s = "; ".join(cit_parts)

        # 统计支撑
        stats = c.get("stats") or {}
        if c.get("type") == "statistical" and stats:
            bad = [
                s for s in stat_results.get(idx, [])
                if not getattr(s, "consistent", True)
            ]
            if bad:
                stat_s = f"⚠️ 不一致({len(bad)})"
                weak_count += len(bad)
            else:
                stat_s = "✅ 一致"
        else:
            stat_s = "—"

        # 设计支撑（软匹配）
        claim_text = f"{c.get('evidence_quote', '')} {c.get('claim', '')}"
        des_s = _check_design_overlap(claim_text, flaws)
        if des_s.startswith("⚠️"):
            weak_count += 1

        verdict = "支撑充分" if "⚠️" not in (cit_s + stat_s + des_s) else "需复核"

        rows.append(
            (
                idx + 1,
                str(c.get("claim", ""))[:60],
                c.get("type", ""),
                cit_s,
                stat_s,
                des_s,
                verdict,
            )
        )

    # Step 4: 生成 Markdown 审查表
    lines = [
        "## ⚖️ 主张-证据审查",
        "",
        f"共抽取 **{len(claims)}** 条核心主张，其中 **{weak_count}** 条需复核。",
        "",
        "| # | 主张 | 类型 | 引用 | 统计 | 设计 | 结论 |",
        "|--|------|------|------|------|------|------|",
    ]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} |")

    lines += [
        "",
        "---",
        "",
        "### 引用核查明细",
        _fmt_cit(cit_checks) if cit_checks else "（未提供文献列表）",
        "",
        "### 设计缺陷明细",
        _fmt_design(flaws) if flaws else "（未检出）",
    ]

    return "\n".join(lines)
