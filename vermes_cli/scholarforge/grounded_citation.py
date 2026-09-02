# -*- coding: utf-8 -*-
"""grounded_citation.py — 通用「主张→溯源锚点」匹配层。

从 scholarforge 学术引用管线抽出的通用层，供任意 agent 在产出事实性陈述时
自动挂溯源锚点（claim → 真实来源 → 支撑判定），而非仅限 ScholarForge 论文写作。

复用（零从零成本）：
  - `citation_matcher.score_relevance`  粗排（0-1，标题/关键词字面重叠 + difflib）
  - `citation_matcher.llm_rerank`       LLM 精排（fail-open 兜底粗排）
  - `citation_matcher.MIN_MATCH_SCORE`  最低分阈值（低于则判「无支撑」）
  - `search.search_papers`              多源聚合检索（免费源 + 付费源 + 本地库）

与 `citation_matcher.match_citations` 的差异：后者围绕论文草稿中的 `[n]` 占位符
做编号重映射；本模块面向**任意自由文本主张**（无占位符），逐条 claim 独立溯源，
不涉及编号/去重/连续编号。
"""
from __future__ import annotations

import logging
import re
from typing import Any, AsyncGenerator, Callable

logger = logging.getLogger("scholarforge.grounded_citation")

# 与 citation_matcher 同源阈值，保证两条路径判定口径一致
MIN_MATCH_SCORE = 0.3
# 每条 claim 的检索结果上限 / 进入精排的候选数
_SEARCH_LIMIT = 8
_RERANK_TOP_K = 5


# ── 关键词提取（LLM 优先 + 正则兜底）──────────────────────────

def _extract_keyword_regex(text: str) -> str:
    """纯正则兜底：专有名词 + 英文术语 + 中文词，合并去重取前 6 个。"""
    # 专有名词（连续大写开头）
    proper = re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9]{2,}(?![A-Za-z0-9])', text)
    stop_proper = {
        'The', 'This', 'That', 'These', 'Those', 'Such', 'However', 'Moreover',
        'Furthermore', 'Therefore', 'Also', 'While', 'When', 'Where', 'What',
        'Which', 'Based', 'Using', 'Given', 'Since', 'From', 'With', 'Both',
        'Each', 'First', 'Second', 'Third', 'Finally', 'In', 'For', 'And',
        'But', 'Not', 'Are', 'Was', 'Were', 'Has', 'Have', 'Can', 'May',
        'Will', 'Been', 'Some', 'More', 'Most', 'Other', 'All', 'One', 'Two',
        'Three',
    }
    proper = [w for w in proper if w not in stop_proper]

    stop_en = {
        'the', 'and', 'for', 'are', 'but', 'not', 'this', 'that', 'with',
        'from', 'have', 'has', 'was', 'were', 'will', 'can', 'may', 'also',
        'such', 'than', 'then', 'these', 'those', 'which', 'their', 'there',
        'what', 'when', 'where', 'who', 'whom', 'been', 'being', 'into',
        'about', 'after', 'before', 'between', 'through', 'during', 'above',
        'below', 'over', 'under', 'again', 'more', 'most', 'other', 'some',
    }
    en_words = [w for w in re.findall(r'[A-Za-z]{3,30}', text) if w.lower() not in stop_en]

    stop_cn = {
        '的研究', '本文', '本研', '研究', '方法', '结果', '结论', '实验', '分析',
        '通过', '基于', '采用', '提出', '实现', '一个', '可以', '这个', '那个',
        '因此', '所以', '然而', '此外', '同时', '另外', '首先', '其次', '最后',
        '表明', '发现', '认为', '显示', '说明',
    }
    cn_words = [w for w in re.findall(r'[\u4e00-\u9fa5]{2,4}', text) if w not in stop_cn]

    seen: set[str] = set()
    out: list[str] = []
    for w in proper + en_words[:5] + cn_words[:3]:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            out.append(w)
    if not out:
        return text[:60].strip()
    return ' '.join(out[:6])


async def _extract_keyword(claim: str, llm_call_fn) -> str:
    """LLM 提取检索关键词，失败回退正则。"""
    if llm_call_fn is not None:
        try:
            prompt = (
                f"从以下陈述中提取 2-4 个用于学术/事实检索的关键短语"
                f"（英文/中文均可，优先领域术语与专有名词）。"
                f"只返回空格分隔的短语，不要解释：\n\n{claim[:500]}"
            )
            result = await llm_call_fn(prompt, temperature=0.2)
            if result and isinstance(result, str) and not result.startswith("❌"):
                kw = result.strip().split("\n")[0].strip('"\'。，, ')
                if len(kw) >= 3:
                    return kw[:120]
        except Exception as e:
            logger.debug(f"grounded_citation: LLM keyword extraction failed: {e}")
    return _extract_keyword_regex(claim)


# ── 检索收集 ──────────────────────────────────────────────────

async def _collect_papers(search_fn, keyword: str, limit: int) -> list:
    """把 search_fn 产出的候选收集为列表（兼容 async generator / 返回 list 两种）。"""
    try:
        result = search_fn(keyword, limit=limit)
    except TypeError:
        # 注入方可能只接受 keyword 单参数
        result = search_fn(keyword)
    if hasattr(result, "__aiter__"):
        out = []
        async for p in result:
            out.append(p)
            if len(out) >= limit:
                break
        return out
    # 同步返回 list 或 awaitable
    if hasattr(result, "__await__"):
        result = await result
    return list(result or [])


