"""Declarative runtime presets (取法 dsh ``agent.cordis.yml``).

A preset is a named bundle of runtime preferences (``api_mode`` / ``model`` /
``toolset`` / ``context_budget`` / ``sandbox``) that can be activated in one
shot instead of re-deriving everything from URL/provider heuristics.

Design constraints (沿用 Vermes fail-open 纪律):
- Loading a preset must NEVER raise into the caller. On any failure we return
  ``None`` and the resolver falls back to its existing URL/provider derivation.
- Presets are additive hints, not hard overrides: only the fields a preset
  actually declares are applied; everything else keeps the derived value.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Built-in presets. Vertical-domain modules (scholarforge / mfgcad /
# designstudio / robotforge) can ship their own by registering via
# ``register_preset`` at import time, or by dropping a YAML next to this file
# later. For now the four domain presets are declared here so the resolver has
# something concrete to consume without requiring the heavy vertical modules
# to be imported (avoiding import-time side effects / circular deps).
_BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    "default": {
        "api_mode": None,  # None => let the resolver derive api_mode
        "model": None,
        "toolset": "auto",
        "context_budget": "auto",
        "sandbox": "inherit",
    },
    "scholarforge": {
        "api_mode": None,
        "model": None,
        "toolset": "scholarforge",
        "context_budget": "large",
        "sandbox": "inherit",
    },
    "mfgcad": {
        "api_mode": None,
        "model": None,
        "toolset": "mfgcad",
        "context_budget": "large",
        "sandbox": "inherit",
    },
    "designstudio": {
        "api_mode": None,
        "model": None,
        "toolset": "designstudio",
        "context_budget": "large",
        "sandbox": "inherit",
    },
    "robotforge": {
        "api_mode": None,
        "model": None,
        "toolset": "robotforge",
        "context_budget": "large",
        "sandbox": "inherit",
    },
}

# Domain presets may register themselves at runtime (e.g. when a vertical
# module is activated). Kept separate from the built-ins so built-ins stay
# import-cheap and the registry is the merge target.
_REGISTERED_PRESETS: Dict[str, Dict[str, Any]] = {}


def register_preset(name: str, spec: Dict[str, Any]) -> None:
    """Register or override a preset at runtime (fail-open, no validation)."""
    if not name or not isinstance(spec, dict):
        logger.warning("register_preset ignored: bad name=%r spec=%r", name, spec)
        return
    _REGISTERED_PRESETS[name] = dict(spec)


def list_presets() -> list[str]:
    """Return all known preset names (built-in + registered)."""
    return sorted(set(_BUILTIN_PRESETS) | set(_REGISTERED_PRESETS))


def load_preset(name: str) -> Optional[Dict[str, Any]]:
    """Load a preset by name.

    Returns a *copy* of the preset spec, or ``None`` when the preset is
    unknown or the name is unsafe. Never raises.
    """
    if not name or not isinstance(name, str):
        return None
    # Reject path-like / metachar inputs to avoid any filesystem traversal.
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        logger.warning("load_preset rejected unsafe name: %r", name)
        return None
    spec = _REGISTERED_PRESETS.get(name) or _BUILTIN_PRESETS.get(name)
    if spec is None:
        logger.debug("load_preset: unknown preset %r", name)
        return None
    return dict(spec)
