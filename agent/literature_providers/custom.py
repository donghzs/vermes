"""Generic adapter for user-defined (custom) literature sources.

A custom source is "any HTTP endpoint the user can query with a search term and
some credentials". We can't know the exact wire protocol of every institution's
internal portal, so :class:`CustomHttpProvider` implements a *best-effort*
adapter:

  * builds the request from the source definition (endpoint, auth scheme,
    query-param / method, credential fields);
  * attaches auth as Bearer / Basic / custom header / query-param / none;
  * tolerantly extracts a paper list from the most common JSON shapes
    (``results`` / ``records`` / ``data`` / ``items`` / ``papers`` / ``hits`` …
    and per-item title / authors / year / abstract / url / doi / journal).

If a portal's response shape is exotic, the tool still reports a clear error
rather than silently returning nothing — and the user can always switch to a
different ``source``.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional

from agent.literature_provider import LiteratureProvider
from agent.literature_providers._http import http_get_json, http_post_json

logger = logging.getLogger(__name__)


# ── tolerant field extraction ────────────────────────────────────────────────

_TITLE_KEYS = ("title", "name", "article_title", "题名")
_AUTHORS_KEYS = ("authors", "author", "creator", "authors_list", "作者")
_YEAR_KEYS = (
    "year", "date", "published", "publication_year", "pubdate",
    "publication_date", "出版年", "year_published",
)
_ABSTRACT_KEYS = ("abstract", "summary", "description", "intro", "摘要")
_URL_KEYS = ("url", "link", "href", "html_url", "pdf_url", "web_url", "doi_url")
_DOI_KEYS = ("doi", "DOI")
_JOURNAL_KEYS = (
    "journal", "source", "publication", "venue", "container-title",
    "journal_name", "期刊",
)
_CITED_KEYS = ("cited_count", "citation_count", "citations", "times_cited", "被引")


def _pick(d: Dict[str, Any], keys: tuple) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    low = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return None


def _author_name(a: Any) -> str:
    if isinstance(a, str):
        return a
    if isinstance(a, dict):
        return (
            a.get("full_name")
            or a.get("name")
            or a.get("family")
            or a.get("display_name")
            or a.get("literal")
            or ""
        )
    return str(a)


def _coerce_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v]
    return [v]


def _coerce_year(v: Any) -> str:
    if v is None or v == "":
        return ""
    s = str(v)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[:4] if len(digits) >= 4 else ""


class CustomHttpProvider(LiteratureProvider):
    """Adapter for a single user-defined literature source definition."""

    def __init__(self, definition: Dict[str, Any]):
        self.defn = dict(definition)
        self._name = definition.get("id") or "custom"
        self.label = definition.get("label", self._name)
        self._base_url = (definition.get("base_url") or "").strip().rstrip("/")
        self._auth = definition.get("auth_scheme", "bearer")
        self._api_key_header = definition.get("api_key_header") or "X-API-KEY"
        self._query_param = definition.get("query_param") or "q"
        self._method = (definition.get("method") or "GET").upper()
        self._fields = definition.get("fields", []) or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self.label
        self._base_url = (definition.get("base_url") or "").strip().rstrip("/")
        self._auth = definition.get("auth_scheme", "bearer")
        self._api_key_header = definition.get("api_key_header") or "X-API-KEY"
        self._query_param = definition.get("query_param") or "q"
        self._method = (definition.get("method") or "GET").upper()
        self._fields = definition.get("fields", []) or []

    # ── capability ──────────────────────────────────────────────────────────

    def supports_search(self) -> bool:
        return True

    def supports_fulltext(self) -> bool:
        return False

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.label,
            "badge": "自定义",
            "tag": "custom",
            "env_vars": [
                {"key": f["key"], "prompt": f.get("label", f["key"]), "url": None}
                for f in self._fields
            ],
        }

    # ── credential / endpoint resolution ─────────────────────────────────────

    def _creds(self) -> Dict[str, Optional[str]]:
        creds: Dict[str, Optional[str]] = {}
        for f in self._fields:
            kind = f.get("kind")
            if not kind:
                continue
            creds[kind] = os.environ.get(f["key"])
        return creds

    def _endpoint(self) -> str:
        # A "网关地址" credential field, if enabled, overrides the stored base.
        for f in self._fields:
            if f.get("kind") == "base_url":
                v = os.environ.get(f["key"])
                if v:
                    return v.strip().rstrip("/")
        return self._base_url

    def is_available(self) -> bool:
        endpoint = self._endpoint()
        if not endpoint:
            return False
        kinds = {f.get("kind") for f in self._fields}
        creds = self._creds()
        if self._auth in ("bearer", "header", "query") and "api_key" in kinds:
            return bool(creds.get("api_key"))
        if self._auth == "basic":
            return bool(creds.get("user") and creds.get("password"))
        return True

    # ── search ────────────────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        endpoint = self._endpoint()
        if not endpoint:
            return {
                "success": False,
                "error": (
                    f"自定义文献源 '{self.label}' 未配置网关地址"
                    f"（在设置 → 文献源 中填写接口地址或网关凭证字段）"
                ),
            }
        limit = max(1, min(int(limit or 10), 50))
        creds = self._creds()
        headers: Dict[str, str] = {}
        qp = self._query_param
        params: Dict[str, Any] = {}
        body: Dict[str, Any] = {}

        if self._method == "GET":
            params[qp] = query
            params["limit"] = limit
        else:
            body[qp] = query
            body["limit"] = limit

        ak = creds.get("api_key")
        user = creds.get("user")
        password = creds.get("password")
        target = params if self._method == "GET" else body

        if self._auth == "bearer" and ak:
            headers["Authorization"] = "Bearer " + ak
        elif self._auth == "header" and ak:
            headers[self._api_key_header] = ak
        elif self._auth == "basic" and user and password:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            headers["Authorization"] = "Basic " + token
        elif self._auth == "query" and ak:
            target["api_key"] = ak

        if user and self._auth != "basic":
            target["user"] = user
        if password and self._auth != "basic":
            target["password"] = password

        try:
            if self._method == "POST":
                r = http_post_json(endpoint, json_body=body, headers=headers)
            else:
                r = http_get_json(endpoint, params=params, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.debug("custom provider %s search failed: %s", self.name, exc)
            return {"success": False, "error": f"检索异常: {exc}"}

        if not r.get("ok"):
            return {"success": False, "error": r.get("error", "请求失败")}

        papers = self._parse(r.get("data") or r.get("text"), limit)
        return {
            "success": True,
            "data": {"papers": papers, "source": self.name, "count": len(papers)},
        }

    # ── tolerant response parsing ─────────────────────────────────────────────

    def _parse(self, payload: Any, limit: int) -> List[Dict[str, Any]]:
        items: List[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for k in (
                "results", "records", "data", "items", "papers", "hits",
                "docs", "documents", "list", "entries", "message",
            ):
                if isinstance(payload.get(k), list):
                    items = payload[k]
                    break
            if not items:  # fall back to any list-of-dicts value
                for v in payload.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        items = v
                        break

        out: List[Dict[str, Any]] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                if isinstance(it, str):
                    out.append({"title": it, "source": self.name})
                continue
            title = _pick(it, _TITLE_KEYS)
            if not title:
                continue  # skip records without a title
            authors = [_author_name(a) for a in _coerce_list(_pick(it, _AUTHORS_KEYS))]
            authors = [a for a in authors if a]
            cited = _pick(it, _CITED_KEYS)
            out.append(
                {
                    "title": str(title),
                    "authors": authors,
                    "year": _coerce_year(_pick(it, _YEAR_KEYS)),
                    "abstract": str(_pick(it, _ABSTRACT_KEYS) or ""),
                    "url": _pick(it, _URL_KEYS) or "",
                    "doi": _pick(it, _DOI_KEYS) or "",
                    "journal": _pick(it, _JOURNAL_KEYS) or "",
                    "cited_count": int(cited) if cited not in (None, "") else 0,
                    "source": self.name,
                }
            )
        return out
