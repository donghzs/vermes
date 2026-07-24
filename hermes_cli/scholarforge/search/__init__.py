"""
ScholarForge 文献搜索模块 - 多源聚合
完全独立于 Vermes 核心，通过 Blueprint 注册

免费源：arXiv, Crossref, Semantic Scholar
付费源：通过 PaidSearchAPI 基类扩展
"""
import asyncio
from agent.service_credentials import get_api_key, get_service_credentials, register_service
import logging
import os
import re
import time
from typing import AsyncGenerator, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("scholarforge.search")

# 429 冷却追踪：source_name → 下次可用时间戳
_COOLDOWN_UNTIL: dict[str, float] = {}
_COOLDOWN_SECONDS = 300  # 5 分钟冷却


def _is_cooled_down(source_name: str) -> bool:
    """检查该源是否在 429 冷却中"""
    until = _COOLDOWN_UNTIL.get(source_name, 0)
    if until > time.time():
        return True
    # 冷却过期，清理
    if until:
        _COOLDOWN_UNTIL.pop(source_name, None)
    return False


def _set_cooldown(source_name: str):
    """标记该源进入 429 冷却"""
    _COOLDOWN_UNTIL[source_name] = time.time() + _COOLDOWN_SECONDS
    logger.info(f"[ScholarForge] {source_name} 进入 {_COOLDOWN_SECONDS}s 429 冷却")


def _query_is_chinese(text: str) -> bool:
    """判断查询是否包含中文字符（用于中文优先源路由）"""
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _select_default_sources(query: str) -> list[str]:
    """按查询语言选择默认搜索源（统一路由后覆盖 registry + 本地源）。

    统一路由后，默认源列表同时包含：
    - registry 内置源（openalex/crossref/pubmed/arxiv/semanticscholar/europepmc/doaj/core）
    - 本地 _SEARCH_SOURCES（baidu_scholar/cnki 等未入 registry 的源）

    中文查询优先 baidu_scholar + cnki；英文查询用全 registry 免费链。
    最后并入已激活的付费源（registry + 本地）。
    """
    # registry 免费源列表
    _registry_free = (
        "openalex", "crossref", "pubmed", "arxiv",
        "semanticscholar", "europepmc", "doaj", "core",
    )
    if _query_is_chinese(query):
        # 中文查询：优先中文源 baidu_scholar + cnki
        sources = ["baidu_scholar", "cnki"]
        # 中文查询排除的英文源（对中文返回无关结果）
        _chinese_excluded = {"crossref", "openalex", "doaj", "pubmed", "arxiv"}
        # 补入对中文有一定支持的 registry 源（语义学者等）
        sources.extend([s for s in _registry_free if s not in _chinese_excluded])
        # 补入本地 _SEARCH_SOURCES 中不重复的（同样排除中文不友好的源）
        for s in DEFAULT_SOURCE_CHAIN:
            if s not in sources and s not in _chinese_excluded and s in _SEARCH_SOURCES:
                sources.append(s)
    else:
        # 英文查询：registry 免费源 + 本地默认链
        sources = list(_registry_free)
        for s in DEFAULT_SOURCE_CHAIN:
            if s not in sources and s in _SEARCH_SOURCES:
                sources.append(s)
    # 加入已激活的本地付费源
    for src_name, entry in _PAID_SOURCE_REGISTRY.items():
        if entry.get("enabled") and src_name in _SEARCH_SOURCES and src_name not in sources:
            sources.append(src_name)
    # 加入 registry 中已配置凭证的付费源
    try:
        from agent.literature_registry import list_providers
        for p in list_providers():
            if p.name not in sources and p.name not in _registry_free and p.is_available():
                sources.append(p.name)
    except Exception:
        pass
    return sources


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


# ═══ 付费源激活存根 ═══
_PAID_SOURCE_REGISTRY: dict[str, dict] = {}  # source_name → {config, api_key, enabled}


async def get_paid_source_configs() -> list[dict]:
    """返回可用付费源的配置信息（不含 API Key）"""
    return [
        {
            "name": cfg["name"],
            "display_name": cfg["display_name"],
            "description": cfg["description"],
            "url": cfg["url"],
            "register_url": cfg.get("register_url", ""),
            "needs_gateway_url": cfg.get("needs_gateway_url", False),
            "requires_api_key": True,
            "enabled": _PAID_SOURCE_REGISTRY.get(cfg["name"], {}).get("enabled", False),
        }
        for cfg in _PAID_SOURCE_DEFINITIONS
    ]


