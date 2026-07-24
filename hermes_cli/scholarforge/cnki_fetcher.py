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
import json
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
    """策略1: 通过用户自建 CNKI 网关（支持 Bearer 卡号 / Basic 账号密码）

    R1 修复：凭证统一从 agent.service_credentials 读取（用户在 Settings 文献源
    中填的网站/卡号/卡密都进统一层），不再只依赖裸环境变量。优先用
    账号+密码走 Basic 认证，否则回退 Bearer 卡号。
    """
    creds = get_service_credentials("cnki", base_url_env_var="CNKI_GATEWAY_URL")
    gateway_url = (creds.get("base_url") or os.environ.get("CNKI_GATEWAY_URL", "")).strip()
    api_key = (creds.get("api_key") or "").strip()
    username = (creds.get("CNKI_USERNAME") or os.environ.get("CNKI_USERNAME", "")).strip()
    password = (creds.get("CNKI_PASSWORD") or os.environ.get("CNKI_PASSWORD", "")).strip()
    if not gateway_url:
        return []

    import base64
    import httpx
    try:
        headers = {"Content-Type": "application/json"}
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        elif api_key:
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
    使用 title.search 提升相关性（全文 search 会返回不相关结果）。
    """
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            # 优先用 title.search 精确匹配标题
            resp = await client.get(
                "https://api.openalex.org/works",
                params={
                    "filter": f"title.search:{query},language:zh,type:article",
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


def _get_cnki_account_credentials() -> tuple[str, str]:
    """回读用户配置的知网账号（卡号卡密）。

    优先读环境变量 ``CNKI_USERNAME`` / ``CNKI_PASSWORD``（与 ``register_service``
    的 ``extra_fields`` key 同名），回退到用户 services 配置。仅使用用户自有
    合法凭证，绝不共享/转售。
    """
    username = (os.environ.get("CNKI_USERNAME") or "").strip()
    password = (os.environ.get("CNKI_PASSWORD") or "").strip()
    if not (username and password):
        try:
            from hermes_cli.config import load_config

            svc = (load_config() or {}).get("services", {}).get("cnki", {}) or {}
            username = username or (svc.get("CNKI_USERNAME") or "").strip()
            password = password or (svc.get("CNKI_PASSWORD") or "").strip()
        except Exception:
            pass
    return username, password


async def _fetch_via_cnki_account(
    query: str, limit: int = 20, username: str = "", password: str = ""
) -> list[CnkiPaper]:
    """策略1.5：用知网账号（卡号卡密）登录后检索。

    仅使用用户自有合法凭证。知网登录含滑块/验证码反爬，自动登录可能被拦截；
    登录失败则优雅降级返回 ``[]``，并建议改用「第三方网关」路径
    (``CNKI_GATEWAY_URL`` + ``CNKI_API_KEY``)。
    """
    if not username or not password:
        return []

    import httpx

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            # 1) 取得 kns 会话 SID
            await client.get("https://kns.cnki.net/kns8s/defaultresult/index", timeout=15)
            # 2) 登录知网通行证
            await client.post(
                "https://login.cnki.net/Login.aspx",
                data={
                    "UserName": username,
                    "PassWord": password,
                    "LoginType": "Document",
                    "r": "https://www.cnki.net/",
                    "code": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            # 3) 判定登录是否成功：拿到认证 cookie
            if not _cnki_session_authenticated(client):
                logger.warning("CNKI 账号登录未成功（可能被验证码/反爬拦截），已回退其它策略")
                return []
            # 4) 检索
            search_resp = await client.post(
                "https://kns.cnki.net/kns8s/brief/grid",
                data={
                    "QueryJson": json.dumps(
                        {"Platform": "", "Resource": "", "Query": query, "Lng": "CHINA"}
                    )
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
            )
            return _parse_cnki_grid_html(search_resp.text, limit)
    except Exception as e:
        logger.error(f"CNKI account search error: {e}")
        return []


def _cnki_session_authenticated(client) -> bool:
    """粗略判定知网登录是否成功：存在认证 cookie。"""
    try:
        cookies = {c.name: c.value for c in client.cookies.jar}
    except Exception:
        return False
    return bool(cookies.get("cnkiUserKey")) or (
        cookies.get("SID") and cookies.get("ASP.NET_SessionId")
    )


def _parse_cnki_grid_html(html: str, limit: int) -> list[CnkiPaper]:
    """尽力解析 CNKI 检索结果 HTML（best-effort，解析失败返回 ``[]``）。

    BeautifulSoup 为可选依赖；缺失时记录日志并降级（此时建议用第三方网关）。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("未安装 bs4，跳过 CNKI HTML 解析（建议配置第三方网关 CNKI_GATEWAY_URL）")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table.GridTableContent tr") or soup.select(".result-table tr")
        papers: list[CnkiPaper] = []
        for row in rows[:limit]:
            title_a = row.select_one("a.fz14") or row.select_one("a[title]")
            title = (title_a.get("title") or title_a.get_text(strip=True)) if title_a else ""
            if not title:
                continue
            author_td = row.select_one("td.author_flag") or row.select_one("td:nth-child(2)")
            author_text = author_td.get_text(strip=True) if author_td else ""
            year = ""
            m = re.search(r"(\d{4})", row.get_text())
            if m:
                year = m.group(1)
            papers.append(
                CnkiPaper(
                    title=title,
                    authors=[a.strip() for a in author_text.split(";") if a.strip()],
                    year=year,
                    source="cnki",
                )
            )
        return papers
    except Exception as e:
        logger.debug(f"CNKI HTML 解析失败: {e}")
        return []


async def search_cnki(query: str, limit: int = 20) -> list[CnkiPaper]:
    """多策略知网搜索 — 自动降级

    策略1(网关) → 1.5(账号卡密) → 2(万方) → 3(OpenAlex)，拿到结果即返回。
    """
    # 策略1: 用户自建网关（最稳定）
    papers = await _fetch_via_gateway(query, limit)
    if papers:
        logger.info(f"CNKI gateway: {len(papers)} results for '{query}'")
        return papers

    # 策略1.5: 知网账号（卡号卡密）直接登录检索
    username, password = _get_cnki_account_credentials()
    if username and password:
        papers = await _fetch_via_cnki_account(query, limit, username, password)
        if papers:
            logger.info(f"CNKI account: {len(papers)} results for '{query}'")
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

register_service("cnki", api_key_env_var="CNKI_API_KEY", label="CNKI", category="literature")

register_service("wanfang", api_key_env_var="WANFANG_API_KEY", label="Wanfang", category="literature")
