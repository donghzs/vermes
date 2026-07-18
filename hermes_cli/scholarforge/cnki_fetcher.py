"""
CNKI 知网文献抓取器 — 多策略论文检索
P0-1: 将知网源从空壳升级为真实可用

策略优先级（自动降级）：
1. 用户自建网关 (CNKI_GATEWAY_URL + CNKI_API_KEY) — 最稳定
2. 万方数据 API (WANFANG_API_KEY) — 备选路径  
3. OpenAlex 中文学术映射 — 免费回退（数据不如知网，但有中文论文）

提供统一的 PaperResult 返回，对上游透明
"""
import asyncio
from agent.service_credentials import get_api_key, get_service_credentials, register_service
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("scholarforge.cnki")


@dataclass
class CnkiPaper:
    """知网风格论文记录"""
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    abstract: str = ""
    cited_count: int = 0
    url: str = ""
    doi: str = ""
    keywords: list[str] = field(default_factory=list)
    source: str = "cnki"


async def _fetch_via_gateway(query: str, limit: int = 20) -> list[CnkiPaper]:
    """策略1: 通过用户自建 CNKI 网关"""
    gateway_url = os.environ.get("CNKI_GATEWAY_URL", "").strip()
    api_key = (get_api_key("cnki") or "").strip()
    if not gateway_url:
        return []

    import httpx
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{gateway_url.rstrip('/')}/search",
                json={"keyword": query, "limit": min(limit, 30)},
                headers=headers, timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"CNKI gateway returned {resp.status_code}")
                return []

        data = resp.json()
        items = data.get("results") or (data.get("data", {}) or {}).get("list", [])
        papers = []
        for item in items:
            title = item.get("title") or item.get("name", "")
            if not title:
                continue
            authors = item.get("authors") or item.get("author", [])
            if isinstance(authors, str):
                authors = [a.strip() for a in authors.split(";") if a.strip()]
            papers.append(CnkiPaper(
                title=title,
                authors=authors,
                year=str(item.get("year") or item.get("publish_year", "")),
                journal=item.get("journal") or item.get("source", ""),
                abstract=(item.get("abstract") or item.get("summary", ""))[:500],
                cited_count=item.get("cited_count", 0) or 0,
                url=item.get("url", ""),
                doi=item.get("doi", ""),
                keywords=item.get("keywords", []),
                source="cnki",
            ))
        return papers
    except Exception as e:
        logger.error(f"CNKI gateway error: {e}")
        return []


async def _fetch_via_wanfang(query: str, limit: int = 20) -> list[CnkiPaper]:
    """策略2: 万方数据 API

    万方开放 API 申请地址: https://dev.wanfangdata.com.cn/
    免费额度: 100次/天
    """
    api_key = (get_api_key("wanfang") or "").strip()
    if not api_key:
        return []

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.wanfangdata.com.cn/v1/search/paper",
                params={"q": query, "size": min(limit, 20), "page": 1},
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"Wanfang returned {resp.status_code}")
                return []

        data = resp.json()
        items = data.get("data", {}).get("results", [])
        papers = []
        for item in items:
            title = item.get("title", "")
            if not title:
                continue
            authors = item.get("authors", [])
            if isinstance(authors, str):
                authors = [a.strip() for a in authors.split(";") if a.strip()]
            papers.append(CnkiPaper(
                title=title,
                authors=authors,
                year=str(item.get("year", "")),
                journal=item.get("journal", {}).get("name", "") if isinstance(item.get("journal"), dict) else item.get("journal", ""),
                abstract=(item.get("abstract", ""))[:500],
                cited_count=int(item.get("cited_count", 0)),
                url=item.get("url", ""),
                doi=item.get("doi", ""),
                keywords=item.get("keywords", []),
                source="wanfang",
            ))
        return papers
    except Exception as e:
        logger.error(f"Wanfang error: {e}")
        return []


async def _fetch_via_openalex_cn(query: str, limit: int = 20) -> list[CnkiPaper]:
    """策略3: OpenAlex 中文学术论文映射

    OpenAlex 索引了来自 Crossref/Datacite 的中文论文元数据。
    虽不如知网丰富，但免费、稳定、可做兜底。
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.openalex.org/works",
                params={
                    "search": query,
                    "filter": "language:zh,type:article",
                    "per_page": min(limit, 25),
                    "sort": "cited_by_count:desc",
                },
                timeout=15,
            )
            if resp.status_code != 200:
                logger.warning(f"OpenAlex CN returned {resp.status_code}")
                return []

        data = resp.json()
        papers = []
        for w in data.get("results", []):
            title = w.get("title", "")
            if not title:
                continue
            authors_raw = w.get("authorships", [])
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in authors_raw if a.get("author", {}).get("display_name")
            ]
            year = str(w.get("publication_year", ""))
            # 期刊名
            venue = ""
            if w.get("primary_location") and w["primary_location"].get("source"):
                venue = w["primary_location"]["source"].get("display_name", "")
            # 摘要 — OpenAlex 有 indexed_abstract (倒排索引，需要重构)
            abstract = ""
            ia = w.get("abstract_inverted_index")
            if ia:
                try:
                    # 重构倒排索引 → 原文
                    word_positions: dict[int, str] = {}
                    for word, positions in ia.items():
                        for pos in positions:
                            word_positions[pos] = word
                    abstract = " ".join(word_positions[k] for k in sorted(word_positions))[:500]
                except Exception:
                    pass

            papers.append(CnkiPaper(
                title=title,
                authors=authors,
                year=year,
                journal=venue,
                abstract=abstract,
                cited_count=w.get("cited_by_count", 0) or 0,
                url=w.get("doi", "") and f"https://doi.org/{w['doi'].lstrip('https://doi.org/')}" or "",
                doi=w.get("doi", ""),
                keywords=[k.get("display_name", "") for k in w.get("keywords", [])[:5] if k.get("display_name")],
                source="openalex-cn",
            ))
        return papers
    except Exception as e:
        logger.error(f"OpenAlex CN error: {e}")
        return []


async def search_cnki(query: str, limit: int = 20) -> list[CnkiPaper]:
    """多策略知网搜索 — 自动降级

    策略1→2→3，拿到结果就返回（不限 >0 即视为有效）
    """
    # 策略1: 用户自建网关
    papers = await _fetch_via_gateway(query, limit)
    if papers:
        logger.info(f"CNKI gateway: {len(papers)} results for '{query}'")
        return papers

    # 策略2: 万方 API
    papers = await _fetch_via_wanfang(query, limit)
    if papers:
        logger.info(f"Wanfang: {len(papers)} results for '{query}'")
        return papers

    # 策略3: OpenAlex 中文兜底
    papers = await _fetch_via_openalex_cn(query, limit)
    if papers:
        logger.info(f"OpenAlex CN: {len(papers)} results for '{query}'")
        return papers

    logger.warning(f"No CNKI results for '{query}' via any strategy")
    return []

register_service("cnki", api_key_env_var="CNKI_API_KEY", label="CNKI")

register_service("wanfang", api_key_env_var="WANFANG_API_KEY", label="Wanfang")
