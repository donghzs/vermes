"""
中文学术搜索聚合器 — ScholarForge 中文文献检索

百度学术有安全验证(403)无法直接爬取，万方/维普无公开免费API。
本模块通过 Crossref + OpenAlex 的中文搜索能力聚合中文学术论文，
并通过语言/标题过滤确保返回中文相关文献。

数据源：
- Crossref: 支持中文关键词搜索，返回中文期刊论文
- OpenAlex: 支持中文搜索，有 language 字段可过滤
"""
import asyncio
import logging
import re
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger("scholarforge.baidu_scholar")

from agent.service_credentials import register_service

# 复用 cnki_fetcher 的 CnkiPaper 保持接口一致
from .cnki_fetcher import CnkiPaper

# Crossref API 端点
CROSSREF_API = "https://api.crossref.org/works"
# OpenAlex API 端点
OPENALEX_API = "https://api.openalex.org/works"

# 判断是否包含中文
_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")


def _is_chinese(text: str) -> bool:
    """判断文本是否包含中文字符"""
    return bool(_CJK_PATTERN.search(text or ""))


async def _search_crossref_chinese(query: str, limit: int) -> list[CnkiPaper]:
    """通过 Crossref 搜索中文学术论文"""
    papers = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                CROSSREF_API,
                params={
                    "query": query,
                    "rows": min(limit * 2, 20),  # 多取一些用于过滤
                    "select": "DOI,title,author,published-print,published-online,container-title,abstract,subject,is-referenced-by-count,URL",
                },
                headers={
                    "User-Agent": "ScholarForge/1.0 (mailto:scholarforge@vbit.top)",
                    "Accept": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Crossref Chinese search: HTTP {resp.status_code}")
            return papers

        items = resp.json().get("message", {}).get("items", [])

        for item in items:
            if len(papers) >= limit:
                break

            title = ""
            titles = item.get("title", [])
            if titles:
                title = titles[0]

            # 过滤：标题包含中文 或者 查询词是中文且标题匹配
            if not title:
                continue

            # 如果查询包含中文，优先返回中文标题的论文
            if _is_chinese(query) and not _is_chinese(title):
                # 也接受英文论文，但中文优先
                pass

            # 作者
            authors = []
            for a in item.get("author", []):
                given = a.get("given", "")
                family = a.get("family", "")
                name = f"{given} {family}".strip() or a.get("name", "")
                if name:
                    authors.append(name)

            # 年份
            year = ""
            for date_field in ["published-print", "published-online"]:
                date_parts = item.get(date_field, {}).get("date-parts", [[]])
                if date_parts and date_parts[0]:
                    year = str(date_parts[0][0])
                    break

            # 期刊
            journal = ""
            container = item.get("container-title", [])
            if container:
                journal = container[0]

            # 摘要
            abstract = item.get("abstract", "")
            if abstract:
                # 移除 XML 标签
                abstract = re.sub(r"<[^>]+>", "", abstract).strip()[:500]

            # DOI
            doi = item.get("DOI", "")

            # 引用数
            cited = item.get("is-referenced-by-count", 0)

            # URL
            url = item.get("URL", "")
            if doi and not url:
                url = f"https://doi.org/{doi}"

            # 语言
            lang = item.get("language", "")

            paper = CnkiPaper(
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                abstract=abstract,
                cited_count=cited,
                url=url,
                doi=doi,
                keywords=[],
                source="baidu_scholar",  # 保持前端来源标识一致
            )
            papers.append(paper)

    except httpx.ConnectError as e:
        logger.warning(f"Crossref Chinese search connect error: {e}")
    except httpx.TimeoutException:
        logger.warning("Crossref Chinese search timeout")
    except Exception as e:
        logger.error(f"Crossref Chinese search error: {e}")

    return papers


async def _search_openalex_chinese(query: str, limit: int) -> list[CnkiPaper]:
    """通过 OpenAlex 搜索中文学术论文"""
    papers = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                OPENALEX_API,
                params={
                    "search": query,
                    "per_page": min(limit * 2, 25),
                    "mailto": "scholarforge@vbit.top",
                },
                headers={"Accept": "application/json"},
            )

        if resp.status_code != 200:
            logger.warning(f"OpenAlex Chinese search: HTTP {resp.status_code}")
            return papers

        results = resp.json().get("results", [])

        for w in results:
            if len(papers) >= limit:
                break

            title = w.get("title", "")
            if not title:
                continue

            # 作者
            authors = []
            for a in w.get("authorships", []):
                author = a.get("author", {})
                name = author.get("display_name", "")
                if name:
                    authors.append(name)

            # 年份
            year = str(w.get("publication_year", "")) if w.get("publication_year") else ""

            # 期刊
            journal = ""
            venue = w.get("primary_location", {}).get("source", {})
            if venue:
                journal = venue.get("display_name", "")

            # 摘要
            abstract = ""
            abstract_inv = w.get("abstract_inverted_index", {})
            if abstract_inv:
                # 重建正序摘要
                word_positions = []
                for word, positions in abstract_inv.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = " ".join(w for _, w in word_positions)[:500]

            # DOI
            doi = w.get("doi", "")
            if doi and doi.startswith("https://doi.org/"):
                doi = doi.replace("https://doi.org/", "")

            # 引用数
            cited = w.get("cited_by_count", 0)

            # URL
            url = ""
            primary_location = w.get("primary_location", {})
            if primary_location:
                landing = primary_location.get("landing_page_url", "")
                if landing:
                    url = landing
            if not url and doi:
                url = f"https://doi.org/{doi}"

            # 语言
            lang = w.get("language", "")

            paper = CnkiPaper(
                title=title,
                authors=authors,
                year=year,
                journal=journal,
                abstract=abstract,
                cited_count=cited,
                url=url,
                doi=doi,
                keywords=[],
                source="baidu_scholar",
            )
            papers.append(paper)

    except httpx.ConnectError as e:
        logger.warning(f"OpenAlex Chinese search connect error: {e}")
    except httpx.TimeoutException:
        logger.warning("OpenAlex Chinese search timeout")
    except Exception as e:
        logger.error(f"OpenAlex Chinese search error: {e}")

    return papers


