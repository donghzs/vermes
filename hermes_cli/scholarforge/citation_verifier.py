"""
ScholarForge 引用验证模块 — 深度 LLM 验证引用真实性
三层验证：范围检查 → Fuzzy 标题匹配 → LLM 断论-支撑验证

Phase 2.3 增强：
- Fuzzy 匹配：用 difflib 快速判断正文引用声称是否与文献标题匹配
- 溯源标注：验证通过时自动标注"可溯源至 [n] Abstract/Title"
- LLM 深度验证作为补充（仅在 fuzzy 无法判定时使用）
"""
import difflib
import json
import logging
import re
from typing import Callable, Awaitable

logger = logging.getLogger("scholarforge.citation")


# 验证结果结构
class CitationVerifyResult:
    def __init__(self, ref_num: int, score: int, reason: str, accurate: bool,
                 trace: str = "", method: str = "llm"):
        self.ref_num = ref_num
        self.score = score  # 0-10, 0=完全捏造, 10=精确吻合
        self.reason = reason
        self.accurate = accurate
        self.trace = trace      # 溯源信息，如 "Title 完全匹配" / "Abstract 匹配度 85%"
        self.method = method    # "fuzzy" | "llm" | "range_only"

    def to_warning(self) -> str:
        if self.score >= 8:
            return ""  # 不需要警告
        elif self.score >= 6:
            return f"⚠️ [{self.ref_num}] 引用可能不够精确：{self.reason}"
        elif self.score >= 3:
            return f"⚠️ [{self.ref_num}] 引用存疑：{self.reason}"
        else:
            return f"❌ [{self.ref_num}] 引用可能捏造：{self.reason}"


# ═══════════════════════════════════════════════════
# Phase 2.3: Fuzzy 标题匹配（快速第一道防线）
# ═══════════════════════════════════════════════════

def _fuzzy_verify(
    ref_num: int,
    text: str,
    papers: list,
    threshold: float = 0.6,
) -> CitationVerifyResult | None:
    """用 difflib 进行 fuzzy 标题匹配，快速判断引用是否合理

    如果正文[ref_num]周围提到了论文标题的关键词，可以高置信度通过。
    fuzzy 无法判定时返回 None，交给 LLM 深度验证。

    Args:
        ref_num: 引用编号（1-indexed）
        text: 全文（用于提取引用上下文的更大窗口）
        papers: 文献列表
        threshold: 相似度阈值，超过此值认为 fuzzy 匹配成功
    """
    if ref_num < 1 or ref_num > len(papers):
        return CitationVerifyResult(ref_num, 0, "引用编号超出文献范围", False,
                                     method="range_only")

    paper = papers[ref_num - 1]
    title = getattr(paper, 'title', '') or ''
    abstract = getattr(paper, 'abstract', '') or ''
    if not title:
        return None  # 无法 fuzzy 验证

    # 提取引用周围更大的上下文（前后各 200 字符）
    ref_pattern = re.compile(r'\[' + str(ref_num) + r'\]')
    m = ref_pattern.search(text)
    if not m:
        return None  # 没找到引用标记

    start = max(0, m.start() - 200)
    end = min(len(text), m.end() + 200)
    context = text[start:end]

    # 1) 全文 title fuzzy match
    title_ratio = difflib.SequenceMatcher(None, context.lower(), title.lower()).ratio()

    # 2) 分词匹配（标题中较长的连续片段出现在上下文中）
    title_words = re.split(r'[\s\-:]+', title.lower())
    word_matches = sum(1 for w in title_words if len(w) > 3 and w in context.lower())
    word_ratio = word_matches / max(len(title_words), 1)

    # 3) 抽象关键句匹配
    abstract_ratio = 0.0
    if abstract and len(abstract) > 30:
        # 取摘要前两句
        first_sentences = '. '.join(abstract.split('. ')[:2]).lower()
        abstract_ratio = difflib.SequenceMatcher(None, context.lower(), first_sentences).ratio()

    # 综合判断
    if title_ratio > 0.7 or word_ratio > 0.4:
        score = min(10, round(8 + title_ratio * 3))
        trace_parts = []
        if title_ratio > 0.7:
            trace_parts.append(f"标题匹配 {round(title_ratio*100)}%")
        if word_ratio > 0.4:
            trace_parts.append(f"关键词覆盖 {round(word_ratio*100)}%")
        return CitationVerifyResult(
            ref_num, score,
            f"引用上下文与文献标题高度吻合",
            True,
            trace=", ".join(trace_parts),
            method="fuzzy"
        )

    # 模糊不确定
    if abstract_ratio > 0.5:
        return CitationVerifyResult(
            ref_num, 6,
            f"引用上下文与文献摘要部分吻合（{round(abstract_ratio*100)}%）",
            True,
            trace=f"Abstract 匹配度 {round(abstract_ratio*100)}%",
            method="fuzzy"
        )

    # Fuzzy 无法判定，返回 None 交给 LLM
    return None


