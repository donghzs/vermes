#!/usr/bin/env python3
"""Hermes → Vermes migration.

Vermes is forked from the official Hermes Agent, so the two user-footprint
directories (``~/.hermes/`` and ``~/.vermes/``) are structurally isomorphic.
Migration is therefore mostly a well-defined file mapping plus a config-field
whitelist merge — far simpler than the OpenClaw migration.

What we migrate (high-value, low-risk):
  - persona:      SOUL.md, IDENTITY.md
  - memories:     memories/MEMORY.md, memories/USER.md  (+ legacy .hermes-memory.md)
  - skills:       per-category skill dirs that are missing in ~/.vermes/skills/
  - config:       a small allowlist of provider/model fields (see CONFIG_FIELD_MAP)

Secrets are NEVER migrated implicitly.  ``migrate_secrets`` must be explicitly
True, and even then only a tiny allowlist of environment keys is copied.

This module is importable (used by the CLI and the agent tool) and also
runnable standalone for testing:

    python -m vermes_cli.hermes_migrate --dry-run
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Legacy Hermes memory file (older Hermes kept memory in a dot-file).
LEGACY_MEMORY_FILE = ".hermes-memory.md"

# Field-level config mapping: (hermes_key_path, vermes_key_path).
# We only migrate a curated allowlist — NOT the entire config — because
# Vermes has diverged (evolution/kanban/voice/updates/x_search are Vermes
# additions) and blindly copying could clobber Vermes-specific state.
# Keys are flat top-level; nested are handled via dot-path lookup.
CONFIG_FIELD_MAP: List[tuple[str, str]] = [
    ("model", "model"),
    ("providers", "providers"),
    ("openrouter", "openrouter"),
    ("bedrock", "bedrock"),
    ("custom_providers", "custom_providers"),
    ("model_catalog", "model_catalog"),
    ("display", "display"),
    ("privacy", "privacy"),
    ("stt", "stt"),
    ("tts", "tts"),
    ("voice", "voice"),
    ("timezone", "timezone"),
    ("command_allowlist", "command_allowlist"),
    ("human_delay", "human_delay"),
    ("context", "context"),
    ("compression", "compression"),
    ("prompt_caching", "prompt_caching"),
]

# Secrets allowlist — only these env keys are ever copied, and only when
# migrate_secrets=True.  Mirrors the OpenClaw migration's posture.
SECRET_ALLOWLIST = {
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "AGNES_API_KEY",
    "ELEVENLABS_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
}

# Skill category dirs we always skip (runtime/product-specific).
SKIP_SKILL_CATEGORIES = {
    "openclaw-imports",
    "hermes-desktop-plugins",
    "__pycache__",
}


@dataclass
class MigrateItem:
    kind: str
    source: Optional[str] = None
    destination: Optional[str] = None
    status: str = "skipped"  # migrated | skipped | conflict | error
    reason: str = ""


@dataclass
class MigrateResult:
    source_root: str
    target_root: str
    dry_run: bool
    items: List[MigrateItem] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        s: Dict[str, int] = {"migrated": 0, "skipped": 0, "conflict": 0, "error": 0}
        for it in self.items:
            s[it.status] = s.get(it.status, 0) + 1
        return s

    def add(
        self,
        kind: str,
        source: Optional[Path],
        destination: Optional[Path],
        status: str,
        reason: str = "",
    ) -> None:
        self.items.append(
            MigrateItem(
                kind=kind,
                source=str(source) if source else None,
                destination=str(destination) if destination else None,
                status=status,
                reason=reason,
            )
        )


def _find_hermes_home() -> Optional[Path]:
    """Locate the Hermes home directory."""
    for name in (".hermes",):
        p = Path.home() / name
        if p.is_dir():
            return p
    return None


def _copy_file_non_destructive(
    source: Path,
    dest: Path,
    overwrite: bool,
) -> tuple[str, str]:
    """Copy a single file. Returns (status, reason)."""
    if not source.exists():
        return "skipped", "source missing"
    if dest.exists() and not overwrite:
        return "conflict", "target already exists"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return "migrated", ""


def _merge_config_fields(
    hermes_config: Dict[str, Any],
    vermes_config: Dict[str, Any],
    overwrite: bool,
) -> int:
    """Merge the curated config allowlist. Returns number of fields merged."""
    merged = 0
    for hermes_key, vermes_key in CONFIG_FIELD_MAP:
        if hermes_key not in hermes_config:
            continue
        value = hermes_config[hermes_key]
        # Skip empty values
        if value is None or value == {} or value == [] or value == "":
            continue
        if vermes_key in vermes_config and not overwrite:
            continue
        vermes_config[vermes_key] = value
        merged += 1
    return merged


def _migrate_secrets(
    hermes_env: Dict[str, str],
    vermes_env_path: Path,
    overwrite: bool,
) -> int:
    """Copy allowlisted env keys from Hermes .env to Vermes .env. Returns count."""
    existing: Dict[str, str] = {}
    if vermes_env_path.exists():
        for line in vermes_env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()

    copied = 0
    for k in SECRET_ALLOWLIST:
        if k not in hermes_env:
            continue
        if k in existing and not overwrite:
            continue
        existing[k] = hermes_env[k]
        copied += 1

    if copied:
        lines = [f"{k}={v}" for k, v in existing.items()]
        vermes_env_path.parent.mkdir(parents=True, exist_ok=True)
        vermes_env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return copied


def _load_dotenv(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # pragma: no cover
        logger.debug("yaml load failed for %s: %s", path, e)
    return {}


def migrate_from_hermes(
    hermes_home: Optional[Path] = None,
    vermes_home: Optional[Path] = None,
    dry_run: bool = True,
    overwrite: bool = False,
    migrate_secrets: bool = False,
    migrate_skills: bool = True,
) -> MigrateResult:
    """Run the Hermes → Vermes migration. Returns a structured result.

    When dry_run=True, nothing is written; every item is reported as it would
    be, with status 'migrated' meaning "would migrate".
    """
    from vermes_cli.config import get_vermes_home, load_config, save_config

    hermes_home = Path(hermes_home) if hermes_home else _find_hermes_home()
    vermes_home = Path(vermes_home) if vermes_home else get_vermes_home()

    result = MigrateResult(
        source_root=str(hermes_home) if hermes_home else "",
        target_root=str(vermes_home),
        dry_run=dry_run,
    )

    if hermes_home is None or not hermes_home.is_dir():
        result.add("hermes-home", hermes_home, vermes_home, "error", "Hermes directory not found")
        return result

    # ── 1. Persona files ──────────────────────────────────────────
    for name in ("SOUL.md", "IDENTITY.md"):
        src = hermes_home / name
        dst = vermes_home / name
        if not dry_run:
            status, reason = _copy_file_non_destructive(src, dst, overwrite)
        else:
            status = "migrated" if src.exists() and (overwrite or not dst.exists()) else ("conflict" if src.exists() and dst.exists() else "skipped")
            reason = "" if status == "migrated" else ("target already exists" if status == "conflict" else "source missing")
        result.add("persona", src, dst, status, reason)

    # ── 2. Memories ────────────────────────────────────────────────
    # Primary memory: memories/MEMORY.md
    mem_src = hermes_home / "memories" / "MEMORY.md"
    mem_dst = vermes_home / "memories" / "MEMORY.md"
    if not dry_run:
        status, reason = _copy_file_non_destructive(mem_src, mem_dst, overwrite)
    else:
        status = "migrated" if mem_src.exists() and (overwrite or not mem_dst.exists()) else ("conflict" if mem_src.exists() and mem_dst.exists() else "skipped")
        reason = "" if status == "migrated" else ("target already exists" if status == "conflict" else "source missing")
    result.add("memory", mem_src, mem_dst, status, reason)

    # User profile: memories/USER.md
    usr_src = hermes_home / "memories" / "USER.md"
    usr_dst = vermes_home / "memories" / "USER.md"
    if not dry_run:
        status, reason = _copy_file_non_destructive(usr_src, usr_dst, overwrite)
    else:
        status = "migrated" if usr_src.exists() and (overwrite or not usr_dst.exists()) else ("conflict" if usr_src.exists() and usr_dst.exists() else "skipped")
        reason = "" if status == "migrated" else ("target already exists" if status == "conflict" else "source missing")
    result.add("user-profile", usr_src, usr_dst, status, reason)

    # Legacy dot-file memory (only if memories/MEMORY.md absent)
    legacy = hermes_home / LEGACY_MEMORY_FILE
    if legacy.exists():
        # Append into memory dir as legacy-hermes-memory.md (non-destructive)
        legacy_dst = vermes_home / "memories" / "legacy-hermes-memory.md"
        if not dry_run:
            status, reason = _copy_file_non_destructive(legacy, legacy_dst, overwrite)
        else:
            status = "migrated" if (overwrite or not legacy_dst.exists()) else "conflict"
            reason = "" if status == "migrated" else "target already exists"
        result.add("legacy-memory", legacy, legacy_dst, status, reason)

    # ── 3. Skills ──────────────────────────────────────────────────
    if migrate_skills:
        hermes_skills = hermes_home / "skills"
        vermes_skills = vermes_home / "skills"
        if hermes_skills.is_dir():
            for category in sorted(hermes_skills.iterdir()):
                if category.name in SKIP_SKILL_CATEGORIES or not category.is_dir():
                    continue
                dst_cat = vermes_skills / category.name
                if dst_cat.exists():
                    # Merge: copy only missing skill subdirs
                    _migrate_skill_category(category, dst_cat, overwrite, dry_run, result)
                else:
                    if not dry_run:
                        shutil.copytree(category, dst_cat, dirs_exist_ok=True)
                    result.add("skill-category", category, dst_cat, "migrated", "")

    # ── 4. Config fields (allowlist) ───────────────────────────────
    hermes_config = _load_yaml(hermes_home / "config.yaml")
    if hermes_config:
        vermes_config = load_config() if not dry_run else _load_yaml(vermes_home / "config.yaml")
        if not dry_run:
            merged = _merge_config_fields(hermes_config, vermes_config, overwrite)
            if merged:
                save_config(vermes_config)
        else:
            # Dry-run: count how many would merge
            merged = 0
            for hk, vk in CONFIG_FIELD_MAP:
                if hk in hermes_config and hermes_config[hk] not in (None, {}, [], ""):
                    if vk not in vermes_config or overwrite:
                        merged += 1
        result.add(
            "config",
            hermes_home / "config.yaml",
            vermes_home / "config.yaml",
            "migrated" if merged else "skipped",
            f"{merged} field(s)" if merged else "no new fields",
        )
    else:
        result.add("config", hermes_home / "config.yaml", vermes_home / "config.yaml", "skipped", "no Hermes config.yaml")

    # ── 5. Secrets (explicit opt-in) ───────────────────────────────
    if migrate_secrets:
        hermes_env = _load_dotenv(hermes_home / ".env")
        if hermes_env:
            if not dry_run:
                copied = _migrate_secrets(hermes_env, vermes_home / ".env", overwrite)
            else:
                vermes_env = _load_dotenv(vermes_home / ".env")
                copied = sum(
                    1 for k in SECRET_ALLOWLIST if k in hermes_env and (k not in vermes_env or overwrite)
                )
            result.add(
                "secrets",
                hermes_home / ".env",
                vermes_home / ".env",
                "migrated" if copied else "skipped",
                f"{copied} key(s)" if copied else "no allowlisted keys",
            )
        else:
            result.add("secrets", hermes_home / ".env", vermes_home / ".env", "skipped", "no Hermes .env")

    return result


def _migrate_skill_category(
    src_cat: Path,
    dst_cat: Path,
    overwrite: bool,
    dry_run: bool,
    result: MigrateResult,
) -> None:
    """Merge a Hermes skill category into an existing Vermes category."""
    for skill_dir in sorted(src_cat.iterdir()):
        if not skill_dir.is_dir():
            continue
        dst_skill = dst_cat / skill_dir.name
        if dst_skill.exists() and not overwrite:
            result.add("skill", skill_dir, dst_skill, "conflict", "skill already exists")
            continue
        if not dry_run:
            shutil.copytree(skill_dir, dst_skill, dirs_exist_ok=True)
        result.add("skill", skill_dir, dst_skill, "migrated", "")


def format_result(result: MigrateResult) -> str:
    """Render a human-readable summary for CLI / chat output."""
    s = result.summary()
    lines = [
        f"Hermes 迁移{'（预演）' if result.dry_run else ''}",
        f"来源: {result.source_root}",
        f"目标: {result.target_root}",
        f"结果: ✅迁移 {s['migrated']} · ⚠冲突 {s['conflict']} · ⏭跳过 {s['skipped']} · ❌错误 {s['error']}",
        "",
    ]
    for it in result.items:
        icon = {"migrated": "✓", "conflict": "⚠", "skipped": "—", "error": "✗"}.get(it.status, "?")
        lines.append(f"  {icon} {it.kind}: {it.status}" + (f" ({it.reason})" if it.reason else ""))
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--migrate-secrets", action="store_true")
    ap.add_argument("--no-skills", action="store_true")
    ap.add_argument("--source", help="Hermes home dir")
    args = ap.parse_args()

    res = migrate_from_hermes(
        hermes_home=Path(args.source) if args.source else None,
        dry_run=not getattr(args, "dry_run", False) is False or args.dry_run,
        overwrite=args.overwrite,
        migrate_secrets=args.migrate_secrets,
        migrate_skills=not args.no_skills,
    )
    print(format_result(res))
