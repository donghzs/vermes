"""User-defined (custom) literature source definitions.

Institutions — universities, hospitals, corporate / enterprise R&D labs — often
run **internal** multi-database literature portals behind a gateway, API key, or
SSO. Vermes lets the user register an **unlimited** number of such sources from
the Settings UI (the very same "文献源设置" form used for built-in sources).

Each definition is persisted to ``<VERMES_HOME>/literature_custom_sources.json``
and merged into the unified :mod:`agent.service_credentials` registry at request
time, so the form rendering, credential whitelist, set-status display, and value
masking that built-in sources enjoy apply to custom sources for free — no
hardcoded vendor names, no framework changes per source.

The credentials themselves are NOT stored here; only the *metadata* (which
fields a source needs). Actual API keys / usernames / passwords are written to
the central ``.env`` via ``PUT /api/env`` under the namespaced env var names
generated below (``LIT_<ID>_API_KEY`` …), so they get the same masking + audit
treatment as every other secret.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = __import__("logging").getLogger(__name__)

# Env-var prefix for every custom-source credential field. Namespaced so a
# user's "API Key" for their hospital portal can never collide with a built-in
# service's key.
ENV_PREFIX = "LIT_"

# Canonical credential-field types the user can toggle on per source. Order is
# fixed so generated env var names are deterministic across edits.
_FIELD_TYPES: Dict[str, Dict[str, Any]] = {
    "api_key":  {"suffix": "API_KEY",  "label": "API Key",  "secret": True,  "kind": "api_key"},
    "base_url": {"suffix": "BASE_URL", "label": "网关地址", "secret": False, "kind": "base_url"},
    "user":     {"suffix": "USER",     "label": "账号",     "secret": False, "kind": "user"},
    "password": {"suffix": "PASSWORD", "label": "密码",     "secret": True,  "kind": "password"},
}
_FIELD_ORDER = ("api_key", "base_url", "user", "password")

# Authentication schemes a custom source may use.
AUTH_SCHEMES = ("none", "bearer", "basic", "header", "query")

# In-memory cache keyed on file mtime to avoid re-parsing on every request.
_cache: Dict[str, Any] = {"mtime": 0.0, "data": None}
_store_path: Optional[Path] = None


def _resolve_store_path() -> Path:
    global _store_path
    if _store_path is None:
        try:
            from hermes_cli.config import get_hermes_home

            base = Path(get_hermes_home())
        except Exception:  # noqa: BLE001
            base = Path.home() / ".vermes"
        _store_path = base / "literature_custom_sources.json"
    return _store_path


def _read_all() -> List[Dict[str, Any]]:
    """Return the list of raw custom-source definitions (cache by mtime)."""
    path = _resolve_store_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _cache["data"] is not None and _cache["mtime"] == mtime:
        return _cache["data"]  # type: ignore[return-value]
    try:
        raw = path.read_text(encoding="utf-8") or "[]"
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        data = []
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _write_all(items: List[Dict[str, Any]]) -> None:
    path = _resolve_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _cache["mtime"] = path.stat().st_mtime
    _cache["data"] = items


def _slugify(text: str) -> str:
    """Turn a human label into a safe source id fragment."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    # ids must start with a letter (env-var / provider-name safety)
    if s and s[0].isdigit():
        s = "s_" + s
    return s


def _build_fields(source_id: str, field_types: List[str]) -> List[Dict[str, Any]]:
    """Generate the credential-field list for a source id from chosen types."""
    fields: List[Dict[str, Any]] = []
    seen = set()
    for ft in _FIELD_ORDER:
        if ft not in field_types:
            continue
        meta = _FIELD_TYPES[ft]
        env_key = f"{ENV_PREFIX}{source_id.upper()}_{meta['suffix']}"
        if env_key in seen:
            continue
        seen.add(env_key)
        fields.append(
            {
                "key": env_key,
                "kind": meta["kind"],
                "label": meta["label"],
                "secret": bool(meta["secret"]),
            }
        )
    return fields


