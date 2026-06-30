"""
ScholarForge Paper Scoring System — LLM 驱动的论文多维评分

三个评分维度：
  1. 原创性 (originality) 0-10 — 观点是否独立、有无新洞察
  2. 逻辑性 (logic) 0-10 — 结构是否严密、论证是否连贯
  3. 引用完整性 (citation_completeness) 0-10 — 引用是否恰当、文献覆盖是否充分

综合评分 = originality×0.3 + logic×0.35 + citation×0.35
"""
import json
import logging
import re
from typing import Optional

logger = logging.getLogger("scholarforge.scoring")


async def score_paper(
    content: str,
    papers: list,
    _make_llm=None,
    topic: str = "",
) -> dict:
    """对论文内容进行 LLM 评分

    Args:
        content: 论文正文（Markdown 格式）
        papers: 文献列表（PaperCard / PaperResult 列表）
        _make_llm: LLM 工厂函数（需返回 async callable）
        topic: 研究主题（可选，用于原创性判断上下文）

    Returns:
        {
            "originality": {"score": 0-10, "reasoning": "..."},
            "logic": {"score": 0-10, "reasoning": "..."},
            "citation_completeness": {"score": 0-10, "reasoning": "..."},
            "overall": 0-10,
            "overall_reasoning": "...",
        }
    """
    if not _make_llm:
        return _fallback_score(content, papers)

    # 构建文献上下文
    papers_text = ""
    if papers:
        papers_text = "已引用文献：\n" + "\n".join([
            f"[{i+1}] {getattr(p, 'title', str(p))} "
            f"({', '.join(getattr(p, 'authors', [])) if hasattr(p, 'authors') and isinstance(getattr(p, 'authors'), list) else ''}, "
            f"{getattr(p, 'year', '')})"
            for i, p in enumerate(papers[:15])
        ])

    # 截断内容以防过长
    content_preview = content[:6000] if len(content) > 6000 else content
    if len(content) > 6000:
        content_preview += "\n\n[... 后续内容已省略 ...]"

    topic_hint = f"\n\n研究主题：{topic}" if topic else ""

    prompt = f"""你是一个严格的学术论文评审专家。请对以下论文内容进行三维度评估。

## 论文内容
{content_preview}
{topic_hint}

## {papers_text}

## 评分要求

请从以下三个维度分别打分（0-10分，精确到0.1），并给出简要理由：

1. **原创性 (originality)**：观点是否独立？有无创新洞见？与现有研究区分度如何？
2. **逻辑性 (logic)**：结构是否严密？论证链条是否连贯？结论是否有说服力？
3. **引用完整性 (citation_completeness)**：引用是否恰当？关键论点是否有文献支撑？引用数量和质量如何？

## 输出格式

**严格按照以下 JSON 格式输出，不要输出其他内容：**

```json
{{
  "originality": {{
    "score": 7.5,
    "reasoning": "该论文提出了...但..."
  }},
  "logic": {{
    "score": 8.0,
    "reasoning": "论证结构清晰..."
  }},
  "citation_completeness": {{
    "score": 6.5,
    "reasoning": "引用了...但缺少..."
  }},
  "overall": 7.3,
  "overall_reasoning": "总体而言，本文..."
}}
```

综合评分公式：overall = originality×0.3 + logic×0.35 + citation×0.35

请开始评估。"""

    try:
        response = await _make_llm(prompt)

        # 提取 JSON
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            result = json.loads(json_match.group(0))
            return _validate_score_result(result)
        else:
            logger.warning("[ScholarForge Score] No JSON found in LLM response, using fallback")
            return _fallback_score(content, papers)

    except json.JSONDecodeError as e:
        logger.error(f"[ScholarForge Score] JSON parse error: {e}")
        return _fallback_score(content, papers)
    except Exception as e:
        logger.error(f"[ScholarForge Score] Error: {e}")
        return _fallback_score(content, papers)


def _validate_score_result(result: dict) -> dict:
    """验证并修正 LLM 输出的评分结果"""
    expected_keys = {
        "originality": {"score": 5.0, "reasoning": ""},
        "logic": {"score": 5.0, "reasoning": ""},
        "citation_completeness": {"score": 5.0, "reasoning": ""},
    }

    for key, defaults in expected_keys.items():
        if key not in result:
            result[key] = defaults
        elif not isinstance(result[key], dict):
            result[key] = defaults
        else:
            score = result[key].get("score", 5.0)
            if not isinstance(score, (int, float)):
                score = 5.0
            result[key]["score"] = round(max(0, min(10, float(score))), 1)
            if "reasoning" not in result[key]:
                result[key]["reasoning"] = ""

    # 计算综合评分
    o = result["originality"]["score"]
    l = result["logic"]["score"]
    c = result["citation_completeness"]["score"]
    result["overall"] = round(o * 0.3 + l * 0.35 + c * 0.35, 1)

    if "overall_reasoning" not in result:
        result["overall_reasoning"] = ""

    return result


