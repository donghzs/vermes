# -*- coding: utf-8 -*-
"""citation_matcher.py — 公共引用匹配管线。

从 tools.py 的成熟管线抽取，供独立工具路径和旗舰一键成文路径共用。

解决的问题（F-25）：两条路径能力严重不对等——
  - 独立工具路径（tools.py）：score_relevance 粗排 + llm_rerank 精排 + 0.3 阈值 + 去重 + 连续编号
  - 旗舰路径（citation_provider.py）：仅启发式 score_relevance、无阈值、无 LLM 精排、无去重、跳号

本模块提供统一接口 match_citations()，两条路径共用同一套匹配逻辑：
  1. 候选池合并（本地库 + 在线检索，标题去重）
  2. score_relevance 粗排 → top-5
  3. llm_rerank 精排（fail-open 兜底粗排）
  4. 最低分阈值 0.3（低于跳过 + 标记 [?n]）
  5. 同篇去重（title_key 碰撞 → 合并编号）
  6. 连续编号（1, 2, 3... 不跳号）

返回 MatchResult(num_to_ref, ref_list, match_log, failed)。
调用方负责后续的正文替换 + 参考文献列表生成。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scholarforge.citation")


# ── 粗排评分（模块级纯函数）──

def score_relevance(paper, context: str, keyword: str) -> float:
    """粗排评分（0-1），用于在 LLM 精排前缩小候选池。

    仅同语言字面比对：提取标题/关键词的中文字面 token 与英文单词做重叠 +
    difflib 模糊相似。中文关键词 vs 英文标题四项因子恒为 0（无法桥接跨语言）。

    跨语言匹配由 match_citations 中的 llm_rerank 完成（LLM 天然理解双语）：
    本函数对中文→英文只能给出 0 分，真实跨语言选择依赖 llm_rerank 的 LLM 精排。
    """
    import difflib
    # 标题与关键词的 token 重叠（含中文 2-gram）
    kw_tokens = set(re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}', keyword.lower()))
    title_tokens = set(re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}', paper.title.lower()))
    overlap = len(kw_tokens & title_tokens) / max(len(kw_tokens), 1)

    # 模糊相似度（含摘要前 80 字符扩大匹配面）
    fuzzy = difflib.SequenceMatcher(
        None,
        keyword[:80].lower(),
        (paper.title + ' ' + (getattr(paper, 'abstract', '') or '')[:80]).lower()
    ).ratio()

    return min(overlap * 0.5 + fuzzy * 0.5, 1.0)


# ── LLM 精排（模块级，fail-open）──

async def llm_rerank(candidates_list, context: str, keyword: str,
                     llm_call_fn=None) -> list:
    """用 LLM 对候选论文做相对排序，返回 (paper, score) 列表降序。

    LLM 返回分数数量与候选不符或异常时，fail-open 兜底回 score_relevance 粗排。

    Args:
        llm_call_fn: 可选的 LLM 调用函数 (async (prompt, **kw) -> str)，
                     默认用 scholarforge.tools._call_llm。传入 None 时 fail-open 走粗排。
    """
    if not candidates_list:
        return []
    if len(candidates_list) == 1:
        return [(candidates_list[0], 1.0)]

    # 构造候选清单
    paper_lines = []
    for i, p in enumerate(candidates_list):
        title = p.title[:120]
        abstract = (getattr(p, 'abstract', '') or '')[:200]
        paper_lines.append(f"{i+1}. {title} | {abstract}")
    papers_text = "\n".join(paper_lines)

    prompt = (
        f"上下文引用片段：\"{context[:300]}\"\n\n"
        f"搜索关键词：{keyword}\n\n"
        f"候选论文：\n{papers_text}\n\n"
        f"请根据与引用上下文的相关性，对以上候选论文打分（0.0-1.0）。"
        f"只返回每行一个分数，按候选编号顺序：\n"
        f"1: 0.85\n2: 0.42\n..."
    )

    # 默认 LLM 调用
    if llm_call_fn is None:
        try:
            from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL
            llm_call_fn = lambda prompt, **kw: _call_llm(prompt, temperature=0.2, model=ANALYSIS_MODEL)
        except ImportError:
            # fail-open: 无 LLM 可用，走纯粗排
            return [(p, score_relevance(p, context, keyword)) for p in candidates_list]

    try:
        result = await llm_call_fn(prompt)
        if result and not result.startswith("❌"):
            scores = []
            for line in result.strip().split("\n"):
                m = re.match(r'\d+[:\.\s]+([\d\.]+)', line.strip())
                if m:
                    try:
                        scores.append(min(max(float(m.group(1)), 0.0), 1.0))
                    except ValueError:
                        scores.append(0.0)
                else:
                    scores.append(0.0)
            if len(scores) != len(candidates_list):
                # 数量不匹配，fail-open 走粗排
                return [(p, score_relevance(p, context, keyword)) for p in candidates_list]
            return [(candidates_list[i], scores[i]) for i in range(len(candidates_list))]
    except Exception as e:
        logger.warning(f"llm_rerank failed: {e}")

    # fail-open: 走粗排
    return [(p, score_relevance(p, context, keyword)) for p in candidates_list]


# ── 最低分阈值 ──

MIN_MATCH_SCORE = 0.3


# ── 匹配结果 ──

@dataclass
class MatchResult:
    """公共匹配管线的结果。"""
    num_to_ref: dict[int, int] = field(default_factory=dict)  # 原始编号 → 连续新编号
    ref_list: list[dict] = field(default_factory=list)        # 参考文献列表（连续编号）
    match_log: list[str] = field(default_factory=list)       # 匹配日志
    failed: list[int] = field(default_factory=list)          # 匹配失败的原始编号


# ── 公共匹配管线 ──

async def match_citations(
    unique_nums: list[int],
    candidates: dict[int, list],
    num_context: dict[int, str],
    num_keywords: dict[int, str],
    local_papers: list | None = None,
    llm_call_fn=None,
) -> MatchResult:
    """公共引用匹配管线。

    从 tools.py 抽取的成熟管线，供独立工具和旗舰一键成文共用。
    统一保证：0.3 阈值 + LLM 精排 + 去重 + 连续编号。

    Args:
        unique_nums: 排序后的唯一引用编号列表
        candidates: 每个编号对应的在线检索候选 {n: [PaperResult, ...]}
        num_context: 每个编号的正文上下文 {n: "..."}
        num_keywords: 每个编号的关键词 {n: "..."}
        local_papers: 本地文献库（可选，合并进候选池）
        llm_call_fn: 可选 LLM 调用函数（默认用 tools._call_llm）

    Returns:
        MatchResult: num_to_ref（原始→连续编号）、ref_list（参考文献列表）、日志、失败列表
    """
    seen_titles: set[str] = set()
    ref_list: list[dict] = []
    next_ref_num = 1
    num_to_ref: dict[int, int] = {}
    match_log: list[str] = []
    failed: list[int] = []
    local_papers = local_papers or []

    for n in unique_nums:
        # 1. 候选池合并（本地在前，标题去重）
        pool: list = []
        pool_titles: set[str] = set()
        for p in local_papers + candidates.get(n, []):
            tk = (p.title or "").lower().strip()[:80]
            if tk and tk not in pool_titles:
                pool_titles.add(tk)
                pool.append(p)
        if not pool:
            failed.append(n)
            match_log.append(f"  [{n}] ⚠️ 无候选文献")
            continue

        # 2. 粗排
        coarse = [(p, score_relevance(p, num_context.get(n, ''), num_keywords.get(n, '')))
                  for p in pool]
        coarse.sort(key=lambda x: x[1], reverse=True)

        # 3. LLM 精排（top-5）
        top_candidates = [p for p, _ in coarse[:5]]
        reranked = await llm_rerank(
            top_candidates, num_context.get(n, ''), num_keywords.get(n, ''),
            llm_call_fn=llm_call_fn,
        )
        reranked.sort(key=lambda x: x[1], reverse=True)

        best_paper, best_score = reranked[0]

        # 4. 最低分阈值（F-23 修复：低于 0.3 跳过，不强塞无关文献）
        if best_score < MIN_MATCH_SCORE:
            failed.append(n)
            match_log.append(f"  [{n}] ⚠️ 最佳匹配分数过低 ({best_score:.2f})，跳过")
            continue

        # 5. 同篇去重
        title_key = best_paper.title.lower().strip()[:80]
        if title_key in seen_titles:
            for ref in ref_list:
                if ref["title"].lower().strip()[:80] == title_key:
                    num_to_ref[n] = ref["ref_num"]
                    break
            match_log.append(f"  [{n}] → [{num_to_ref.get(n)}] (重复，合并)")
            continue

        # 6. 连续编号
        seen_titles.add(title_key)
        num_to_ref[n] = next_ref_num
        is_local = getattr(best_paper, "source", "") == "local"
        ref_list.append({
            "ref_num": next_ref_num,
            "title": best_paper.title,
            "authors": ", ".join(best_paper.authors[:3]) if best_paper.authors else "Unknown",
            "year": best_paper.year or "n.d.",
            "venue": getattr(best_paper, "venue", "") or "",
            "doi": getattr(best_paper, "doi", "") or "",
            "url": getattr(best_paper, "url", "") or "",
            "abstract": (getattr(best_paper, "abstract", "") or "")[:1000],
            "source": getattr(best_paper, "source", "") or "",
            "score": round(best_score, 2),
        })
        tag = "📚本地" if is_local else "🌐"
        match_log.append(f"  [{n}] → [{next_ref_num}] ✅ {tag} ({best_score:.0%}) {best_paper.title[:50]}")
        next_ref_num += 1

    return MatchResult(
        num_to_ref=num_to_ref,
        ref_list=ref_list,
        match_log=match_log,
        failed=failed,
    )


# ── 正文替换（公共，F-2/F-3 修复后的正则回调版）──

def expand_citation(s: str) -> list[int]:
    """展开 [n] / [n-m] / [n,m,...] 为编号列表。"""
    inner = s[1:-1]
    nums = []
    for part in inner.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            nums.extend(range(min(int(a), int(b)), max(int(a), int(b)) + 1))
        else:
            nums.append(int(part))
    return nums


def replace_citations_in_text(draft: str, num_to_ref: dict[int, int]) -> str:
    """正则回调单次替换正文引用编号。

    F-2/F-3 修复：旧代码用顺序 str.replace() 导致级联串号。
    正则回调单次替换，位置精确、不重扫。未匹配的占位符标记 [?n]。
    """
    def _sub(m: re.Match) -> str:
        nums = expand_citation(m.group(0))
        mapped = [num_to_ref.get(n) for n in nums]
        if all(r is not None for r in mapped):
            return f"[{','.join(str(r) for r in mapped)}]"
        return f"[?{m.group(0)[1:-1]}]"
    return re.sub(r'\[\d+(?:[-,]\d+)*\]', _sub, draft)


def build_references_section(ref_list: list[dict]) -> str:
    """生成参考文献列表（连续编号 1..N，只列被引用的）。"""
    lines = ["\n\n## 参考文献\n"]
    for ref in sorted(ref_list, key=lambda x: x["ref_num"]):
        lines.append(
            f"[{ref['ref_num']}] {ref['authors']} ({ref['year']}). "
            f"{ref['title']}. {ref.get('venue', '')}."
            + (f" DOI: {ref['doi']}" if ref.get("doi") else "")
            + "\n"
        )
    return "".join(lines)
