"""Unified service-credential access for business plugins / tools / skills.

Vermes design rule (owner directive): every component that calls an external
API must read its credentials from the USER's central API configuration — NOT
each plugin scattering its own ``os.environ.get("XXX_API_KEY")`` reads. This:

  * lets the desktop frontend render ONE API section instead of per-plugin
    forms (it cannot possibly enumerate every API-needing plugin/tool/skill);
  * makes "API problems" tractable in a single place (the user's API settings);
  * keeps the framework vendor-agnostic — plugins declare their own service
    metadata; the accessor is generic (no vendor names live here).

Resolution order for a service's api_key:
  1. central config overlay: ``config["services"][service_id]["api_key"]``
     (an OPTIONAL overlay the user/operator may set; when present it wins);
  2. env var: ``<SERVICE_ID_UPPER>_API_KEY`` by convention, or an explicit
     ``env_var`` (backward-compat with existing env usage).

     The desktop frontend writes credentials here: ``PUT /api/env`` →
     ``save_env_value`` → the active ``.env`` file. So in the normal UI flow
     the central ``config["services"]`` is empty and the env fallback is what
     actually delivers the user-set key to the agent at runtime. Both paths
     are read; config overrides env only when explicitly populated.
  3. ``default`` (None).

The accessor never raises — missing credentials return None so callers can
gracefully disable the feature (same fail-soft contract as the rest of the
unified memory base).
"""
from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

import os
from typing import Any, Dict, List, Optional

# Service registry: service_id -> metadata declared by the plugin itself
# (env var names, human label, extra fields). The framework contains NO
# vendor names — only the generic resolution logic below.
_SERVICES: Dict[str, Dict[str, Any]] = {}

# Central config namespace that holds the user-configured API credentials.
_SERVICES_CONFIG_KEY = "services"


# Env-var name fragments that imply a secret (masked) input field.
_SECRET_HINTS = ("KEY", "PASSWORD", "SECRET", "TOKEN", "PASSWD", "PWD")


def _normalize_extra_field(field: Any) -> Dict[str, Any]:
    """Normalize an extra_fields entry (str or dict) to a canonical dict.

    Accepted forms:
      * ``"CNKI_USERNAME"`` — bare env-var name; label defaults to the name,
        secret-ness inferred from the name (PASSWORD/TOKEN/... → secret).
      * ``{"key": "CNKI_USERNAME", "label": "账号", "secret": False}`` — full
        declaration (only ``key`` is required).
    """
    if isinstance(field, dict):
        key = str(field.get("key", "")).strip()
        out = {"key": key}
        if field.get("label"):
            out["label"] = str(field["label"])
        if "secret" in field:
            out["secret"] = bool(field["secret"])
        return out
    return {"key": str(field).strip()}


def register_service(
    service_id: str,
    *,
    api_key_env_var: Optional[str] = None,
    base_url_env_var: Optional[str] = None,
    label: str = "",
    extra_fields: Optional[List[Any]] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    url: Optional[str] = None,
) -> None:
    """Declare a service's credential metadata (plugin-side, not framework).

    Call this once at plugin import time. It powers schema aggregation so the
    frontend can render every service's API fields from one source.

    ``category`` groups services in the settings UI (default ``"services"``;
    e.g. literature providers register with ``category="literature"`` so the
    frontend can render a dedicated "文献源" tab). ``extra_fields`` entries may
    be bare env-var names or dicts ``{key, label, secret}`` for richer
    rendering (username/password/gateway fields).
    """
    meta = _SERVICES.setdefault(service_id, {})
    if api_key_env_var:
        meta["api_key_env_var"] = api_key_env_var
    if base_url_env_var:
        meta["base_url_env_var"] = base_url_env_var
    if label:
        meta["label"] = label
    if category:
        meta["category"] = category
    if description:
        meta["description"] = description
    if url:
        meta["url"] = url
    if extra_fields:
        ef = meta.setdefault("extra_fields", [])
        known = {(_f.get("key") if isinstance(_f, dict) else _f) for _f in ef}
        for f in extra_fields:
            nf = _normalize_extra_field(f)
            if nf["key"] and nf["key"] not in known:
                ef.append(nf)
                known.add(nf["key"])
            elif nf["key"] in known and (len(nf) > 1):
                # Merge richer metadata into a previously bare declaration.
                for idx, existing in enumerate(ef):
                    ekey = existing.get("key") if isinstance(existing, dict) else existing
                    if ekey == nf["key"]:
                        merged = _normalize_extra_field(existing)
                        merged.update(nf)
                        ef[idx] = merged
                        break


def _is_secret_key(env_key: str) -> bool:
    upper = env_key.upper()
    return any(h in upper for h in _SECRET_HINTS)


