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
  1. central config: ``config["services"][service_id]["api_key"]``
     (the user-configured API — the canonical source);
  2. env var: ``<SERVICE_ID_UPPER>_API_KEY`` by convention, or an explicit
     ``env_var`` (backward-compat with existing env usage);
  3. ``default`` (None).

The accessor never raises — missing credentials return None so callers can
gracefully disable the feature (same fail-soft contract as the rest of the
unified memory base).
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Service registry: service_id -> metadata declared by the plugin itself
# (env var names, human label, extra fields). The framework contains NO
# vendor names — only the generic resolution logic below.
_SERVICES: Dict[str, Dict[str, Any]] = {}

# Central config namespace that holds the user-configured API credentials.
_SERVICES_CONFIG_KEY = "services"


def register_service(
    service_id: str,
    *,
    api_key_env_var: Optional[str] = None,
    base_url_env_var: Optional[str] = None,
    label: str = "",
    extra_fields: Optional[List[str]] = None,
) -> None:
    """Declare a service's credential metadata (plugin-side, not framework).

    Call this once at plugin import time. It powers schema aggregation so the
    frontend can render every service's API fields from one source.
    """
    meta = _SERVICES.setdefault(service_id, {})
    if api_key_env_var:
        meta["api_key_env_var"] = api_key_env_var
    if base_url_env_var:
        meta["base_url_env_var"] = base_url_env_var
    if label:
        meta["label"] = label
    if extra_fields:
        ef = meta.setdefault("extra_fields", [])
        for f in extra_fields:
            if f not in ef:
                ef.append(f)


def _load_user_services() -> Dict[str, Any]:
    """Best-effort load of the user's central ``services`` config namespace."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        services = cfg.get(_SERVICES_CONFIG_KEY)
        if isinstance(services, dict):
            return services
    except Exception:
        pass
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
    return creds


def get_registered_services() -> Dict[str, Dict[str, Any]]:
    """Return service metadata for schema aggregation (frontend single source)."""
    return {sid: dict(meta) for sid, meta in _SERVICES.items()}