async def activate_paid_source(
    source_name: str,
    api_key: str,
    gateway_url: str = "",
    username: str = "",
    password: str = "",
) -> bool:
    """激活付费文献源

    验证 API Key 有效性后启用该源。
    CNKI 源额外接受 gateway_url（自建网关）以及 username/password（卡密，走 Basic 认证）。

    R1 修复：把 UI 填的凭证同步桥接到环境变量，使 cnki_fetcher._fetch_via_gateway
    （只读 env / 统一凭证层）能真正拿到网站/卡号/卡密。
    """
    cfg = next((c for c in _PAID_SOURCE_DEFINITIONS if c["name"] == source_name), None)
    if not cfg:
        logger.warning(f"Unknown paid source: {source_name}")
        return False

    # 基本 Key 格式验证（允许仅有 username/password 的网关账号模式）
    if not api_key and not (username and password):
        logger.warning(f"[ScholarForge] Invalid credentials for {source_name}")
        return False

    entry = {
        "config": cfg,
        "api_key": api_key,
        "enabled": True,
    }
    # CNKI 特殊处理：需要 gateway_url
    if cfg.get("needs_gateway_url") and gateway_url:
        entry["gateway_url"] = gateway_url
    if username:
        entry["username"] = username
    if password:
        entry["password"] = password

    _PAID_SOURCE_REGISTRY[source_name] = entry

    # R1 修复：桥接到环境变量（cnki_fetcher 优先读统一凭证层，env 为兜底）
    if cfg.get("needs_gateway_url") and gateway_url:
        os.environ["CNKI_GATEWAY_URL"] = gateway_url
    if api_key:
        os.environ["CNKI_API_KEY"] = api_key
    if username:
        os.environ["CNKI_USERNAME"] = username
    if password:
        os.environ["CNKI_PASSWORD"] = password

    logger.info(f"[ScholarForge] Paid source activated: {source_name}")
    return True


# 付费源定义（用户在各平台注册后填写 API Key 即可接入）
_PAID_SOURCE_DEFINITIONS = [
    {
        "name": "cnki",
        "display_name": "CNKI 中国知网",
        "description": "中文学术论文最全库。知网无公开 API，需自建网关或使用第三方代理服务。填入网关 URL 和 API Key 后可用。",
        "url": "https://github.com/donghzs/cnki-gateway（开源网关参考）",
        "needs_gateway_url": True,
    },
    {
        "name": "scopus",
        "display_name": "Scopus",
        "description": "Elsevier 学术文献数据库，覆盖 7000+ 出版商。免费注册开发者账号获取 API Key。",
        "url": "https://dev.elsevier.com/",
        "register_url": "https://dev.elsevier.com/apikey/manage",
    },
    {
        "name": "web_of_science",
        "display_name": "Web of Science",
        "description": "Clarivate 科学引文索引，SCI/SSCI 核心文献全覆盖。需机构订阅或开发者账号。",
        "url": "https://developer.clarivate.com/apis/wos",
        "register_url": "https://developer.clarivate.com/",
    },
    {
        "name": "google_scholar",
        "display_name": "Google Scholar (SerpAPI)",
        "description": "通过 SerpAPI 接入 Google Scholar，支持中英文，覆盖面最广。免费额度 100次/月。",
        "url": "https://serpapi.com/google-scholar-api",
        "register_url": "https://serpapi.com/",
    },
]


