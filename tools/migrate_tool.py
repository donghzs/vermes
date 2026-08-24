#!/usr/bin/env python3
"""Agent tool: migrate configuration/skills/memories from another agent into Vermes.

Supports two sources:
  - hermes    (official Hermes Agent — structurally isomorphic to Vermes)
  - openclaw  (OpenClaw — via the openclaw-migration skill script)

This lets a user type, in the chat window, something like:
  "帮我把 Hermes 的配置迁移过来"  or  "从 OpenClaw 迁移我的技能和记忆"

and the agent invokes migrate_agent(source="hermes") directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

MIGRATE_AGENT_SCHEMA = {
    "name": "migrate_agent",
    "description": (
        "Migrate persona, memories, skills, and (optionally) allowlisted secrets "
        "from another AI agent into Vermes. Supported sources: 'hermes' (the "
        "official Hermes Agent) and 'openclaw' (OpenClaw). Always performs a "
        "dry-run preview first and reports what would change. Set execute=true "
        "only after the user has reviewed the preview (or explicitly asked to "
        "proceed). "
        "Example: user says '从 Hermes 迁移配置' → migrate_agent(source='hermes')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["hermes", "openclaw"],
                "description": "Which agent to migrate from. 'hermes' = official Hermes Agent (~/.hermes). 'openclaw' = OpenClaw (~/.openclaw).",
            },
            "execute": {
                "type": "boolean",
                "default": False,
                "description": "False = dry-run preview only (default). True = actually apply the migration after preview.",
            },
            "overwrite": {
                "type": "boolean",
                "default": False,
                "description": "Overwrite existing target files. Default false (skip conflicts).",
            },
            "migrate_secrets": {
                "type": "boolean",
                "default": False,
                "description": "Include allowlisted secrets (API keys). Default false — secrets are never migrated implicitly.",
            },
            "migrate_skills": {
                "type": "boolean",
                "default": True,
                "description": "Migrate missing skills (default true). Set false to skip skill migration.",
            },
        },
        "required": ["source"],
    },
}


def migrate_agent(
    source: str = "hermes",
    execute: bool = False,
    overwrite: bool = False,
    migrate_secrets: bool = False,
    migrate_skills: bool = True,
    **kwargs: Any,
) -> str:
    """Tool handler. Returns a human-readable migration summary."""
    source = (source or "hermes").strip().lower()

    if source == "hermes":
        from vermes_cli.hermes_migrate import (
            _find_hermes_home,
            format_result,
            migrate_from_hermes,
        )

        home = _find_hermes_home()
        if home is None:
            return "未找到 Hermes 目录（~/.hermes）。请确认已安装官方 Hermes Agent。"

        result = migrate_from_hermes(
            hermes_home=home,
            dry_run=not execute,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            migrate_skills=migrate_skills,
        )
        return format_result(result)

    elif source == "openclaw":
        # Delegate to the existing claw migration path (preview-first).
        # We run it non-interactively with --dry-run unless execute is set.
        from pathlib import Path

        from vermes_cli.claw import _find_migration_script, _load_migration_module
        from vermes_cli.config import get_vermes_home

        openclaw_home = Path.home() / ".openclaw"
        if not openclaw_home.is_dir():
            return "未找到 OpenClaw 目录（~/.openclaw）。请确认已安装 OpenClaw。"

        script = _find_migration_script()
        if not script:
            return "OpenClaw 迁移脚本未找到（openclaw-migration skill 未安装）。"

        mod = _load_migration_module(script)
        if mod is None:
            return "无法加载 OpenClaw 迁移脚本。"

        selected = mod.resolve_selected_options(None, None, preset="full")
        migrator = mod.Migrator(
            source_root=openclaw_home.resolve(),
            target_root=get_vermes_home().resolve(),
            execute=execute,
            workspace_target=None,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name="full",
            skill_conflict_mode="overwrite" if overwrite else "skip",
        )
        report = migrator.migrate()
        summary = report.get("summary", {})
        mode = "预演" if not execute else "执行"
        lines = [f"OpenClaw 迁移（{mode}）", f"结果: ✅迁移 {summary.get('migrated', 0)} · ⚠冲突 {summary.get('conflict', 0)} · ⏭跳过 {summary.get('skipped', 0)} · ❌错误 {summary.get('error', 0)}"]
        for item in report.get("items", []):
            status = item.get("status", "skipped")
            icon = {"migrated": "✓", "conflict": "⚠", "skipped": "—", "error": "✗", "archived": "📦"}.get(status, "?")
            lines.append(f"  {icon} {item.get('kind', '?')}: {status}" + (f" ({item.get('reason')})" if item.get("reason") else ""))
        return "\n".join(lines)

    else:
        return f"不支持的迁移来源: {source}。可选: hermes, openclaw"


# --- Registry ---
from tools.registry import registry, tool_error


def check_migrate_requirements() -> bool:
    """Migration tool has no hard external dependencies — always available."""
    return True


registry.register(
    name="migrate_agent",
    toolset="system",
    schema=MIGRATE_AGENT_SCHEMA,
    handler=lambda args, **kw: migrate_agent(
        source=args.get("source", "hermes"),
        execute=args.get("execute", False),
        overwrite=args.get("overwrite", False),
        migrate_secrets=args.get("migrate_secrets", False),
        migrate_skills=args.get("migrate_skills", True),
    ),
    check_fn=check_migrate_requirements,
    emoji="🔁",
)
