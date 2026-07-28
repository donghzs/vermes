"""Blueprint: Skills & Tools（技能与工具集管理）

Vermes skills and toolsets endpoints.
"""

import json
import logging
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

_log = logging.getLogger(__name__)


# ── models ─────────────────────────────────────────────────────

class SkillToggle(BaseModel):
    name: str
    enabled: bool


class ToolsetToggle(BaseModel):
    enabled: bool


class SkillInstallRequest(BaseModel):
    identifier: str
    name: Optional[str] = None
    source: Optional[str] = None
    force: bool = False


class UsageRecord(BaseModel):
    kind: str                       # "expert" | "skill"
    id: str                         # stable capability id
    title: Optional[str] = None    # human label (for readability)
    scope: Optional[str] = ""      # user/session scope ("" = global)


# ── route handlers ─────────────────────────────────────────────

async def get_skills():
    from tools.skills_tool import _find_all_skills
    from hermes_cli.skills_config import get_disabled_skills
    from hermes_cli.config import load_config

    config = load_config()
    disabled = get_disabled_skills(config)
    skills = _find_all_skills(skip_disabled=True)
    for s in skills:
        s["enabled"] = s["name"] not in disabled
    return skills


async def toggle_skill(body: SkillToggle):
    from hermes_cli.skills_config import get_disabled_skills, save_disabled_skills
    from hermes_cli.config import load_config

    config = load_config()
    disabled = get_disabled_skills(config)
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": body.name, "enabled": body.enabled}


async def get_toolsets():
    from hermes_cli.tools_config import (
        _get_effective_configurable_toolsets,
        get_effective_web_toolset_keys,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset
    from hermes_cli.config import load_config

    config = load_config()
    # Reflect what the web/desktop agent actually runs (platform_toolsets.web),
    # not the CLI config — the app is the web platform.
    enabled_toolsets = get_effective_web_toolset_keys(config)
    result = []
    for name, label, desc in _get_effective_configurable_toolsets():
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        is_enabled = name in enabled_toolsets
        result.append({
            "name": name, "label": label, "description": desc,
            "enabled": is_enabled,
            "available": is_enabled,
            "configured": _toolset_has_keys(name, config),
            "tools": tools,
        })
    return result


async def toggle_toolset(name: str, body: ToolsetToggle):
    """Enable/disable a toolset for the web/desktop platform.

    Persists to ``platform_toolsets.web``. The web chat backend re-reads
    config per request, so the change takes effect on the next conversation
    without a restart.

    Crucially we toggle against the user's *explicit* ``.web`` list, not the
    governed resolver output: the resolver drops the ``hermes-cli`` composite
    (a platform default carrying terminal/skills/vision) and re-expands it
    lazily. Saving the resolver's output would strip ``hermes-cli`` and silently
    amputate the agent's core tools. Toggling the raw list preserves it.
    """
    from hermes_cli.tools_config import (
        get_effective_web_toolset_keys,
        _save_platform_tools,
        _get_effective_configurable_toolsets,
    )
    from hermes_cli.config import load_config
    from fastapi import HTTPException

    # Validate the toolset name *before* mutating config. Only real,
    # UI-toggleable toolsets (built-in + plugin) may be toggled — this stops
    # garbage keys (typos, injected names) from being persisted into
    # platform_toolsets.web, which previously would silently pollute config.
    valid_keys = {ts_key for ts_key, _, _ in _get_effective_configurable_toolsets()}
    if name not in valid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or non-toggleable toolset: {name!r}",
        )

    config = load_config()
    raw_web = (config.get("platform_toolsets") or {}).get("web")
    if raw_web:
        base = set(raw_web)
    else:
        # No explicit .web yet (fresh install) — start from the governed
        # default set, then flip the requested toolset.
        base = set(get_effective_web_toolset_keys(config))
    if body.enabled:
        base.add(name)
    else:
        base.discard(name)
    _save_platform_tools(config, "web", base)
    return {"ok": True, "name": name, "enabled": body.enabled}


# ── market handlers ────────────────────────────────────────────

async def get_experts():
    """Curated expert catalog (qclaw-style) merged with live skill status."""
    from tools.skills_tool import _find_all_skills
    from hermes_cli.skills_config import get_disabled_skills
    from hermes_cli.config import load_config

    catalog_path = Path(__file__).parent.parent / "experts_catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("Failed to load experts catalog: %s", exc)
        catalog = []

    config = load_config()
    disabled = set(get_disabled_skills(config))
    try:
        installed_skills = _find_all_skills(skip_disabled=True)
    except Exception:
        installed_skills = []
    installed_names = {s.get("name") for s in installed_skills}

    out = []
    for e in catalog:
        skill_names = e.get("skills", []) or []
        skills_status = []
        for sn in skill_names:
            is_installed = sn in installed_names
            skills_status.append({
                "name": sn,
                "installed": is_installed,
                "enabled": is_installed and sn not in disabled,
            })
        ready = all(s["installed"] for s in skills_status) if skills_status else True
        out.append({**e, "skills_status": skills_status, "ready": ready})
    return out


