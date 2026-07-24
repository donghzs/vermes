"""书童图书馆 (shutong) 专用文献源适配器。

shutong 是一个第三方中文文献代理网关（知网/万方/维普等），基于 EmpireCMS。
经实测，其检索链路为两段式 SSO：

  1. EmpireCMS 表单登录（卡号+密码 → 会话 cookie）—— 与通用 ``form`` 网关一致；
  2. 登录后访问中文库入口（如 ``/l77.php``，需带 ``Referer``），页面 JS 会
     重定向到 ``https://api88.wenxian.shop/token?sign_key=...&sign_token=<JWT>``，
     其中 ``sign_token`` 是一段 JWT（payload 含 ``domain``/``username``/``exp``）。
     该 JWT 即访问真实检索 API（``api88.wenxian.shop`` 上的专有 search 接口）的凭证。

第 2 步之后的**真实检索端点契约是专有的、无法靠盲探得到**（Go 服务，所有猜测路径
均 404）。因此本适配器：

  * 实装并验证了「登录 → 取 SSO JWT」这一段（:meth:`_acquire_sso_token`）；
  * ``search()`` 在拿到 JWT 后，调用**用户配置的** ``search_url``（真实检索端点，
    需从浏览器 DevTools 抓取），按 ``token_scheme``（bearer/cookie/query）携带 JWT，
    再用通用的 tolerant 解析器抽取论文；
  * 若 ``search_url`` 未配置，返回清晰可执行的提示，而非静默失败。

合规：仅用于用户自有合法 shutong 凭证。
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, Optional

from agent.literature_providers._http import (
    http_get_json,
    http_login_then_get,
    http_post_json,
)
from agent.literature_providers.custom import CustomHttpProvider

logger = logging.getLogger(__name__)

# shutong 的中文库 SSO 入口（登录后拿令牌的页面）。可被定义覆盖。
_DEFAULT_SSO_PATH = "/l77.php"
_DEFAULT_SSO_REFERER_PATH = "/zhongwenku/"

# 从 SSO 入口的 JS 重定向里抽取 sign_token JWT 的正则。
_REDIRECT_RE = re.compile(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]")
_SIGN_TOKEN_RE = re.compile(r"[?&]sign_token=([^&'\"\s]+)")


class ShutongProvider(CustomHttpProvider):
    """书童图书馆专用适配器：EmpireCMS 登录 + SSO JWT + 可配置检索端点。"""

    def __init__(self, definition: Dict[str, Any]):
        super().__init__(definition)
        base = self._base_url or (definition.get("login_url", "").rstrip("/"))
        self._sso_url = (definition.get("sso_url") or (base + _DEFAULT_SSO_PATH)).strip().rstrip("/")
        ref = definition.get("sso_referer") or (base + _DEFAULT_SSO_REFERER_PATH)
        self._sso_referer = ref.strip().rstrip("/")
        # JWT 携带方式：bearer / cookie / query
        self._token_scheme = (definition.get("token_scheme") or "bearer").lower()
        # EmpireCMS 登录所需的隐藏域（shutong 实测需要）
        extra = dict(self._login_extra_fields or {})
        extra.setdefault("enews", "login")
        extra.setdefault("lifetime", "0")
        extra.setdefault("ecmsfrom", "/zhongwenku/")
        self._login_extra_fields = extra

    # ── SSO token acquisition (verified step) ───────────────────────────────

    def _acquire_sso_token(self) -> Dict[str, Any]:
        """登录后取 SSO JWT。返回 {ok, token|error}。"""
        creds = self._creds()
        login_payload = {
            self._login_user_field: creds.get("user") or "",
            self._login_password_field: creds.get("password") or "",
        }
        login_payload.update(self._login_extra_fields)
        try:
            r = http_login_then_get(
                login_url=self._login_url,
                login_payload=login_payload,
                get_url=self._sso_url,
                get_headers={"Referer": self._sso_referer},
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"SSO 入口访问失败: {exc}"}
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "SSO 入口访问失败（请检查登录地址/账号密码）")}
        text = r.get("text") or ""
        m = _REDIRECT_RE.search(text)
        if not m:
            return {"ok": False, "error": "未能从 SSO 入口解析到重定向（shutong 页面结构可能已变更）"}
        token = _SIGN_TOKEN_RE.search(m.group(1))
        if not token:
            return {"ok": False, "error": "SSO 重定向中未包含 sign_token（令牌派发契约可能已变更）"}
        return {"ok": True, "token": token.group(1)}

    # ── search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 10), 50))
        endpoint = self._endpoint()

        # 1) acquire SSO token (requires login)
        tok = self._acquire_sso_token()
        if not tok.get("ok"):
            return {
                "success": False,
                "error": tok.get("error", "未能获取 shutong SSO 令牌"),
                "source": self.name,
            }

        # 2) call the real search endpoint — MUST be configured by the user,
        #    because shutong's actual retrieval API is proprietary/undiscoverable.
        if not self._search_url or self._search_url.rstrip("/") in (
            endpoint.rstrip("/"), ""
        ):
            return {
                "success": False,
                "error": (
                    "shutong 检索端点未配置：真实检索接口（api88.wenxian.shop 上的 "
                    "search API）为专有契约，无法自动探测。请在设置 → 文献源 中填写 "
                    "该源的 search_url（从浏览器 DevTools 抓一次 shutong 站内检索的请求地址），"
                    "并选择 token_scheme（bearer/cookie/query）。"
                ),
                "source": self.name,
            }

        headers: Dict[str, str] = {}
        params: Dict[str, Any] = {self._query_param: query, "limit": limit}
        if self._token_scheme == "bearer":
            headers["Authorization"] = "Bearer " + tok["token"]
        elif self._token_scheme == "cookie":
            headers["Cookie"] = "token=" + tok["token"]
        elif self._token_scheme == "query":
            params["token"] = tok["token"]
        else:
            # default: try bearer
            headers["Authorization"] = "Bearer " + tok["token"]

        try:
            if self._method == "POST":
                r = http_post_json(self._search_url, json_body=params, headers=headers)
            else:
                r = http_get_json(self._search_url, params=params, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shutong search failed: %s", exc)
            return {"success": False, "error": f"检索异常: {exc}", "source": self.name}

        if not r.get("ok"):
            return {
                "success": False,
                "error": r.get("error", "检索请求失败"),
                "source": self.name,
            }

        papers = self._parse(r.get("data") or r.get("text"), limit)
        return {
            "success": True,
            "data": {"papers": papers, "source": self.name, "count": len(papers)},
        }