async def search_papers(
    query: str,
    limit: int = 10,
    sources: list[str] | None = None,
    timeout: float = 15.0,
    min_results: int = 3,
    max_wait: float = 10.0,
) -> AsyncGenerator[PaperResult, None]:
    """
    多源聚合搜索，快速返回，流式产出结果（去重）

    统一路由：优先委托 :mod:`agent.literature_registry`（含 17 个内置源 +
    用户自定义源），覆盖 arXiv / Crossref / OpenAlex / PubMed / Semantic Scholar /
    DOAJ / CORE / Europe PMC / CNKI / 万方 / 维普 / Scopus / IEEE / WOS / Springer /
    ScienceDirect / EBSCO + 任意自定义机构内部文献库。

    如果 registry 中找不到某源（如百度学术 / Google Scholar），
    回退到本地 ``_SEARCH_SOURCES`` 实现。

    策略：
    1. 并发所有源，先完成的先出
    2. 到达 min_results 后，max_wait 秒内没新结果就停止
    3. 429 冷却源自动跳过
    4. 单源超时 8s，避免卡死
    """
    if not sources:
        sources = _select_default_sources(query)

    # ── 统一路由：先走 literature_registry，覆盖所有内置+自定义源 ──
    registry_papers = await _search_via_registry(query, limit, sources)
    if registry_papers:
        seen = set()
        for p in registry_papers:
            title_key = p.title.lower().strip()[:50]
            if title_key and title_key not in seen:
                seen.add(title_key)
                yield p
        return

    # ── 回退：本地 _SEARCH_SOURCES（百度学术 / Google Scholar 等未入 registry 的源） ──
    # 过滤已在 registry 处理的源
    local_sources = [s for s in sources if s in _SEARCH_SOURCES and not _is_cooled_down(s)]
    skipped = set(sources) - set(local_sources)
    if skipped:
        logger.debug(f"[ScholarForge] sources not in local _SEARCH_SOURCES: {skipped}")

    if not local_sources:
        logger.info("[ScholarForge] 无本地源可搜索（已由 registry 处理或全部冷却）")
        return

    per_source_timeout = min(timeout, 8.0)
    pending = set()
    for src in local_sources:
        task = asyncio.create_task(_search_with_timeout(src, query, limit, per_source_timeout))
        task._scholarforge_source = src
        pending.add(task)

    seen_titles = set()
    count = 0
    last_yield_at = time.time()
    deadline = time.time() + max_wait + 5

    while pending:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        done, pending = await asyncio.wait(
            pending, timeout=min(max(remaining, 0.5), 5.0), return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            src = getattr(task, '_scholarforge_source', 'unknown')
            try:
                papers = task.result()
                for p in papers:
                    title_key = p.title.lower().strip()[:50]
                    if title_key and title_key not in seen_titles:
                        seen_titles.add(title_key)
                        yield p
                        count += 1
                        last_yield_at = time.time()
                        if count >= limit:
                            for t in pending:
                                t.cancel()
                            return
            except asyncio.TimeoutError:
                logger.warning(f"[ScholarForge] {src} timeout")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(f"[ScholarForge] {src} failed: {e}")

        if count >= min_results and time.time() - last_yield_at > max_wait:
            logger.info(f"[ScholarForge] {count}结果 {max_wait}s无新，提前返回")
            for t in pending:
                t.cancel()
            break

    for t in pending:
        t.cancel()


async def _search_via_registry(
    query: str, limit: int, sources: list[str] | None
) -> list[PaperResult]:
    """桥接到 :mod:`agent.literature_registry` 统一检索。

    遍历 sources 列表，对每个 source 名在 registry 中查找 provider；
    如果找到且可用，调用 provider.search() 并转换为 PaperResult。
    如果 source 列表为 None 或空，使用 active provider 自动选源。

    返回空列表表示没有 registry 可处理的源（调用方回退到本地实现）。
    """
    try:
        from agent.literature_registry import (
            bootstrap_builtin_providers,
            bootstrap_custom_providers,
            bootstrap_local_providers,
            get_active_literature_provider,
            get_provider_by_ref,
            iter_local_providers,
        )

        bootstrap_builtin_providers()
        bootstrap_custom_providers()
        bootstrap_local_providers()
    except Exception as exc:
        logger.debug("[ScholarForge] literature_registry unavailable: %s", exc)
        return []

    import asyncio as _aio

    # 确定 providers 列表
    providers = []
    if sources:
        for src_name in sources:
            p = get_provider_by_ref(src_name)
            if p is not None:
                providers.append(p)
            else:
                logger.debug(
                    "[ScholarForge] source '%s' not in registry, will try local", src_name
                )
    else:
        active = get_active_literature_provider()
        if active is not None:
            providers = [active]
        # 并入所有可用的本地文献库（文件夹/USB），让本地已备论文也参与检索
        for lp in iter_local_providers():
            if lp.is_available() and lp not in providers:
                providers.append(lp)
        if not providers:
            # 没有可用 provider，返回空让调用方回退
            return []

    if not providers:
        return []

    async def _call_one(p):
        try:
            import asyncio as _a
            result = await _a.to_thread(p.search, query, limit)
        except Exception as exc:
            logger.debug("[ScholarForge] registry provider %s failed: %s", p.name, exc)
            return []
        if not isinstance(result, dict) or not result.get("success"):
            err = result.get("error", "unknown") if isinstance(result, dict) else "non-dict"
            logger.debug("[ScholarForge] registry provider %s returned error: %s", p.name, err)
            return []
        papers_data = (result.get("data") or {}).get("papers", [])
        out = []
        for pd in papers_data:
            out.append(PaperResult(
                paper_id=pd.get("doi") or pd.get("url") or pd.get("title", "")[:50],
                title=pd.get("title", ""),
                authors=list(pd.get("authors") or []),
                year=str(pd.get("year", "")),
                venue=pd.get("journal", ""),
                abstract=pd.get("abstract", ""),
                citation_count=int(pd.get("cited_count") or 0),
                url=pd.get("url", ""),
                source=pd.get("source") or p.name,
                pdf_url=pd.get("pdf_url", ""),
                doi=pd.get("doi", ""),
            ))
        return out

    tasks = [_call_one(p) for p in providers]
    results = await _aio.gather(*tasks, return_exceptions=True)
    all_papers = []
    for r in results:
        if isinstance(r, list):
            all_papers.extend(r)
    return all_papers[:limit]


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

    if _is_cooled_down("arxiv"):
        logger.info("[ScholarForge] arXiv 冷却中，跳过")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://export.arxiv.org/api/query", params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }, timeout=10)
            if resp.status_code == 429:
                _set_cooldown("arxiv")
                return []
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

    if _is_cooled_down("crossref"):
        logger.info("[ScholarForge] Crossref 冷却中，跳过")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.crossref.org/works", params={
                "query": query,
                "rows": limit,
                "select": "title,author,abstract,published-print,container-title,is-referenced-by-count,DOI,URL",
            }, timeout=10, headers={
                "User-Agent": "ScholarForge/0.1.0 (mailto:contact@scholarforge.ai)"
            })
            if resp.status_code == 429:
                _set_cooldown("crossref")
                return []
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