def _fallback_score(content: str, papers: list) -> dict:
    """无 LLM 时的启发式评分"""
    # 内容长度作为参考
    content_len = len(content)

    # 原创性：基于内容长度和复杂度估算
    originality = min(8.0, max(3.0, content_len / 1000))

    # 逻辑性：基本检查
    has_sections = content.count("##") + content.count("###")
    logic = min(9.0, max(4.0, has_sections * 1.2))

    # 引用完整性
    ref_count = len(re.findall(r"\[\d+\]", content))
    paper_count = len(papers)
    citation = min(10.0, max(2.0, (ref_count + paper_count) * 0.5)) if paper_count > 0 else min(4.0, ref_count * 0.8)

    overall = round(originality * 0.3 + logic * 0.35 + citation * 0.35, 1)

    return {
        "originality": {
            "score": round(originality, 1),
            "reasoning": f"基于内容长度和复杂度估算（{content_len}字符）"
        },
        "logic": {
            "score": round(logic, 1),
            "reasoning": f"基于章节结构估算（{has_sections}个章节标记）"
        },
        "citation_completeness": {
            "score": round(citation, 1),
            "reasoning": f"检测到 {ref_count} 处引用标记，{paper_count} 篇文献"
        },
        "overall": overall,
        "overall_reasoning": "（无 LLM 的启发式评估，建议为 Agent 配置 API Key 以获得准确评分）",
    }


# ═══════════════════════════════════════════════════
# 共识度评分 (Phase 2.2 — Consensus Meter)
# ═══════════════════════════════════════════════════

async def score_consensus(
    claim: str,
    papers: list,
    llm=None,
) -> dict:
    """评估某一论断在多篇文献中的共识度

    类似 Consensus.app 的 Consensus Meter：
    给定一个论断（如"深度学习优于传统方法"），LLM 逐篇判断每篇文献
    对该论断的支持态度，统计支持/反对/中立的文献比例。
    """
    if not llm or not papers:
        return _fallback_consensus(claim, papers)

    paper_descs = []
    for i, p in enumerate(papers[:15], 1):
        title = getattr(p, 'title', '')[:150]
        abstract = (getattr(p, 'abstract', '') or '')[:250]
        paper_descs.append(f"[{i}] {title}\n    {abstract}")

    prompt = f"""你是学术审稿人。请逐篇判断以下文献对论断的支持态度。

论断："{claim}"

文献列表：
{chr(10).join(paper_descs)}

对每篇文献，判断：
- support: 文献明确支持或验证了该论断
- oppose: 文献结论与该论断矛盾
- neutral: 文献与该论断无关或未明确表态

只回复 JSON（不要其他文字）：
{{"results":[
  {{"ref":1,"stance":"support","reason":"论文实验证明..."}},
  {{"ref":2,"stance":"oppose","reason":"论文指出相反结论..."}},
  ...
]}}"""

    try:
        response = await llm(prompt)
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r'^```(?:json)?\s*', '', response)
            response = re.sub(r'\s*```$', '', response)

        data = json.loads(response)
        results = data.get("results", [])

        support = sum(1 for r in results if r.get("stance") == "support")
        oppose = sum(1 for r in results if r.get("stance") == "oppose")
        neutral = sum(1 for r in results if r.get("stance") == "neutral")
        total = support + oppose + neutral
        consensus_pct = round(support / total * 100, 1) if total > 0 else 50.0

        if consensus_pct >= 75:
            confidence = "high"
        elif consensus_pct >= 50:
            confidence = "medium"
        else:
            confidence = "low"

        return {
            "claim": claim,
            "support": support,
            "oppose": oppose,
            "neutral": neutral,
            "total": total,
            "consensus_pct": consensus_pct,
            "confidence": confidence,
            "per_paper": results,
        }

    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[ScholarForge] Consensus scoring failed: {e}")
        return _fallback_consensus(claim, papers)
    except Exception as e:
        logger.error(f"[ScholarForge] Consensus scoring error: {e}")
        return _fallback_consensus(claim, papers)


def _fallback_consensus(claim: str, papers: list) -> dict:
    """无 LLM 时的粗粒度共识估算"""
    n = len(papers)
    return {
        "claim": claim,
        "support": 0, "oppose": 0, "neutral": n,
        "total": n, "consensus_pct": 0.0,
        "confidence": "low", "per_paper": [],
    }


async def extract_key_claims(content: str, llm=None, max_claims: int = 5) -> list[str]:
    """从论文正文中提取关键论断（供共识度评分使用）"""
    if not llm:
        sentences = re.findall(r'[^。\n]+[。]', content[:3000])
        claims = []
        for s in sentences:
            if any(kw in s for kw in ['表明', '发现', '优于', '显著', '证明', '证实', '有效', '结论']):
                s = s.strip()[:120]
                if s not in claims:
                    claims.append(s)
        return claims[:max_claims]

    prompt = f"""从以下论文章节中提取 {max_claims} 个最核心的论断/主张。
要求：每行一个论断，只输出论断本身，简短（15字以内）。

论文内容：
{content[:4000]}"""

    try:
        resp = await llm(prompt)
        claims = []
        for line in resp.strip().split("\n"):
            line = line.strip()
            line = re.sub(r'^\d+[\.\)、]\s*', '', line)
            if line and len(line) > 2:
                claims.append(line[:150])
        return claims[:max_claims]
    except Exception as e:
        logger.error(f"[ScholarForge] extract_key_claims failed: {e}")
        return []
