"""通用「EmpireCMS 登录 + SSO → 动态 KNS8 镜像」临时登录检索适配器基类。

适用对象：shutong / wenx / ccki 等"用户购买到的第三方中文文献代理"。它们架构同构：
EmpireCMS 卡密登录 → 某 SSO 入口跳转到**动态分配**的 CNKI KNS8 镜像 →
``POST <mirror>/kns8s/brief/grid`` 标准知网检索 → 解析 ``table.result-table-list``。

差异**仅通过配置表达**，无需逐个写 adapter：

- ``sso_path`` / ``sso_referer`` / ``sso_mode``（``token_then_redirect`` | ``direct_302``）
- ``ecmsfrom`` 等 EmpireCMS 登录隐藏域
- ``channel_gate``：该账号该频道是否需购买群组开通（未开通时给出友好错误而非静默失败）
- ``source_tag``：结果归属标记（shutong / wenx / ...）

**会话缓存（防封号核心）**：登录一次后将 cookie + mirror 复用，带 TTL（默认 10min），
失效才重登。避免 agent 单会话多次查询反复登录触发网关防暴破
（曾因探测过于频繁封停过用户账号 24 小时）。

合规：仅用于用户自有合法凭证；只做已购权限内检索，不破解任何验证码。
全流程纯 HTTP（httpx）完成，无需浏览器、无需用户从 DevTools 抓 URL。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from agent.literature_providers.custom import CustomHttpProvider

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 从 SSO 入口的 JS 跳转里抽取真实 token / 镜像 URL。
_REDIRECT_RE = re.compile(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_META_REFRESH_RE = re.compile(r"url=([^'\"\s>]+)", re.I)

# 知网 KNS8 跨库检索的数据库代码集（"全部"跨库），实测有效。
_KUAKU_CODE = (
    "YSTT4HG0,LSTPFY1C,JUP3MUPD,MPMFIG1A,EMRPGLPA,"
    "WQ0UVIAA,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R"
)

_SEARCH_PATH = "/kns8s/brief/grid"

# 登录态缓存 TTL（秒）：登录一次，cookie+mirror 复用，到期才重登。
_SESSION_TTL = 600


class Kns8TempLoginProvider(CustomHttpProvider):
    """通用：EmpireCMS 临时登录 → 发现 KNS8 镜像 → 标准知网检索。

    子类只需覆写下列类常量表达差异，无需重写逻辑：
    ``_DEFAULT_SSO_PATH`` / ``_DEFAULT_SSO_REFERER`` / ``_DEFAULT_SSO_MODE`` /
    ``_DEFAULT_ECMSFROM`` / ``_DEFAULT_CHANNEL_GATE`` / ``_DEFAULT_SOURCE_TAG``。
    也可在注册 definition 中直接以字段覆盖（优先级更高）。
    """

    _DEFAULT_SSO_PATH = "/l77.php"
    _DEFAULT_SSO_REFERER = "/zhongwenku/"
    _DEFAULT_SSO_MODE = "token_then_redirect"  # 或 "direct_302"
    _DEFAULT_ECMSFROM = "/zhongwenku/"
    _DEFAULT_CHANNEL_GATE = False
    _DEFAULT_SOURCE_TAG = "kns8"

    def __init__(self, definition: Dict[str, Any]):
        super().__init__(definition)
        base = (self._base_url or definition.get("login_url", "")).rstrip("/")
        self._portal_base = base
        # SSO 入口：默认类常量，definition 字段可覆盖
        self._sso_path = (definition.get("sso_path") or self._DEFAULT_SSO_PATH).strip()
        self._sso_url = (
            definition.get("sso_url") or (base + self._sso_path)
        ).strip().rstrip("/")
        self._sso_referer = (
            definition.get("sso_referer") or (base + self._DEFAULT_SSO_REFERER)
        ).strip().rstrip("/")
        self._sso_mode = (
            definition.get("sso_mode") or self._DEFAULT_SSO_MODE
        ).strip().lower()
        self._ecmsfrom = definition.get("ecmsfrom") or self._DEFAULT_ECMSFROM
        self._channel_gate = bool(definition.get("channel_gate", self._DEFAULT_CHANNEL_GATE))
        self._source_tag = definition.get("source_tag") or self._DEFAULT_SOURCE_TAG
        self._mirror_base_override = (definition.get("mirror_base") or "").strip().rstrip("/")
        self._search_path = (definition.get("search_path") or _SEARCH_PATH).strip()
        # 登录字段：EmpireCMS 实测为 username/password
        self._login_user_field = self._login_user_field or "username"
        self._login_password_field = self._login_password_field or "password"
        extra = dict(self._login_extra_fields or {})
        extra.setdefault("enews", "login")
        extra.setdefault("tobind", "0")
        extra.setdefault("lifetime", "0")
        extra.setdefault("ecmsfrom", self._ecmsfrom)
        self._login_extra_fields = extra
        # 会话缓存（实例级，进程生命周期内复用，防封号）
        self._sess_cookies: Optional[Any] = None
        self._sess_mirror: Optional[str] = None
        self._sess_expire = 0.0

    # ── 标准 CNKI KNS8 QueryJson ────────────────────────────────────────────

    def _build_query_json(self, query: str) -> str:
        payload = {
            "Platform": "",
            "Resource": "CROSSDB",
            "Classid": "WD0FTY92",
            "Products": "",
            "QNode": {
                "QGroup": [
                    {
                        "Key": "Subject",
                        "Title": "",
                        "Logic": 0,
                        "Items": [
                            {
                                "Field": "SU",
                                "Value": query,
                                "Operator": "TOPRANK",
                                "Logic": 0,
                                "Title": "主题",
                            }
                        ],
                        "ChildItems": [],
                    }
                ]
            },
            "ExScope": 1,
            "SearchType": 2,
            "Rlang": "CHINESE",
            "KuaKuCode": _KUAKU_CODE,
        }
        return json.dumps(payload, ensure_ascii=False)

    # ── 会话缓存（防封号核心） ──────────────────────────────────────────────

    def _cache_session(self, cookies: Any, mirror: str) -> None:
        self._sess_cookies = cookies
        self._sess_mirror = mirror
        self._sess_expire = time.time() + _SESSION_TTL

    def _take_session(self) -> Optional[str]:
        """若登录态缓存未过期，返回缓存的 mirror；否则返回 None。"""
        if self._sess_cookies is not None and self._sess_expire > time.time():
            return self._sess_mirror
        return None

    # ── SSO → 镜像发现（纯 HTTP 跟随重定向，无浏览器） ─────────────────────────

    def _discover_mirror(self, client) -> Dict[str, Any]:
        """登录后跟随 SSO 跳转发现动态 KNS8 镜像 base。返回 {ok, mirror|error}。"""
        creds = self._creds()
        login_payload = {
            self._login_user_field: creds.get("user") or "",
            self._login_password_field: creds.get("password") or "",
        }
        login_payload.update(self._login_extra_fields)
        try:
            # 1) 表单登录（种会话 cookie）
            client.post(
                self._login_url,
                data=login_payload,
                headers={"Referer": self._portal_base + "/e/member/login/"},
            )
            # 2) SSO 入口 → 解析跳转目标
            if self._sso_mode == "direct_302":
                # 入口直接 302 到镜像（wenx /cs00.php 这类）
                r = client.get(self._sso_url, headers={"Referer": self._sso_referer})
                final = str(r.url)
            else:
                # 入口返回 JS location.href → token URL，再 302 到镜像（shutong /l77.php）
                r = client.get(self._sso_url, headers={"Referer": self._sso_referer})
                token_url = self._extract_redirect(r.text)
                if not token_url:
                    return {
                        "ok": False,
                        "error": "未能从入口解析到 SSO 跳转（登录可能失败或页面结构变更）",
                    }
                r = client.get(token_url, headers={"Referer": self._portal_base + "/"})
                final = str(r.url)
            # 3) 抽取镜像 host
            m = re.match(r"https?://[^/]+", final)
            if not m:
                return {"ok": False, "error": "入口未重定向到检索镜像"}
            mirror = m.group(0)
            # 防呆：未跳出门户域
            if self._portal_base and mirror.rstrip("/") == self._portal_base.rstrip("/"):
                if self._channel_gate:
                    return {
                        "ok": False,
                        "error": "该知网频道需在网关开通群组/订阅后才能检索（请先在购买处开通）",
                        "channel_gate": True,
                    }
                return {"ok": False, "error": "SSO 未跳出门户（令牌派发可能失败）"}
            return {"ok": True, "mirror": mirror}
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s mirror discovery failed: %s", self._source_tag, exc)
            return {"ok": False, "error": f"SSO 会话建立失败: {exc}"}

    @staticmethod
    def _extract_redirect(html: str) -> str:
        """从 SSO 入口 HTML 抽取真实跳转 URL（优先带 token 的 location.href）。"""
        candidates = _REDIRECT_RE.findall(html or "")
        for u in candidates:
            if "token" in u or "sign_token" in u:
                return u
        if candidates:
            return candidates[0]
        m = _META_REFRESH_RE.search(html or "")
        return m.group(1) if m else ""

    # ── search（临时登录；有缓存则复用，避免反复登录封号） ────────────────────

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 10), 50))
        try:
            import httpx
        except ImportError:
            return {"success": False, "error": "httpx 未安装 — pip install httpx", "source": self.name}

        try:
            with httpx.Client(
                timeout=25, follow_redirects=True, headers={"User-Agent": _UA}
            ) as client:
                # 1) 建立会话并发现动态镜像（优先复用缓存登录态）
                cached_mirror = self._take_session()
                if cached_mirror:
                    if self._sess_cookies is not None:
                        client.cookies = self._sess_cookies
                    mirror = cached_mirror
                else:
                    disc = self._discover_mirror(client)
                    if not disc.get("ok"):
                        return {
                            "success": False,
                            "error": disc.get("error"),
                            "channel_gate": disc.get("channel_gate", False),
                            "source": self.name,
                        }
                    mirror = disc["mirror"]
                    # 缓存登录态（mock 客户端可能无 cookies 属性，用 getattr 容错）
                    self._cache_session(getattr(client, "cookies", None), mirror)

                # 2) 标准知网 KNS8 检索（检索 API 不设验证码）
                form = {
                    "boolSearch": "true",
                    "QueryJson": self._build_query_json(query),
                    "pageNum": "1",
                    "pageSize": str(min(limit, 50)),
                    "sortField": "PT",
                    "sortType": "desc",
                    "dstyle": "listmode",
                    "boolSortSearch": "false",
                    "productStr": "",
                    "aside": "",
                }
                r = client.post(
                    mirror + self._search_path,
                    data=form,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": mirror + "/kns8/defaultresult/index",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                if r.status_code != 200:
                    return {
                        "success": False,
                        "error": f"检索请求失败 HTTP {r.status_code}",
                        "source": self.name,
                    }
                # 验证码拦截兜底判定
                if "verify" in str(r.url).lower() or "captchaType" in r.text:
                    # 会话可能过期，清缓存下次重登
                    self._sess_cookies = None
                    self._sess_mirror = None
                    return {
                        "success": False,
                        "error": "检索被镜像验证码拦截（会话可能过期，请稍后重试）",
                        "source": self.name,
                    }
                papers = _parse_shutong_grid(r.text, limit, self._source_tag)
                return {
                    "success": True,
                    "data": {"papers": papers, "source": self.name, "count": len(papers)},
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("%s search failed: %s", self._source_tag, exc)
            return {"success": False, "error": f"检索异常: {exc}", "source": self.name}


def _parse_shutong_grid(html: str, limit: int, source_tag: str = "shutong") -> List[Dict[str, Any]]:
    """解析 KNS8 ``table.result-table-list`` 结果 HTML（best-effort）。

    行结构（实证）：``td.seq`` / ``td.name``(标题 + ``a.fz14`` 链接) /
    ``td.author`` / ``td.source``(期刊) / ``td.date`` / ``td.data``(类型)。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("未安装 bs4，无法解析结果 HTML")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table.result-table-list tbody tr") or soup.select(
            "table.result-table-list tr"
        )
        papers: List[Dict[str, Any]] = []
        for row in rows:
            if row.find("th"):
                continue
            name_cell = row.select_one("td.name")
            title_a = (
                name_cell.select_one("a.fz14") or name_cell.select_one("a")
                if name_cell
                else None
            )
            title = ""
            url = ""
            if title_a:
                title = (title_a.get("title") or title_a.get_text(strip=True) or "").strip()
                url = (title_a.get("href") or "").strip()
            elif name_cell:
                title = name_cell.get_text(strip=True)
            if not title:
                continue
            author_cell = row.select_one("td.author")
            authors: List[str] = []
            if author_cell:
                links = author_cell.select("a")
                if links:
                    authors = [a.get_text(strip=True) for a in links if a.get_text(strip=True)]
                else:
                    raw = author_cell.get_text(";", strip=True)
                    authors = [a.strip() for a in re.split(r"[;,、\s]+", raw) if a.strip()]
            source_cell = row.select_one("td.source")
            venue = source_cell.get_text(strip=True) if source_cell else ""
            date_cell = row.select_one("td.date")
            year = ""
            if date_cell:
                m = re.search(r"(\d{4})", date_cell.get_text())
                if m:
                    year = m.group(1)
            type_cell = row.select_one("td.data")
            doc_type = type_cell.get_text(strip=True) if type_cell else ""
            papers.append(
                {
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "venue": venue,
                    "url": url,
                    "type": doc_type,
                    "source": source_tag,
                }
            )
            if len(papers) >= limit:
                break
        return papers
    except Exception as exc:  # noqa: BLE001
        logger.debug("kns8 grid 解析失败: %s", exc)
        return []
