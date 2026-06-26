"""
ScholarForge 文献搜索模块 - 多源聚合
完全独立于 Vermes 核心，通过 Blueprint 注册

免费源：arXiv, Crossref, Semantic Scholar
付费源：通过 PaidSearchAPI 基类扩展
"""
import asyncio
import logging
from typing import AsyncGenerator, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("scholarforge.search")


@dataclass
class PaperResult:
    """统一论文结果格式"""
    paper_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    abstract: str = ""
    citation_count: int = 0
    url: str = ""
    source: str = ""
    doi: str = ""
    pdf_url: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "abstract": self.abstract,
            "citations": self.citation_count,
            "url": self.url,
            "source": self.source,
            "pdf_url": self.pdf_url,
            "doi": self.doi,
        }


# 注册表：搜索源名称 → 搜索函数
_SEARCH_SOURCES: dict[str, Callable] = {}


def register_search_source(name: str, func: Callable):
    """注册新的搜索源"""
    _SEARCH_SOURCES[name] = func
    logger.info(f"[ScholarForge] Registered search source: {name}")


def get_available_sources() -> list[str]:
    return list(_SEARCH_SOURCES.keys())


async def search_papers(
    query: str,
    limit: int = 10,
    sources: list[str] | None = None,
    timeout: float = 15.0
) -> AsyncGenerator[PaperResult, None]:
    """
    多源聚合搜索，流式产出结果（去重）
    """
    if not sources:
        sources = list(_SEARCH_SOURCES.keys())

    tasks = []
    for src in sources:
        if src in _SEARCH_SOURCES:
            tasks.append(_search_with_timeout(src, query, limit, timeout))

    seen_titles = set()
    results_queue = asyncio.Queue()

    async def collector():
        for task in asyncio.as_completed(tasks):
            try:
                papers = await task
                for p in papers:
                    title_key = p.title.lower().strip()[:50]
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        await results_queue.put(p)
            except Exception as e:
                logger.warning(f"[ScholarForge] Source failed: {e}")
        await results_queue.put(None)

    collector_task = asyncio.create_task(collector())

    count = 0
    while True:
        paper = await results_queue.get()
        if paper is None or count >= limit * len(sources):
            break
        yield paper
        count += 1

    await collector_task


async def _search_with_timeout(source: str, query: str, limit: int, timeout: float) -> list[PaperResult]:
    try:
        return await asyncio.wait_for(
            _SEARCH_SOURCES[source](query, limit),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"[ScholarForge] {source} timeout")
        return []


# ====================
# 内置免费源实现
# ====================

async def _search_arxiv(query: str, limit: int = 10) -> list[PaperResult]:
    """arXiv 搜索 - 预印本，计算机/物理/数学"""
    import httpx
    from xml.etree import ElementTree as ET

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://export.arxiv.org/api/query", params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }, timeout=10)
            resp.raise_for_status()

        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

        results = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").replace("\n", " ").strip()
            summary = (entry.findtext("atom:summary", "", ns) or "").replace("\n", " ").strip()

            authors = []
            for author in entry.findall("atom:author", ns):
                name = author.findtext("atom:name", "", ns)
                if name:
                    authors.append(name)

            published = entry.findtext("atom:published", "", ns) or ""
            year = published[:4]

            arxiv_id = ""
            for id_elem in entry.findall("atom:id", ns):
                if id_elem.text:
                    arxiv_id = id_elem.text.split("/")[-1]

            pdf_url = ""
            for link in entry.findall("atom:link", ns):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href", "")

            results.append(PaperResult(
                paper_id=f"arxiv:{arxiv_id}",
                title=title,
                authors=authors,
                year=year,
                venue="arXiv",
                abstract=summary[:500] + ("..." if len(summary) > 500 else ""),
                url=f"https://arxiv.org/abs/{arxiv_id}",
                source="arxiv",
                pdf_url=pdf_url,
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] arXiv search failed: {e}")
        return []


async def _search_crossref(query: str, limit: int = 10) -> list[PaperResult]:
    """Crossref 搜索 - 开放获取，DOI 权威"""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.crossref.org/works", params={
                "query": query,
                "rows": limit,
                "select": "title,author,abstract,published-print,container-title,is-referenced-by-count,DOI,URL",
            }, timeout=10, headers={
                "User-Agent": "ScholarForge/0.1.0 (mailto:contact@scholarforge.ai)"
            })
            resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("message", {}).get("items", []):
            title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else str(item.get("title", ""))

            authors = []
            for a in item.get("author", []):
                name = f"{a.get('given', '')} {a.get('family', '')}".strip()
                if name:
                    authors.append(name)

            year = ""
            if item.get("published-print"):
                year = str(item["published-print"].get("date-parts", [[""]])[0][0])
            elif item.get("published-online"):
                year = str(item["published-online"].get("date-parts", [[""]])[0][0])

            venue = ""
            if item.get("container-title"):
                venue = item["container-title"][0] if isinstance(item["container-title"], list) else str(item["container-title"])

            abstract = (item.get("abstract") or "")[:500]

            results.append(PaperResult(
                paper_id=f"crossref:{item.get('DOI', '')}",
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                citation_count=item.get("is-referenced-by-count", 0) or 0,
                url=item.get("URL", f"https://doi.org/{item.get('DOI', '')}"),
                source="crossref",
                doi=item.get("DOI", ""),
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] Crossref search failed: {e}")
        return []


async def _search_semantic_scholar(query: str, limit: int = 10) -> list[PaperResult]:
    """Semantic Scholar - AI 驱动学术搜索（有 rate limit）"""
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params={
                "query": query,
                "limit": limit,
                "fields": "title,abstract,authors,year,venue,citationCount,externalIds,url",
            }, timeout=10)

            if resp.status_code == 429:
                logger.warning("[ScholarForge] Semantic Scholar rate limited (429)")
                return []
            resp.raise_for_status()

        data = resp.json()
        results = []
        for paper in data.get("data", []):
            authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
            results.append(PaperResult(
                paper_id=f"s2:{paper.get('paperId', '')}",
                title=paper.get("title", ""),
                authors=authors,
                year=str(paper.get("year", "")),
                venue=paper.get("venue", ""),
                abstract=(paper.get("abstract") or "")[:500],
                citation_count=paper.get("citationCount", 0) or 0,
                url=paper.get("url", ""),
                source="semantic_scholar",
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] Semantic Scholar search failed: {e}")
        return []


# 注册内置源
register_search_source("arxiv", _search_arxiv)
register_search_source("crossref", _search_crossref)
register_search_source("semantic_scholar", _search_semantic_scholar)
