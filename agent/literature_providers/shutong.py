"""书童图书馆 (shutong) 专用文献源适配器。

shutong 是一个第三方中文文献代理网关（知网/万方/维普等），基于 EmpireCMS。
经**真机抓包实证**（2026-07-24），其检索链路为：

  1. EmpireCMS 表单登录（卡号+密码 → 会话 cookie）——
     ``POST /e/member/doaction.php``（enews=login / ecmsfrom=/zhongwenku/ / lifetime=0）。
  2. 登录态访问中文库入口 ``/l77.php``（带 ``Referer: /zhongwenku/``）→ 返回一段
     ``location.href='https://api88.wenxian.shop/token?sign_key=...&sign_token=<JWT>'``
     的 JS 跳转（``sign_token`` 是 SSO JWT）。
  3. 请求该 token URL → **HTTP 302 直接重定向到一个动态的 CNKI KNS8 镜像**
     （形如 ``http://<ip:port>/kns8/defaultresult/index``；镜像地址按会话动态分配，
     必须运行时跟随重定向发现，不能硬编码）。
  4. 在镜像上以**标准知网 KNS8 契约**检索：
     ``POST <mirror>/kns8s/brief/grid``，body 为 ``boolSearch=true&QueryJson=<...>``，
     返回 ``<table class="result-table-list">`` 结果 HTML。

**全流程可用纯 HTTP（httpx）自动完成，无需浏览器、无需用户从 DevTools 抓 URL。**
镜像首页 ``/kns8s/`` 会弹滑块验证码，但检索 API ``/kns8s/brief/grid`` 本身**不设验证码**
（用新鲜 SSO 会话直连即可），因此本适配器绕开首页、直接调检索 API。

合规：仅用于用户自有合法 shutong 凭证；仅做用户已购权限内的检索，不破解任何验证码。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from agent.literature_providers.custom import CustomHttpProvider

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# shutong 中文库 SSO 入口（登录后取令牌/跳镜像的页面）。可被定义覆盖。
_DEFAULT_SSO_PATH = "/l77.php"
_DEFAULT_SSO_REFERER_PATH = "/zhongwenku/"

# 从 SSO 入口的 JS 跳转里抽取真实 token URL（api88.wenxian.shop/token?...）。
_REDIRECT_RE = re.compile(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", re.I)
_META_REFRESH_RE = re.compile(r"url=([^'\"\s>]+)", re.I)

# 知网 KNS8 跨库检索的数据库代码集（"全部"跨库），实测有效。
_KUAKU_CODE = (
    "YSTT4HG0,LSTPFY1C,JUP3MUPD,MPMFIG1A,EMRPGLPA,"
    "WQ0UVIAA,BLZOG7CK,PWFIRAGL,NN3FJMUV,NLBO1Z6R"
)

_SEARCH_PATH = "/kns8s/brief/grid"


class ShutongProvider(CustomHttpProvider):
    """书童图书馆专用适配器：EmpireCMS 登录 + SSO → KNS8 镜像 → 标准知网检索。"""

    def __init__(self, definition: Dict[str, Any]):
        super().__init__(definition)
        base = (self._base_url or definition.get("login_url", "")).rstrip("/")
        self._portal_base = base
        self._sso_url = (definition.get("sso_url") or (base + _DEFAULT_SSO_PATH)).strip().rstrip("/")
        ref = definition.get("sso_referer") or (base + _DEFAULT_SSO_REFERER_PATH)
        self._sso_referer = ref.strip().rstrip("/")
        # 可选覆盖：直接指定镜像/检索路径（默认全自动发现）
        self._mirror_base_override = (definition.get("mirror_base") or "").strip().rstrip("/")
        self._search_path = (definition.get("search_path") or _SEARCH_PATH).strip()
        # 登录字段：shutong 实测为 username/password
        self._login_user_field = self._login_user_field or "username"
        self._login_password_field = self._login_password_field or "password"
        # EmpireCMS 登录隐藏域（shutong 实测所需）
        extra = dict(self._login_extra_fields or {})
        extra.setdefault("enews", "login")
        extra.setdefault("tobind", "0")
        extra.setdefault("lifetime", "0")
        extra.setdefault("ecmsfrom", "/zhongwenku/")
        self._login_extra_fields = extra

    # ── query building (standard CNKI KNS8 contract) ────────────────────────

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

    # ── SSO → mirror discovery (verified via HTTP redirects, no browser) ─────

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
            # 2) 中文库入口 → 解析 JS 跳转到 api88 token URL
            r = client.get(self._sso_url, headers={"Referer": self._sso_referer})
            token_url = self._extract_redirect(r.text)
            if not token_url:
                return {
                    "ok": False,
                    "error": "未能从中文库入口解析到 SSO 跳转（登录可能失败或页面结构变更）",
                }
            # 3) 跟随 token 302 → 动态镜像 base
            r = client.get(token_url, headers={"Referer": self._portal_base + "/"})
            final = str(r.url)
            m = re.match(r"https?://[^/]+", final)
            if not m:
                return {"ok": False, "error": "SSO 令牌未重定向到检索镜像"}
            mirror = m.group(0)
            # 防呆：镜像不应还停在 shutong 门户域名
            if self._portal_base and mirror.rstrip("/") in self._portal_base.rstrip("/"):
                return {"ok": False, "error": "SSO 未跳出门户（令牌派发可能失败）"}
            return {"ok": True, "mirror": mirror}
        except Exception as exc:  # noqa: BLE001
            logger.debug("shutong mirror discovery failed: %s", exc)
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

    # ── search ──────────────────────────────────────────────────────────────

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
                # 1) 建立会话并发现动态镜像
                if self._mirror_base_override:
                    # 仍需登录 + 走 SSO 以取得镜像会话 cookie
                    disc = self._discover_mirror(client)
                    mirror = self._mirror_base_override if not disc.get("ok") else disc["mirror"]
                else:
                    disc = self._discover_mirror(client)
                    if not disc.get("ok"):
                        return {"success": False, "error": disc.get("error"), "source": self.name}
                    mirror = disc["mirror"]

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
                    return {
                        "success": False,
                        "error": "检索被镜像验证码拦截（会话可能过期，请稍后重试）",
                        "source": self.name,
                    }
                papers = _parse_shutong_grid(r.text, limit)
                return {
                    "success": True,
                    "data": {"papers": papers, "source": self.name, "count": len(papers)},
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("shutong search failed: %s", exc)
            return {"success": False, "error": f"检索异常: {exc}", "source": self.name}


def _parse_shutong_grid(html: str, limit: int) -> List[Dict[str, Any]]:
    """解析 KNS8 ``table.result-table-list`` 结果 HTML（best-effort）。

    行结构（实证）：``td.seq`` / ``td.name``(标题 + ``a.fz14`` 链接) /
    ``td.author`` / ``td.source``(期刊) / ``td.date`` / ``td.data``(类型)。
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("未安装 bs4，无法解析 shutong 结果 HTML")
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
                # 作者格内每个作者常为独立 <a>；退化为整体文本切分
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
                    "source": "shutong",
                }
            )
            if len(papers) >= limit:
                break
        return papers
    except Exception as exc:  # noqa: BLE001
        logger.debug("shutong grid 解析失败: %s", exc)
        return []