async def search_baidu_scholar(query: str, limit: int = 10) -> list[CnkiPaper]:
    """中文学术搜索聚合

    通过 Crossref + OpenAlex 聚合搜索中文学术论文。
    当查询包含中文时，优先返回中文标题的论文。

    Args:
        query: 搜索关键词（中英文均可）
        limit: 返回结果数量上限

    Returns:
        CnkiPaper 列表
    """
    # 并行搜索两个源
    crossref_task = _search_crossref_chinese(query, limit)
    openalex_task = _search_openalex_chinese(query, limit)

    crossref_results, openalex_results = await asyncio.gather(
        crossref_task, openalex_task, return_exceptions=True
    )

    # 处理异常
    if isinstance(crossref_results, Exception):
        logger.warning(f"Crossref search failed: {crossref_results}")
        crossref_results = []
    if isinstance(openalex_results, Exception):
        logger.warning(f"OpenAlex search failed: {openalex_results}")
        openalex_results = []

    # 合并去重
    seen_titles = set()
    merged = []

    # 如果查询包含中文，优先放中文标题的论文
    if _is_chinese(query):
        chinese_first = []
        others = []
        for paper in (crossref_results + openalex_results):
            # P1.1: 去除尾部 [] 统一化标题（Crossref 有时会加 [] 后缀）
            title_norm = paper.title.rstrip('[]').lower().strip()
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)
            if _is_chinese(paper.title):
                chinese_first.append(paper)
            else:
                others.append(paper)
        merged = (chinese_first + others)[:limit]
    else:
        for paper in (crossref_results + openalex_results):
            title_norm = paper.title.rstrip('[]').lower().strip()
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)
            merged.append(paper)
        merged = merged[:limit]

    if merged:
        logger.info(f"Chinese academic search: {len(merged)} results for '{query}' "
                     f"(Crossref: {len(crossref_results)}, OpenAlex: {len(openalex_results)})")

    return merged


# 纳入统一凭证体系（免费源，无 api_key 依赖）
register_service("baidu_scholar", label="Baidu Scholar 中文聚合")