# ═══════════════════════════════════════════════════
# 批量验证（Phase 2.3 增强版：Fuzzy 优先 + LLM 补充）
# ═══════════════════════════════════════════════════

async def verify_citations(
    text: str,
    papers: list,
    llm: Callable[[str], Awaitable[str]],
    max_papers: int = 20,
) -> list[CitationVerifyResult]:
    """
    批量深度验证正文中的所有 [n] 引用

    Phase 2.3: 先用 fuzzy 快速筛出明确 pass/fail 的，剩余交给 LLM

    Args:
        text: 论文正文（含 [n] 引用标记）
        papers: PaperCard 列表
        llm: 异步 LLM 调用函数
        max_papers: 最多验证前 N 篇文献

    Returns:
        list[CitationVerifyResult]: 验证结果列表
    """
    # Step 1: 提取所有引用编号
    ref_pattern = re.compile(r'\[(\d+)\]')
    citations = {}  # ref_num → list of (context_before, context_after)
    
    for m in ref_pattern.finditer(text):
        num = int(m.group(1))
        if num < 1 or num > len(papers):
            continue
        start = max(0, m.start() - 80)
        end = min(len(text), m.end() + 80)
        context_before = text[start:m.start()].strip()
        context_after = text[m.end():end].strip()
        
        if num not in citations:
            citations[num] = []
        citations[num].append((context_before, context_after))
    
    if not citations:
        return []

    target_nums = sorted(citations.keys())[:max_papers]

    # Step 2: Fuzzy 优先验证（快速、零 token）
    verified: dict[int, CitationVerifyResult] = {}  # ref_num → 已验证
    llm_needed: set[int] = set()

    for n in target_nums:
        result = _fuzzy_verify(n, text, papers)
        if result is not None:
            verified[n] = result
        else:
            llm_needed.add(n)

    fuzzy_count = len(verified)
    if fuzzy_count > 0:
        logger.info(
            f"[ScholarForge] Citation verify: fuzzy matched {fuzzy_count}/{len(target_nums)}, "
            f"{len(llm_needed)} need LLM"
        )

    # Step 3: LLM 深度验证剩余（仅对 fuzzy 无法判定的）
    if llm_needed:
        llm_needed_sorted = sorted(llm_needed)
        paper_descriptions = []
        for n in llm_needed_sorted:
            p = papers[n - 1]
            authors = ", ".join(p.authors[:3]) if hasattr(p, 'authors') and p.authors else "未知作者"
            year = p.year if hasattr(p, 'year') and p.year else "未知年份"
            abstract = (p.abstract or "")[:300] if hasattr(p, 'abstract') else ""
            title = p.title if hasattr(p, 'title') else ""
            paper_descriptions.append(
                f"[{n}] {title}\n    作者: {authors}, {year}\n    摘要: {abstract}"
            )

        citation_contexts = []
        for n in llm_needed_sorted:
            before, after = citations[n][0]
            citation_contexts.append(f"  [{n}]: ...{before}[{n}]{after}...")

        prompt = f"""你是一个严格的学术审稿人。请逐一验证以下引用是否准确支撑了所对应的断论。

对每个引用，判断：
- 10分：论文摘要明确讨论了该断论，引用完全准确
- 7-9分：论文主题相关，但摘要细节与断论不完全匹配
- 3-6分：论文主题勉强相关，但断论可能超出了论文讨论范围
- 0-2分：论文讨论的是完全不同的话题，引用可能捏造

【文献库】
{chr(10).join(paper_descriptions)}

【引用上下文】(...内是引用出现的正文)
{chr(10).join(citation_contexts)}

只回复 JSON 格式（不要其他文字）：
{{"results":[{{"ref":1,"score":8,"reason":"论文明确讨论了该主题","trace":"Abstract 提到..."}},{{"ref":2,"score":4,"reason":"论文主题相关但断论过度泛化","trace":""}}]}}"""

        try:
            response = await llm(prompt)
            response = response.strip()
            if response.startswith("```"):
                response = re.sub(r'^```(?:json)?\s*', '', response)
                response = re.sub(r'\s*```$', '', response)

            data = json.loads(response)
            for item in data.get("results", []):
                ref = item.get("ref", 0)
                score = item.get("score", 5)
                reason = item.get("reason", "")
                trace = item.get("trace", "")
                verified[ref] = CitationVerifyResult(
                    ref_num=ref,
                    score=score,
                    reason=reason,
                    accurate=score >= 7,
                    trace=trace,
                    method="llm",
                )

        except json.JSONDecodeError as e:
            logger.warning(f"[ScholarForge] Citation verify LLM JSON parse failed: {e}")
        except Exception as e:
            logger.error(f"[ScholarForge] Citation verify LLM failed: {e}")

    # 确保 llm_needed 但 LLM 失败的有兜底
    for n in llm_needed:
        if n not in verified:
            verified[n] = CitationVerifyResult(
                n, 5, "(LLM 验证失败，默认为中等)", True,
                method="fallback"
            )

    # 按编号排序返回
    return [verified[n] for n in target_nums if n in verified]


