"""
ScholarForge — 文献知识沉淀 (Literature Cards)

把 search 结果变成结构化卡片持久化累积，写综述时直接调矩阵视图。

两个公开函数：
- save_cards(papers) → 检索结果 → LLM 抽 7 字段 → 去重入库
- literature_matrix(topic) → SQL/TF-IDF 筛选 → Markdown 矩阵
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("scholarforge.literature_cards")

_SYS = (
    "你是文献元数据抽取器。只输出 JSON 数组，不要解释或 Markdown 代码块包裹。"
)

_PROMPT_TEMPLATE = """请从以下文献中抽取结构化信息。

【文献列表】
{papers_json}

对每篇文献，抽取以下 7 个字段：
- research_question: 该文献要回答的核心研究问题（一句话）
- methods: 使用的研究方法（简述，如"准实验设计+问卷"）
- datasets: 使用的数据集/数据来源（简述，如"PSCC 2020+自采问卷"）
- findings: 核心发现（1-2 句）
- limitations: 作者承认的局限或你判断的局限（1 句）
- key_claims: 该文献的关键主张（字符串数组，1-3 条）
- tags: 主题标签（字符串数组，1-3 个，如"LLM"、"教育"、"K-12"）

输出严格 JSON 数组（不要 ```json 包裹），每元素对应一篇文献，按上面顺序。
如果某字段无法从摘要中判断，填 "未明确"。
"""


async def save_cards(
    papers: list[dict[str, Any]],
) -> dict[str, int]:
    """把 search 结果存为结构化文献卡片。

    参数:
        papers: list[dict]，每条含 title/authors/year/venue/abstract/url/doi/pdf_url/source

    返回:
        {"added": N, "skipped": M, "total": T}
    """
    if not papers:
        return {"added": 0, "skipped": 0, "total": 0}

    from hermes_cli.scholarforge.database import get_conn, init_db
    from hermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

    # 1. 整批 LLM 抽取 7 字段
    papers_for_llm = []
    for p in papers:
        papers_for_llm.append({
            "title": p.get("title", ""),
            "abstract": (p.get("abstract", "") or "")[:1500],
        })

    prompt = _PROMPT_TEMPLATE.format(papers_json=json.dumps(papers_for_llm, ensure_ascii=False, indent=2))

    try:
        raw = await _call_llm(prompt, _SYS, temperature=0.2, model=ANALYSIS_MODEL)
    except Exception as e:
        logger.error("LLM call failed during save_cards: %s", e)
        raw = "[]"

    extracted = _parse_json_array(raw)
    # 对齐：如果 LLM 返回数量与输入不符，按 title 匹配或填默认
    while len(extracted) < len(papers):
        extracted.append({})

    # 2. 去重 + 入库
    init_db()
    added = 0
    skipped = 0
    now = int(__import__("time").time())

    with get_conn() as conn:
        for i, p in enumerate(papers):
            title = (p.get("title", "") or "").strip()
            doi = (p.get("doi", "") or "").strip()
            if not title:
                skipped += 1
                continue

            # 去重：doi 归一后查，否则 title lower+strip 查
            if doi:
                exists = conn.execute(
                    "SELECT id FROM literature_cards WHERE doi=?", (doi,)
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT id FROM literature_cards WHERE LOWER(TRIM(title))=LOWER(TRIM(?))",
                    (title,),
                ).fetchone()

            if exists:
                skipped += 1
                continue

            ext = extracted[i] if i < len(extracted) else {}
            authors = p.get("authors", [])
            if isinstance(authors, list):
                authors = json.dumps(authors, ensure_ascii=False)

            try:
                conn.execute("""
                    INSERT INTO literature_cards
                    (title, authors, year, venue, doi, url, pdf_url, source,
                     abstract, research_question, methods, datasets, findings,
                     limitations, key_claims, tags, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    title, authors,
                    str(p.get("year", "") or ""),
                    p.get("venue", "") or "",
                    doi,
                    p.get("url", "") or "",
                    p.get("pdf_url", "") or "",
                    p.get("source", "") or "",
                    p.get("abstract", "") or "",
                    ext.get("research_question", "未明确"),
                    ext.get("methods", "未明确"),
                    ext.get("datasets", "未明确"),
                    ext.get("findings", "未明确"),
                    ext.get("limitations", "未明确"),
                    json.dumps(ext.get("key_claims", []), ensure_ascii=False),
                    json.dumps(ext.get("tags", []), ensure_ascii=False),
                    now,
                ))
                added += 1
            except Exception as e:
                logger.warning("Failed to insert card for '%s': %s", title[:50], e)
                skipped += 1

    return {"added": added, "skipped": skipped, "total": added + skipped}


async def save_cards_from_query(
    query: str,
    limit: int = 10,
) -> dict[str, int]:
    """触发 search → 沉淀为卡片（便捷入口）"""
    if not query.strip():
        return {"added": 0, "skipped": 0, "total": 0}

    from hermes_cli.scholarforge.search import search_papers

    papers = []
    async for r in search_papers(query, limit=limit):
        papers.append(r.to_dict())

    return await save_cards(papers)


def literature_matrix(
    topic: str = "",
    tag: str = "",
    limit: int = 30,
) -> str:
    """从已沉淀的卡片中生成综述矩阵。

    参数:
        topic: 可选，用 TF-IDF 对 abstract 做语义排序
        tag: 可选，SQL LIKE 过滤 tags 字段
        limit: 最多返回条数

    返回:
        Markdown 矩阵 + gap 提示
    """
    from hermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    with get_conn() as conn:
        if tag:
            rows = conn.execute(
                """SELECT * FROM literature_cards
                   WHERE tags LIKE ? OR tags LIKE ? OR tags LIKE ?
                   ORDER BY added_at DESC LIMIT ?""",
                (f'["{tag}"]', f'%"{tag}"%', f'%"{tag}",%', limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM literature_cards ORDER BY added_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    if not rows:
        return "ℹ️ 文献卡片库为空。请先用 `scholarforge_save_literature_cards` 沉淀文献。"

    cards = [dict(r) for r in rows]

    # 如果有 topic，用 TF-IDF 重排
    if topic.strip():
        try:
            from hermes_cli.scholarforge.rag import PaperRetriever

            retriever = PaperRetriever()
            retriever.index(cards)
            hits = retriever.search(topic, top_k=min(len(cards), limit))
            ordered = []
            seen = set()
            for paper, score in hits:
                if id(paper) not in seen:
                    ordered.append(paper)
                    seen.add(id(paper))
            if ordered:
                cards = ordered[:limit]
        except Exception as e:
            logger.warning("TF-IDF rerank failed, using default order: %s", e)

    return _format_matrix(cards, topic or tag or "全部")


def _format_matrix(cards: list[dict], label: str) -> str:
    """格式化为 Markdown 综述矩阵 + gap 提示"""
    lines = [
        f"## 📊 文献综述矩阵：{label}",
        f"（共 {len(cards)} 篇）",
        "",
    ]

    # 收集所有 limitations 用于 gap 分析
    all_limitations = []

    for i, c in enumerate(cards, 1):
        # 解析 JSON 字段
        try:
            claims = json.loads(c.get("key_claims", "[]"))
        except Exception:
            claims = []
        try:
            tags = json.loads(c.get("tags", "[]"))
        except Exception:
            tags = []

        authors_str = c.get("authors", "")
        try:
            authors_list = json.loads(authors_str) if authors_str else []
            authors_str = ", ".join(authors_list[:3])
        except Exception:
            pass

        lines += [
            f"### {i}. {c.get('title', '无标题')}",
            f"- **作者**: {authors_str or '未知'}",
            f"- **年份**: {c.get('year', '未知')} | **期刊**: {c.get('venue', '未知')} | **来源**: {c.get('source', '')}",
            f"- **DOI**: {c.get('doi', '无') or '无'}",
            f"- **研究问题**: {c.get('research_question', '未明确')}",
            f"- **方法**: {c.get('methods', '未明确')}",
            f"- **数据**: {c.get('datasets', '未明确')}",
            f"- **发现**: {c.get('findings', '未明确')}",
            f"- **局限**: {c.get('limitations', '未明确')}",
        ]
        if claims:
            lines.append(f"- **关键主张**: {' / '.join(claims)}")
        if tags:
            lines.append(f"- **标签**: {', '.join(tags)}")
        lines.append("")

        lim = c.get("limitations", "")
        if lim and lim != "未明确":
            all_limitations.append(lim)

    # Gap 提示
    if all_limitations:
        lines += ["### 🔍 潜在研究空白（从局限中提炼）", ""]
        # 简单聚合：相同的 limitation 关键词出现多次
        from collections import Counter
        words = []
        for lim in all_limitations:
            # 提取关键词（中文按字分词不够好，这里简单按标点拆）
            for part in re.split(r"[，,；;。]", lim):
                part = part.strip()
                if 2 <= len(part) <= 20:
                    words.append(part)
        common = Counter(words).most_common(5)
        for word, count in common:
            if count >= 2:
                lines.append(f"- **{word}**（{count} 篇文献提及）→ 可能是研究空白")
        if not any(c >= 2 for _, c in common):
            lines.append("（各文献局限点较分散，未发现明显聚集的空白）")
        lines.append("")

    return "\n".join(lines)


def _parse_json_array(raw: str) -> list[dict]:
    """从 LLM 响应中提取 JSON 数组，抗格式漂移。"""
    # 优先 ```json 块
    m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        # 裸 [ ... ]
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if not m:
            return []
        raw = m.group(0)

    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse literature_cards JSON array: %s", e)
        return []