# ── 单条 claim 溯源 ────────────────────────────────────────────

async def ground_claim(
    claim: str,
    *,
    search_fn: Callable | None = None,
    llm_call_fn: Callable | None = None,
    min_score: float = MIN_MATCH_SCORE,
) -> dict:
    """对单条主张做溯源，返回结构化结果。

    Args:
        claim: 待溯源的主张文本（一句话）。
        search_fn: 检索函数 `(keyword, limit=...) -> async-gen/list[PaperResult]`。
                   默认 `vermes_cli.scholarforge.search.search_papers`。
        llm_call_fn: LLM 调用 `(prompt, **kw) -> str`。默认
                     `vermes_cli.scholarforge.tools._call_llm`。None 时跳过 LLM
                     精排（纯粗排）。
        min_score: 支撑判定阈值（默认 0.3，与 citation_matcher 同源）。

    Returns:
        dict: {claim, keyword, verdict, best, sources, error}
    """
    from vermes_cli.scholarforge.citation_matcher import (
        score_relevance,
        llm_rerank,
    )

    if search_fn is None:
        from vermes_cli.scholarforge.search import search_papers
        search_fn = search_papers

    keyword = await _extract_keyword(claim, llm_call_fn)

    result = {
        "claim": claim,
        "keyword": keyword,
        "verdict": "unsupported",
        "best": None,
        "sources": [],
        "error": None,
    }

    try:
        candidates = await _collect_papers(search_fn, keyword, _SEARCH_LIMIT)
    except Exception as e:
        result["error"] = f"检索失败: {str(e)[:160]}"
        return result

    if not candidates:
        result["error"] = "无候选来源"
        return result

    # 粗排 → 取 top-K → LLM 精排（fail-open 兜底粗排）
    coarse = sorted(
        candidates,
        key=lambda p: score_relevance(p, claim, keyword),
        reverse=True,
    )[: _RERANK_TOP_K]

    # 单候选：llm_rerank 的「只有一篇=就是它」捷径会硬编码 1.0，
    # 这是为「引用编号→唯一论文」语义设计的，对开放式事实溯源会造成假阳性
    # （检索只返回一篇无关文献也判 1.0）。此处改用真实 score_relevance 作为分数。
    if len(coarse) == 1:
        reranked = [(coarse[0], score_relevance(coarse[0], claim, keyword))]
    else:
        reranked = await llm_rerank(coarse, claim, keyword, llm_call_fn=llm_call_fn)
    reranked.sort(key=lambda x: x[1], reverse=True)

    sources = []
    for p, score in reranked:
        sources.append({
            "title": getattr(p, "title", "") or "",
            "authors": ", ".join(getattr(p, "authors", [])[:3] or []) or "Unknown",
            "year": getattr(p, "year", "") or "n.d.",
            "venue": getattr(p, "venue", "") or "",
            "doi": getattr(p, "doi", "") or "",
            "url": getattr(p, "url", "") or "",
            "source": getattr(p, "source", "") or "",
            "abstract": (getattr(p, "abstract", "") or "")[:200],
            "score": round(score, 2),
        })

    best = sources[0] if sources else None
    if best and best["score"] >= min_score:
        result["verdict"] = "supported"
    result["best"] = best
    result["sources"] = sources
    return result


async def ground_claims(
    claims: list[str],
    *,
    search_fn: Callable | None = None,
    llm_call_fn: Callable | None = None,
    min_score: float = MIN_MATCH_SCORE,
) -> list[dict]:
    """批量溯源多条主张（顺序执行，每条一次检索 + 精排）。"""
    results = []
    for claim in claims:
        claim = (claim or "").strip()
        if not claim:
            continue
        results.append(await ground_claim(
            claim,
            search_fn=search_fn,
            llm_call_fn=llm_call_fn,
            min_score=min_score,
        ))
    return results


# ── 报告格式化 ────────────────────────────────────────────────

def format_grounded_report(results: list[dict]) -> str:
    """把 ground_claims 的结果渲染为 Markdown 报告。"""
    if not results:
        return "ℹ️ 没有可溯源的主张。"

    supported = sum(1 for r in results if r.get("verdict") == "supported")
    lines = [
        "## 🔗 溯源报告（Grounded Citations）",
        "",
        f"共 **{len(results)}** 条主张，**{supported}** 条找到支撑来源，"
        f"**{len(results) - supported}** 条未能充分支撑。",
        "",
    ]

    for i, r in enumerate(results, 1):
        verdict = r.get("verdict")
        mark = "✅ 有支撑" if verdict == "supported" else "⚠️ 无充分支撑"
        lines.append(f"### [{i}] {mark}")
        lines.append(f"**主张**：{r.get('claim', '')}")
        lines.append(f"**检索式**：`{r.get('keyword', '')}`")
        if r.get("error"):
            lines.append(f"**原因**：{r['error']}")
        best = r.get("best")
        if best:
            lines.append("")
            lines.append(
                f"**最佳来源**（置信 {best['score']:.0%}）："
                f"{best['authors']} ({best['year']}). *{best['title']}*."
                f"{' ' + best['venue'] + '.' if best['venue'] else ''}"
                + (f" DOI: {best['doi']}" if best['doi'] else "")
            )
            alt = [s for s in r.get("sources", [])[1:4]]
            if alt:
                lines.append("")
                lines.append("**备选来源**：")
                for s in alt:
                    lines.append(
                        f"- {s['authors']} ({s['year']}). *{s['title']}* "
                        f"（置信 {s['score']:.0%}）"
                    )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