def embed_warnings_in_text(text: str, verify_results: list[CitationVerifyResult]) -> tuple[str, int, int]:
    """
    将验证警告嵌入正文

    Returns:
        (annotated_text, warning_count, error_count)
    """
    if not verify_results:
        return text, 0, 0

    warnings = [r for r in verify_results if 3 <= r.score < 7]
    errors = [r for r in verify_results if r.score < 3]

    report_lines = ["\n\n---\n### 📋 引用验证报告\n"]

    if errors:
        report_lines.append(f"\n**❌ 高风险引用（{len(errors)} 处）：**\n")
        for r in errors:
            trace_info = f" — {r.trace}" if r.trace else ""
            report_lines.append(f"- [{r.ref_num}] 分数 {r.score}/10：{r.reason}{trace_info} [{r.method}]")

    if warnings:
        report_lines.append(f"\n**⚠️ 需核实引用（{len(warnings)} 处）：**\n")
        for r in warnings:
            trace_info = f" — {r.trace}" if r.trace else ""
            report_lines.append(f"- [{r.ref_num}] 分数 {r.score}/10：{r.reason}{trace_info} [{r.method}]")

    if not errors and not warnings:
        report_lines.append(f"\n✅ 所有 {len(verify_results)} 处引用验证通过（Fuzzy:"
                           f" {sum(1 for r in verify_results if r.method == 'fuzzy')},"
                           f" LLM: {sum(1 for r in verify_results if r.method == 'llm')}）\n")

    annotated = text + "\n".join(report_lines)
    return annotated, len(warnings), len(errors)