def get_service_fields(service_id: str) -> List[Dict[str, Any]]:
    """Return the ordered, deduplicated credential field list for a service.

    Each field: ``{"key": ENV_VAR, "kind": "api_key"|"base_url"|"extra",
    "label": str, "secret": bool}``. This is the single source the schema
    endpoint / env allowlist / frontend form all derive from.
    """
    meta = _SERVICES.get(service_id, {})
    label = meta.get("label", service_id)
    fields: List[Dict[str, Any]] = []
    seen: set = set()

    ak = meta.get("api_key_env_var")
    if ak:
        fields.append({"key": ak, "kind": "api_key", "label": f"{label} API Key", "secret": True})
        seen.add(ak)
    bu = meta.get("base_url_env_var")
    if bu and bu not in seen:
        fields.append({"key": bu, "kind": "base_url", "label": f"{label} Base URL", "secret": False})
        seen.add(bu)
    for ef in meta.get("extra_fields", []) or []:
        nf = _normalize_extra_field(ef)
        key = nf.get("key")
        if not key or key in seen:
            continue
        fields.append({
            "key": key,
            "kind": "extra",
            "label": nf.get("label") or key,
            "secret": nf.get("secret", _is_secret_key(key)),
        })
        seen.add(key)
    return fields


def _load_user_services() -> Dict[str, Any]:
    """Best-effort load of the user's central ``services`` config namespace."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        services = cfg.get(_SERVICES_CONFIG_KEY)
        if isinstance(services, dict):
            return services
    except Exception as e:
        logger.debug("service_credentials.py:  load user services failed: %s", e)
    return {}


def get_api_key(service_id: str, *, env_var: Optional[str] = None, default: Optional[str] = None) -> Optional[str]:
    """Resolve a service's API key from the user's central config, then env.

    ``service_id`` is the plugin's stable identifier (e.g. ``"supermemory"``).
    By convention the fallback env var is ``<SERVICE_ID_UPPER>_API_KEY`` unless
    ``env_var`` is given (for non-conventional names like ``BRV_API_KEY``).
    """
    svc = _load_user_services().get(service_id) or {}
    key = svc.get("api_key")
    if key:
        return key
    meta = _SERVICES.get(service_id, {})
    env_name = env_var or meta.get("api_key_env_var") or f"{service_id.upper()}_API_KEY"
    return os.environ.get(env_name, default)


def get_service_credentials(
    service_id: str,
    *,
    base_url_env_var: Optional[str] = None,
    default_base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a service's ``{api_key, base_url}`` from central config then env.

    Returns a dict; ``base_url`` is omitted when unresolved (callers that don't
    need it won't be surprised by a None entry unless one was configured).
    """
    svc = _load_user_services().get(service_id) or {}
    meta = _SERVICES.get(service_id, {})
    api_key = svc.get("api_key") or os.environ.get(
        meta.get("api_key_env_var") or f"{service_id.upper()}_API_KEY"
    )
    bu_env = base_url_env_var or meta.get("base_url_env_var")
    base_url = (
        svc.get("base_url")
        or (os.environ.get(bu_env) if bu_env else None)
        or default_base_url
    )
    creds: Dict[str, Any] = {"api_key": api_key}
    if base_url is not None:
        creds["base_url"] = base_url
    # R1 fix: surface declared extra_fields (e.g. CNKI_USERNAME/CNKI_PASSWORD)
    # so callers like the CNKI fetcher can use account+password auth, not just
    # the api_key. Only keys the service explicitly declared are exposed.
    for ef in meta.get("extra_fields", []) or []:
        ek = ef.get("key")
        if ek and ek not in creds and svc.get(ek):
            creds[ek] = svc[ek]
    return creds


def get_registered_services() -> Dict[str, Dict[str, Any]]:
    """Return service metadata for schema aggregation (frontend single source).

    Each entry carries the raw registration metadata plus a computed
    ``fields`` list (see :func:`get_service_fields`) and a resolved
    ``category`` (default ``"services"``) so consumers never re-derive them.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for sid, meta in _SERVICES.items():
        entry = dict(meta)
        entry.setdefault("category", "services")
        entry.setdefault("label", sid)
        entry["fields"] = get_service_fields(sid)
        out[sid] = entry
    # Merge user-defined custom literature sources (institutions' internal
    # portals) so the same form / whitelist / masking applies to them for free.
    try:
        from agent.literature_custom_store import get_custom_service_entries

        for _sid, _entry in get_custom_service_entries().items():
            if _sid in out:
                continue
            _entry = dict(_entry)
            _entry.setdefault("category", "literature")
            _entry.setdefault("label", _sid)
            _entry.setdefault("fields", [])
            out[_sid] = _entry
    except Exception:
        logger.debug("get_registered_services: skipped custom sources")
    return out