async def _search_doaj(query: str, limit: int = 10) -> list[PaperResult]:
    """DOAJ (Directory of Open Access Journals) — 免费开放获取期刊"""
    import httpx

    if _is_cooled_down("doaj"):
        logger.info("[ScholarForge] DOAJ 冷却中，跳过")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://doaj.org/api/search/articles/{query}",
                params={"pageSize": limit, "page": 1},
                timeout=10,
            )
            if resp.status_code == 429:
                _set_cooldown("doaj")
                return []
            resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("results", []):
            bibjson = item.get("bibjson", {})
            title = bibjson.get("title", "")
            if not title:
                continue

            # 提取作者
            authors = []
            for a in bibjson.get("author", []):
                name = a.get("name", "")
                if name:
                    authors.append(name)

            # 获取期刊信息
            journal = bibjson.get("journal", {})
            venue = journal.get("title", "") if journal else ""
            year = bibjson.get("year", "")
            abstract = (bibjson.get("abstract", "") or "")[:500]

            # DOI
            identifiers = bibjson.get("identifier", [])
            doi = ""
            url = ""
            for ident in identifiers:
                if ident.get("type") == "doi":
                    doi = ident.get("id", "")
                    url = f"https://doi.org/{doi}"
                    break
            if not url:
                url = bibjson.get("link", [{}])[0].get("url", "") if bibjson.get("link") else ""

            results.append(PaperResult(
                paper_id=f"doaj:{doi or bibjson.get('eissn', [''])[0]}",
                title=title,
                authors=authors,
                year=str(year),
                venue=venue,
                abstract=abstract,
                url=url,
                source="doaj",
                doi=doi,
            ))

        return results
    except Exception as e:
        logger.error(f"[ScholarForge] DOAJ search failed: {e}")
        return []