def _normalize_definition(raw: Dict[str, Any], *, source_id: str) -> Dict[str, Any]:
    """Coerce a stored/created definition into canonical shape (id preserved)."""
    field_types = [ft for ft in _FIELD_ORDER if ft in (raw.get("field_types") or [])]
    auth = raw.get("auth_scheme", "bearer")
    if auth not in AUTH_SCHEMES:
        auth = "bearer"
    method = (raw.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        method = "GET"
    return {
        "id": source_id,
        "label": (raw.get("label") or source_id).strip() or source_id,
        "description": (raw.get("description") or "").strip(),
        "url": (raw.get("url") or "").strip(),
        "base_url": (raw.get("base_url") or "").strip(),
        "auth_scheme": auth,
        "api_key_header": (raw.get("api_key_header") or "X-API-KEY").strip(),
        "query_param": (raw.get("query_param") or "q").strip() or "q",
        "method": method,
        "field_types": field_types,
        "fields": _build_fields(source_id, field_types),
        "created_at": raw.get("created_at") or time.time(),
        "updated_at": time.time(),
    }


def list_custom_sources() -> List[Dict[str, Any]]:
    """Return all persisted custom-source definitions (raw, with ``fields``)."""
    return _read_all()


def get_custom_source(source_id: str) -> Optional[Dict[str, Any]]:
    for d in _read_all():
        if d.get("id") == source_id:
            return d
    return None


def _unique_id(desired: str) -> str:
    base = _slugify(desired)
    # Non-ASCII labels (e.g. Chinese) slugify to empty — fall back to a random
    # but stable fragment so the env-var / provider name is still unique.
    if not base:
        base = "src_" + uuid.uuid4().hex[:6]
    existing = {d.get("id") for d in _read_all()}
    if base not in existing:
        return base
    return f"{base}_{uuid.uuid4().hex[:6]}"


def add_custom_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a custom source. Returns the persisted definition."""
    label = (payload.get("label") or "").strip()
    if not label:
        raise ValueError("自定义文献库名称不能为空")
    source_id = _unique_id(label)
    definition = _normalize_definition(payload, source_id=source_id)
    items = _read_all()
    items.append(definition)
    _write_all(items)
    logger.info("Added custom literature source '%s' (id=%s)", label, source_id)
    return definition


def update_custom_source(source_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a custom source's metadata. Env-var names stay tied to *source_id*,
    so credentials already saved in ``.env`` survive an edit."""
    items = _read_all()
    for i, d in enumerate(items):
        if d.get("id") == source_id:
            merged = dict(d)
            for k in (
                "label", "description", "url", "base_url", "auth_scheme",
                "api_key_header", "query_param", "method", "field_types",
            ):
                if k in payload:
                    merged[k] = payload[k]
            items[i] = _normalize_definition(merged, source_id=source_id)
            _write_all(items)
            logger.info("Updated custom literature source id=%s", source_id)
            return items[i]
    return None


def delete_custom_source(source_id: str) -> bool:
    """Remove a custom source and best-effort purge its saved credentials."""
    items = _read_all()
    kept = [d for d in items if d.get("id") != source_id]
    if len(kept) == len(items):
        return False
    _write_all(kept)
    # purge orphaned credentials so no secrets linger in .env
    try:
        from hermes_cli.env import remove_env_value
    except Exception:  # noqa: BLE001
        try:
            from hermes_cli.config import remove_env_value  # type: ignore
        except Exception:  # noqa: BLE001
            remove_env_value = None  # type: ignore
    if remove_env_value:
        for d in items:
            if d.get("id") == source_id:
                for f in d.get("fields", []):
                    try:
                        remove_env_value(f.get("key"))
                    except Exception:  # noqa: BLE001
                        pass
    logger.info("Deleted custom literature source id=%s", source_id)
    return True


def get_custom_service_entries() -> Dict[str, Dict[str, Any]]:
    """Return registry-shaped entries (category='literature') merged into
    :func:`agent.service_credentials.get_registered_services`."""
    out: Dict[str, Dict[str, Any]] = {}
    for d in _read_all():
        sid = d.get("id")
        if not sid:
            continue
        fields = d.get("fields") or []
        norm_fields = [
            {
                "key": f["key"],
                "kind": f.get("kind", "extra"),
                "label": f.get("label", f["key"]),
                "secret": bool(f.get("secret")),
            }
            for f in fields
        ]
        out[sid] = {
            "id": sid,
            "label": d.get("label", sid),
            "description": d.get("description", ""),
            "url": d.get("url", ""),
            "category": "literature",
            "custom": True,
            "fields": norm_fields,
        }
    return out
