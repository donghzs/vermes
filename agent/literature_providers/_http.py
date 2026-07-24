"""Shared HTTP helpers for literature providers.

Thin wrappers around httpx with the fail-soft contract every provider uses:
never raise — return ``{"ok": bool, ...}`` so callers can degrade gracefully.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20


def http_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """GET *url* expecting a JSON body. Returns {ok, data|error, status}."""
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx 未安装 — pip install httpx"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, params=params or {}, headers=headers or {})
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code} from {url.split('?')[0]}",
                }
            return {"ok": True, "status": 200, "data": resp.json()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("http_get_json(%s) failed: %s", url, exc)
        return {"ok": False, "error": str(exc)}


def http_get_text(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """GET *url* returning the raw text body (e.g. arXiv Atom XML)."""
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx 未安装 — pip install httpx"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, params=params or {}, headers=headers or {})
            if resp.status_code != 200:
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code} from {url.split('?')[0]}",
                }
            return {"ok": True, "status": 200, "text": resp.text}
    except Exception as exc:  # noqa: BLE001
        logger.debug("http_get_text(%s) failed: %s", url, exc)
        return {"ok": False, "error": str(exc)}


def http_post_json(
    url: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """POST JSON to *url* expecting a JSON body back."""
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx 未安装 — pip install httpx"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(url, json=json_body or {}, headers=headers or {})
            if resp.status_code not in (200, 201):
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code} from {url.split('?')[0]}",
                }
            return {"ok": True, "status": resp.status_code, "data": resp.json()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("http_post_json(%s) failed: %s", url, exc)
        return {"ok": False, "error": str(exc)}


def http_login_then_search(
    *,
    login_url: str,
    login_payload: Dict[str, Any],
    search_url: str,
    search_method: str = "GET",
    search_params: Optional[Dict[str, Any]] = None,
    search_json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Card-gateway pattern: POST a form login to get a session cookie, then
    run the search request *on the same session* so the auth cookie is carried.

    Used by custom sources with ``auth_scheme == "form"`` (e.g. purchased
    third-party literature portals accessed by 卡号+密码). Login success is
    best-effort — we only fail loudly if the *search* request itself fails;
    a bad login will surface as an empty/erroring search the user can debug.
    """
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "httpx 未安装 — pip install httpx"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # 1) form login → session cookie
            client.post(login_url, data=login_payload or {}, headers=headers or {})
            # 2) search with the established session
            if search_method.upper() == "POST":
                resp = client.post(
                    search_url,
                    json=search_json or {},
                    params=search_params or {},
                    headers=headers or {},
                )
            else:
                resp = client.get(
                    search_url, params=search_params or {}, headers=headers or {}
                )
            if resp.status_code not in (200, 201):
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "error": f"HTTP {resp.status_code} from {search_url.split('?')[0]}",
                }
            ctype = resp.headers.get("content-type", "")
            if "json" in ctype:
                return {"ok": True, "status": resp.status_code, "data": resp.json()}
            return {"ok": True, "status": resp.status_code, "text": resp.text}
    except Exception as exc:  # noqa: BLE001
        logger.debug("http_login_then_search(%s) failed: %s", search_url, exc)
        return {"ok": False, "error": str(exc)}