class _CaptureConsole:
    """Rich-console stand-in that records printed text (no TTY needed)."""

    def __init__(self):
        self.lines: List[str] = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    def status(self, *args, **kwargs):
        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return _Ctx()

    def rule(self, *args, **kwargs):
        pass


async def market_search(q: str = "", source: str = "all", limit: int = 24):
    """Search the skills hub (official / clawhub / github / lobehub / ...)."""
    from tools.skills_hub import (
        GitHubAuth, create_source_router, unified_search, _skill_meta_to_dict,
    )
    try:
        auth = GitHubAuth()
        sources = create_source_router(auth)
        results = unified_search(
            q or "", sources,
            source_filter=source or "all",
            limit=max(1, min(int(limit), 100)),
        )
    except Exception as exc:
        _log.warning("Skills market search failed: %s", exc)
        return {"items": [], "total": 0, "error": str(exc)}
    items = [_skill_meta_to_dict(r) for r in results]
    return {"items": items, "total": len(items), "query": q, "source": source}


async def market_install(body: SkillInstallRequest):
    """Install a skill non-interactively (quarantine + scan + install)."""
    from hermes_cli.skills_hub import do_install
    from tools.skills_hub import HubLockFile
    cap = _CaptureConsole()
    installed_name = (body.name or "").strip()
    try:
        do_install(
            body.identifier,
            category="",
            force=body.force,
            console=cap,
            skip_confirm=True,
            name_override=installed_name,
        )
    except Exception as exc:
        return {"ok": False, "name": installed_name, "message": str(exc)}
    lock = HubLockFile()
    resolved = installed_name or body.identifier.split("/")[-1]
    if lock.get_installed(resolved):
        return {"ok": True, "name": resolved, "message": "已安装"}
    return {"ok": False, "name": resolved, "message": "\n".join(cap.lines) or "安装失败"}


async def market_uninstall(name: str):
    """Uninstall a hub-installed skill."""
    from tools.skills_hub import uninstall_skill
    try:
        ok, msg = uninstall_skill(name)
        return {"ok": ok, "name": name, "message": msg}
    except Exception as exc:
        return {"ok": False, "name": name, "message": str(exc)}


# ── usage telemetry (越用越懂用户) ──────────────────────────────

async def record_usage(body: UsageRecord):
    """Record one capability-usage event into the unified memory base."""
    from agent.memory_fabric import record_usage as _record
    try:
        _record(body.kind, body.id, body.title or "", body.scope or "")
        return {"ok": True, "kind": body.kind, "id": body.id}
    except Exception as exc:
        _log.warning("record_usage failed: %s", exc)
        return {"ok": False, "kind": body.kind, "id": body.id, "message": str(exc)}


async def get_usage_recommend(kind: Optional[str] = None, limit: int = 4):
    """Top-used capabilities (experts/skills) for "你可能想用"."""
    from agent.memory_fabric import get_usage_counts
    try:
        items = get_usage_counts(kind, "", max(1, min(int(limit), 20)))
    except Exception as exc:
        _log.warning("usage recommend failed: %s", exc)
        items = []
    return {"items": items, "kind": kind, "limit": limit}


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register skills & tools routes on the FastAPI app."""
    app.add_api_route(
        "/api/skills", get_skills, methods=["GET"], name="get_skills"
    )
    app.add_api_route(
        "/api/skills/toggle", toggle_skill, methods=["PUT"], name="toggle_skill"
    )
    app.add_api_route(
        "/api/tools/toolsets", get_toolsets, methods=["GET"], name="get_toolsets"
    )
    app.add_api_route(
        "/api/tools/toolsets/{name}", toggle_toolset, methods=["PUT"], name="toggle_toolset"
    )
    app.add_api_route(
        "/api/skills/market", market_search, methods=["GET"], name="market_search"
    )
    app.add_api_route(
        "/api/experts", get_experts, methods=["GET"], name="get_experts"
    )
    app.add_api_route(
        "/api/skills/install", market_install, methods=["POST"], name="market_install"
    )
    app.add_api_route(
        "/api/skills/{name}", market_uninstall, methods=["DELETE"], name="market_uninstall"
    )
    app.add_api_route(
        "/api/usage", record_usage, methods=["POST"], name="record_usage"
    )
    app.add_api_route(
        "/api/usage/recommend", get_usage_recommend, methods=["GET"], name="get_usage_recommend"
    )


blueprint = None  # no APIRouter; uses register_to(app) pattern
