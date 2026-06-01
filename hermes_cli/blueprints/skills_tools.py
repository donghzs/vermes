"""Blueprint: Skills & Tools（技能与工具集管理）

Vermes skills and toolsets endpoints.
"""

import logging
from typing import List

from pydantic import BaseModel

_log = logging.getLogger(__name__)


# ── models ─────────────────────────────────────────────────────

class SkillToggle(BaseModel):
    name: str
    enabled: bool


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
        _get_platform_tools,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset
    from hermes_cli.config import load_config

    config = load_config()
    enabled_toolsets = _get_platform_tools(
        config,
        "cli",
        include_default_mcp_servers=False,
    )
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


blueprint = None  # no APIRouter; uses register_to(app) pattern
