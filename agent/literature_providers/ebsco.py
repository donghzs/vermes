"""EBSCO literature provider — 账号密码 (EDS API) driven.

Uses the EBSCO Discovery Service (EDS) REST API with UID auth: the user
supplies their institution's ``EBSCO_USER_ID`` / ``EBSCO_PASSWORD`` /
``EBSCO_PROFILE`` in the 文献源 settings form. Flow: UIDAuth → CreateSession
→ Search (all JSON).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_providers._http import http_get_json, http_post_json
from agent.service_credentials import register_service

logger = logging.getLogger(__name__)

_AUTH_URL = "https://eds-api.ebscohost.com/authservice/rest/UIDAuth"
_SESSION_URL = "https://eds-api.ebscohost.com/edsapi/rest/CreateSession"
_SEARCH_URL = "https://eds-api.ebscohost.com/edsapi/rest/Search"

register_service(
    "ebsco",
    label="EBSCO",
    category="literature",
    description="EBSCO Discovery Service — 用户自备机构账号密码 + Profile",
    url="https://developer.ebsco.com/",
    extra_fields=[
        {"key": "EBSCO_USER_ID", "label": "EBSCO 账号（User ID）", "secret": False},
        {"key": "EBSCO_PASSWORD", "label": "EBSCO 密码", "secret": True},
        {"key": "EBSCO_PROFILE", "label": "EDS Profile ID", "secret": False},
    ],
)


def _dig(d: Any, *path: str, default: Any = "") -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return cur if cur is not None else default


class EbscoProvider(LiteratureProvider):
    """EBSCO Discovery Service search (账号密码驱动)."""

    @property
    def name(self) -> str:
        return "ebsco"

    @property
    def display_name(self) -> str:
        return "EBSCO"

    def is_available(self) -> bool:
        return bool(
            os.environ.get("EBSCO_USER_ID")
            and os.environ.get("EBSCO_PASSWORD")
            and os.environ.get("EBSCO_PROFILE")
        )

    def supports_fulltext(self) -> bool:
        return False

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        user = os.environ.get("EBSCO_USER_ID")
        pwd = os.environ.get("EBSCO_PASSWORD")
        profile = os.environ.get("EBSCO_PROFILE")
        if not (user and pwd and profile):
            return {
                "success": False,
                "error": "EBSCO_USER_ID / EBSCO_PASSWORD / EBSCO_PROFILE 未配置 — 请在设置的「文献源」中填入",
            }

        auth = http_post_json(_AUTH_URL, json_body={"UserId": user, "Password": pwd})
        if not auth.get("ok"):
            return {"success": False, "error": f"EBSCO 认证失败: {auth.get('error')}"}
        token = auth["data"].get("AuthToken", "")
        if not token:
            return {"success": False, "error": "EBSCO 认证响应缺少 AuthToken"}

        sess = http_post_json(
            _SESSION_URL,
            json_body={"Profile": profile, "Guest": "n"},
            headers={"x-authenticationToken": token},
        )
        if not sess.get("ok"):
            return {"success": False, "error": f"EBSCO 会话创建失败: {sess.get('error')}"}
        session_token = sess["data"].get("SessionToken", "")

        res = http_get_json(
            _SEARCH_URL,
            params={
                "query": query,
                "resultsperpage": min(int(limit), 50),
                "pagenumber": 1,
                "view": "brief",
            },
            headers={
                "x-authenticationToken": token,
                "x-sessionToken": session_token,
            },
        )
        if not res.get("ok"):
            return {"success": False, "error": f"EBSCO 检索失败: {res.get('error')}"}

        records = _dig(res["data"], "SearchResult", "Data", "Records", default=[]) or []
        papers = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            items = {
                i.get("Name"): i.get("Data", "")
                for i in (_dig(rec, "Items", default=[]) or [])
                if isinstance(i, dict)
            }
            title = items.get("Title", "") or _dig(rec, "RecordInfo", "BibRecord", "BibEntity", "Titles", default="")
            if isinstance(title, list):
                title = (title[0] or {}).get("TitleFull", "") if title else ""
            if not title:
                continue
            papers.append(
                PaperRecord(
                    title=str(title),
                    authors=[a for a in str(items.get("Author", "")).split("; ") if a],
                    year="",
                    journal=str(items.get("TitleSource", ""))[:200],
                    abstract=str(items.get("Abstract", ""))[:800],
                    cited_count=0,
                    url=rec.get("PLink", "") or "",
                    doi="",
                    keywords=[],
                    source="ebsco",
                ).to_dict()
            )
        return {"success": True, "data": {"papers": papers}}

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "账号密码",
            "tag": "EBSCO Discovery Service — 填入机构账号密码 + Profile 即启用",
            "env_vars": [
                {"key": "EBSCO_USER_ID", "prompt": "EBSCO 账号（User ID）"},
                {"key": "EBSCO_PASSWORD", "prompt": "EBSCO 密码"},
                {"key": "EBSCO_PROFILE", "prompt": "EDS Profile ID"},
            ],
        }