async def _search_pubmed(query: str, limit: int = 10) -> list[PaperResult]:
    """PubMed/PMC 搜索 — 免费医学/生命科学文献"""
    import httpx
    from xml.etree import ElementTree as ET

    if _is_cooled_down("pubmed"):
        logger.info("[ScholarForge] PubMed 冷却中，跳过")
        return []
    try:
        async with httpx.AsyncClient() as client:
            # Step 1: ESearch 检索 ID
            search_resp = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": query,
                    "retmax": limit,
                    "retmode": "json",
                    "sort": "relevance",
                },
                timeout=10,
            )
            if search_resp.status_code == 429:
                _set_cooldown("pubmed")
                return []
            search_resp.raise_for_status()

        search_data = search_resp.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []

        # Step 2: EFetch 获取详情
        async with httpx.AsyncClient() as client:
            fetch_resp = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={
                    "db": "pubmed",
                    "id": ",".join(id_list),
                    "retmode": "xml",
                    "rettype": "abstract",
                },
                timeout=15,
            )
            fetch_resp.raise_for_status()

        root = ET.fromstring(fetch_resp.text)
        results = []
        for article in root.findall(".//PubmedArticle"):
            medline = article.find(".//MedlineCitation")
            if medline is None:
                continue

            article_elem = medline.find(".//Article")
            if article_elem is None:
                continue

            # 标题
            title_elem = article_elem.find(".//ArticleTitle")
            title = (title_elem.text or "") if title_elem is not None else ""

            # 作者
            authors = []
            author_list = article_elem.find(".//AuthorList")
            if author_list is not None:
                for auth in author_list.findall("Author"):
                    last = auth.findtext("LastName", "")
                    fore = auth.findtext("ForeName", "")
                    if last:
                        authors.append(f"{fore} {last}".strip())

            # 年份
            year = ""
            pub_date = article_elem.find(".//PubDate")
            if pub_date is not None:
                y = pub_date.findtext("Year", "")
                if not y:
                    y = pub_date.findtext("MedlineDate", "")[:4]
                year = y

            # 期刊名
            journal_elem = article_elem.find(".//Journal")
            venue = ""
            if journal_elem is not None:
                venue = journal_elem.findtext("Title", "")

            # 摘要
            abstract_elem = article_elem.find(".//Abstract")
            abstract = ""
            if abstract_elem is not None:
                parts = []
                for at in abstract_elem.findall("AbstractText"):
                    label = at.get("Label", "")
                    text = at.text or ""
                    if label:
                        parts.append(f"{label}: {text}")
                    else:
                        parts.append(text)
                abstract = " ".join(parts)[:500]

            # PMID
            pmid = medline.findtext(".//PMID", "")

            # DOI
            doi = ""
            for aid in article_elem.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = aid.text or ""
                    break

            results.append(PaperResult(
                paper_id=f"pubmed:{pmid}",
                title=title.strip(),
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
                source="pubmed",
                doi=doi,
            ))

        return results
    except Exception as e:
        logger.error(f"[ScholarForge] PubMed search failed: {e}")
        return []


async def _search_semantic_scholar(query: str, limit: int = 10) -> list[PaperResult]:
    """Semantic Scholar - AI 驱动学术搜索（有 rate limit）"""
    import httpx

    # 429 冷却检查
    if _is_cooled_down("semantic_scholar"):
        logger.info("[ScholarForge] Semantic Scholar 冷却中，跳过")
        return []

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.semanticscholar.org/graph/v1/paper/search", params={
                "query": query,
                "limit": limit,
                "fields": "title,abstract,authors,year,venue,citationCount,externalIds,url",
            }, timeout=10)

            if resp.status_code == 429:
                _set_cooldown("semantic_scholar")
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


# ═══════════════════════════════════════════════════════════════
# 更多免费源
# ═══════════════════════════════════════════════════════════════

async def _search_openalex(query: str, limit: int = 10) -> list[PaperResult]:
    """OpenAlex — 完全免费开放学术图谱，2.4亿+ 作品，无需 API Key
    https://openalex.org
    """
    import httpx
    if _is_cooled_down("openalex"):
        logger.info("[ScholarForge] OpenAlex 冷却中，跳过")
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.openalex.org/works", params={
                "search": query,
                "per_page": min(limit, 25),
                "sort": "cited_by_count:desc",
            }, timeout=10, headers={"User-Agent": "ScholarForge/1.0"})
            if resp.status_code == 429:
                _set_cooldown("openalex")
                return []
            resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("results", []):
            title = item.get("title", "")
            if not title:
                continue
            # 作者
            authors = []
            for auth in (item.get("authorships") or []):
                a = auth.get("author", {})
                name = a.get("display_name", "")
                if name:
                    authors.append(name)
            # 年份
            year = str(item.get("publication_year", ""))
            # 期刊/会议
            venue = ""
            loc = item.get("primary_location") or {}
            src = loc.get("source") or {}
            venue = src.get("display_name", "")
            # 摘要（OpenAlex 有 inverted abstract 需要重建）
            abstract = ""
            inv_abs = item.get("abstract_inverted_index")
            if inv_abs and isinstance(inv_abs, dict):
                # 重建：{word: [positions]} → 按 position 排序
                word_positions = []
                for word, positions in inv_abs.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = " ".join(w for _, w in word_positions)[:500]
            doi = (item.get("doi") or "").replace("https://doi.org/", "")
            results.append(PaperResult(
                paper_id=f"openalex:{item.get('id', '').split('/')[-1]}",
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                citation_count=item.get("cited_by_count", 0) or 0,
                url=f"https://doi.org/{doi}" if doi else item.get("id", ""),
                source="openalex",
                doi=doi,
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] OpenAlex search failed: {e}")
        return []


async def _search_core_free(query: str, limit: int = 10) -> list[PaperResult]:
    """CORE API — 全球最大 OA 论文仓库聚合，免费额度 5000 req/day
    https://core.ac.uk/services/api
    """
    import httpx
    if _is_cooled_down("core"):
        logger.info("[ScholarForge] CORE 冷却中，跳过")
        return []
    core_key = get_api_key("core") or ""
    if not core_key:
        logger.debug("[ScholarForge] CORE 无 API Key，跳过")
        return []
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get("https://api.core.ac.uk/v3/search/works", params={
                "q": query,
                "limit": min(limit, 10),
            }, timeout=10, headers={
                "Authorization": f"Bearer {core_key}",
                "User-Agent": "ScholarForge/1.0",
            })
            if resp.status_code == 429:
                _set_cooldown("core")
                return []
            if resp.status_code != 200:
                logger.warning(f"[ScholarForge] CORE returned {resp.status_code}")
                return []

        data = resp.json()
        results = []
        for item in data.get("results", []):
            title = item.get("title", "")
            if not title:
                continue
            authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]
            year = str(item.get("yearPublished", "") or "")
            abstract = (item.get("abstract") or "")[:500]
            doi = item.get("doi", "")
            results.append(PaperResult(
                paper_id=f"core:{item.get('id', '')}",
                title=title,
                authors=authors,
                year=year,
                venue=item.get("publisher", "") or item.get("journal", {}).get("title", ""),
                abstract=abstract,
                citation_count=item.get("citationCount", 0) or 0,
                url=item.get("downloadUrl") or (f"https://doi.org/{doi}" if doi else ""),
                pdf_url=item.get("downloadUrl", ""),
                source="core",
                doi=doi,
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] CORE search failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# 付费源适配器（用户填入 API Key 后可用）
# ═══════════════════════════════════════════════════════════════

def _get_paid_source_key(source_name: str) -> str:
    """获取用户填入的付费源 API Key"""
    entry = _PAID_SOURCE_REGISTRY.get(source_name, {})
    return entry.get("api_key", "") or os.environ.get(f"SCHOLARFORGE_{source_name.upper()}_KEY", "")


async def _search_scopus(query: str, limit: int = 10) -> list[PaperResult]:
    """Scopus Search API — Elsevier 学术数据库
    API: https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl
    """
    import httpx
    api_key = _get_paid_source_key("scopus")
    if not api_key:
        return []
    try:
        headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.elsevier.com/content/search/scopus", params={
                "query": f"TITLE-ABS-KEY({query})",
                "count": min(limit, 25),
                "sort": "-citedby-count",
            }, headers=headers, timeout=10)
            if resp.status_code == 401 or resp.status_code == 403:
                logger.warning(f"[ScholarForge] Scopus API key invalid")
                return []
            resp.raise_for_status()

        data = resp.json()
        results = []
        entries = data.get("search-results", {}).get("entry", [])
        for item in entries:
            title = item.get("dc:title", "")
            if not title:
                continue
            authors = []
            auth_text = item.get("dc:creator", "")
            if auth_text:
                authors = [a.strip() for a in auth_text.split(",") if a.strip()]
            year = item.get("prism:coverDate", "")[:4]
            venue = item.get("prism:publicationName", "")
            abstract = (item.get("dc:description") or "")[:500]
            doi = item.get("prism:doi", "")
            results.append(PaperResult(
                paper_id=f"scopus:{item.get('dc:identifier', '')}",
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                abstract=abstract,
                citation_count=int(item.get("citedby-count", 0) or 0),
                url=f"https://doi.org/{doi}" if doi else "",
                source="scopus",
                doi=doi,
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] Scopus search failed: {e}")
        return []


async def _search_wos(query: str, limit: int = 10) -> list[PaperResult]:
    """Web of Science Starter API — Clarivate 科学引文索引
    API: https://developer.clarivate.com/apis/wos
    """
    import httpx
    api_key = _get_paid_source_key("web_of_science")
    if not api_key:
        return []
    try:
        headers = {"X-ApiKey": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://api.clarivate.com/apis/wos-starter/v1/documents", params={
                "db": "WOS",
                "q": f"TS=({query})",
                "limit": min(limit, 25),
            }, headers=headers, timeout=10)
            if resp.status_code == 401 or resp.status_code == 403:
                logger.warning(f"[ScholarForge] WoS API key invalid")
                return []
            resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("hits", []):
            title = item.get("title", "")
            if not title:
                continue
            authors = []
            for a in (item.get("names", {}).get("authors", []) or []):
                name = f"{a.get('lastName', '')} {a.get('firstName', '')}".strip()
                if name:
                    authors.append(name)
            year = str(item.get("source", {}).get("publishYear", ""))
            venue = item.get("source", {}).get("sourceTitle", "")
            doi = item.get("identifiers", {}).get("doi", "")
            results.append(PaperResult(
                paper_id=f"wos:{item.get('uid', '')}",
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                citation_count=item.get("citingArticlesCount", 0) or 0,
                url=f"https://doi.org/{doi}" if doi else "",
                source="web_of_science",
                doi=doi,
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] WoS search failed: {e}")
        return []


async def _search_serpapi_scholar(query: str, limit: int = 10) -> list[PaperResult]:
    """Google Scholar via SerpAPI — 支持中文，覆盖面最广
    API: https://serpapi.com/google-scholar-api
    """
    import httpx
    api_key = _get_paid_source_key("google_scholar")
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get("https://serpapi.com/search", params={
                "engine": "google_scholar",
                "q": query,
                "num": min(limit, 20),
                "api_key": api_key,
            }, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"[ScholarForge] SerpAPI returned {resp.status_code}")
                return []

        data = resp.json()
        results = []
        for item in data.get("organic_results", []):
            title = item.get("title", "")
            if not title:
                continue
            pub_info = item.get("publication_info", {})
            authors = []
            summary = pub_info.get("summary", "")
            if summary:
                # Google Scholar 格式: "Author1, Author2, ... - Journal, Year - Publisher"
                parts = summary.split(" - ")
                if parts:
                    authors = [a.strip() for a in parts[0].split(",")[:5] if a.strip() and not a.strip().startswith("…")]
            year = ""
            for part in (pub_info.get("summary", "") or "").split(" - "):
                m = re.search(r'\b(19|20)\d{2}\b', part)
                if m:
                    year = m.group()
                    break
            abstract = (item.get("snippet") or "")[:500]
            results.append(PaperResult(
                paper_id=f"scholar:{item.get('result_id', '')}",
                title=title,
                authors=authors,
                year=year,
                venue=pub_info.get("summary", "").split(" - ")[1] if " - " in pub_info.get("summary", "") else "",
                abstract=abstract,
                citation_count=(item.get("inline_links", {}) or {}).get("cited_by", {}).get("total", 0) or 0,
                url=item.get("link", ""),
                source="google_scholar",
            ))
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] SerpAPI search failed: {e}")
        return []


async def _search_cnki_gateway(query: str, limit: int = 10) -> list[PaperResult]:
    """CNKI 知网搜索 — 多策略自动降级 (P0-1)
    
    策略优先级：
    1. 用户自建网关 (CNKI_GATEWAY_URL + CNKI_API_KEY)
    2. 万方数据 API (WANFANG_API_KEY)
    3. OpenAlex 中文学术映射 — 免费兜底
    """
    from hermes_cli.scholarforge.cnki_fetcher import search_cnki

    try:
        cnki_papers = await search_cnki(query, limit)
        results = []
        for cp in cnki_papers:
            results.append(PaperResult(
                paper_id=f"{cp.source}:{(cp.doi or cp.url or cp.title)[:80]}",
                title=cp.title,
                authors=cp.authors,
                year=cp.year,
                venue=cp.journal,
                abstract=cp.abstract,
                citation_count=cp.cited_count,
                url=cp.url or f"https://scholar.google.com/scholar?q={cp.title}",
                source=cp.source,
                doi=cp.doi,
            ))
        if results:
            logger.info(f"[ScholarForge] CNKI multi-strategy: {len(results)} results via {cnki_papers[0].source if cnki_papers else 'unknown'}")
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] CNKI multi-strategy failed: {e}")
        return []


async def _search_baidu_scholar_source(query: str, limit: int = 10) -> list[PaperResult]:
    """百度学术风格中文聚合源 — 免费（Crossref + OpenAlex 中文聚合，中文优先排序）

    与默认链的 crossref/openalex 同源，仅在中文查询时由 _select_default_sources
    选入以替代二者，避免重复调用。无 API Key 依赖。
    """
    try:
        from hermes_cli.scholarforge.baidu_scholar_fetcher import search_baidu_scholar

        papers = await search_baidu_scholar(query, limit)
        results = []
        for cp in papers:
            results.append(PaperResult(
                paper_id=f"{cp.source}:{(cp.doi or cp.url or cp.title)[:80]}",
                title=cp.title,
                authors=cp.authors,
                year=cp.year,
                venue=cp.journal,
                abstract=cp.abstract,
                citation_count=cp.cited_count,
                url=cp.url or f"https://scholar.google.com/scholar?q={cp.title}",
                source=cp.source,
                doi=cp.doi,
            ))
        if results:
            logger.info(f"[ScholarForge] Baidu Scholar source: {len(results)} results for '{query}'")
        return results
    except Exception as e:
        logger.error(f"[ScholarForge] Baidu Scholar source failed: {e}")
        return []


# 注册全部搜索源 — 免费 + 付费
# 免费源
register_search_source("arxiv", _search_arxiv)
register_search_source("crossref", _search_crossref)
register_search_source("openalex", _search_openalex)
register_search_source("doaj", _search_doaj)
register_search_source("semantic_scholar", _search_semantic_scholar)
register_search_source("pubmed", _search_pubmed)
register_search_source("core", _search_core_free)

# 付费源（需用户填 API Key）
register_search_source("scopus", _search_scopus)
register_search_source("web_of_science", _search_wos)
register_search_source("google_scholar", _search_serpapi_scholar)
register_search_source("cnki", _search_cnki_gateway)
register_search_source("baidu_scholar", _search_baidu_scholar_source)

# 默认搜索链（仅免费源，付费源需用户手动激活后加入）
DEFAULT_SOURCE_CHAIN = ["arxiv", "crossref", "openalex", "doaj", "semantic_scholar", "pubmed", "core"]


async def check_source_connectivity(source_name: str, timeout: float = 5.0) -> dict:
    """检查某个搜索源是否可达"""
    import httpx

    source_test_endpoints = {
        "arxiv": ("https://export.arxiv.org/api/query", {"search_query": "all:test", "max_results": 0}),
        "crossref": ("https://api.crossref.org/works", {"query": "test", "rows": 0, "select": "title"}),
        "openalex": ("https://api.openalex.org/works", {"search": "test", "per_page": 1}),
        "doaj": ("https://doaj.org/api/search/articles/test", {"pageSize": 0}),
        "semantic_scholar": ("https://api.semanticscholar.org/graph/v1/paper/search", {"query": "test", "limit": 0}),
        "pubmed": ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {"db": "pubmed", "term": "test", "retmax": 0, "retmode": "json"}),
        "core": ("https://api.core.ac.uk/v3/search/works", {"q": "test", "limit": 1}),
    }

    info = {
        "name": source_name,
        "accessible": False,
        "requires_key": False,
        "in_default_chain": source_name in DEFAULT_SOURCE_CHAIN,
        "error": None,
    }

    if source_name not in source_test_endpoints:
        info["error"] = "未知来源"
        return info

    url, params = source_test_endpoints[source_name]
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            info["accessible"] = True
    except Exception as e:
        info["error"] = str(e)[:200]

    return info


async def get_configured_sources() -> dict:
    """报告所有搜索源的可达性状态"""
    tasks = []
    for name in _SEARCH_SOURCES:
        tasks.append(check_source_connectivity(name))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    sources = []
    for r in results:
        if isinstance(r, Exception):
            sources.append({"name": "unknown", "accessible": False, "error": str(r)})
        else:
            sources.append(r)

    accessible_count = sum(1 for s in sources if s.get("accessible"))
    return {
        "sources": sources,
        "total": len(sources),
        "accessible": accessible_count,
        "default_chain": DEFAULT_SOURCE_CHAIN,
    }

register_service("core", api_key_env_var="CORE_API_KEY", label="ScholarForge Core")
