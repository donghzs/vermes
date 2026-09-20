#!/usr/bin/env python3
"""One-shot migration: remove plaintext provider API keys from config.yaml.

Background (issue #15803 / plaintext-key cleanup):
  - Built-in providers already store credentials in ~/.vermes/.env via
    ``save_env_value``, and record only ``key_env`` in config.yaml.
  - Custom providers (not in PROVIDER_TEMPLATES) historically wrote the raw
    ``api_key`` inline into config.yaml ``providers.<id>.api_key``.
  - This script relocates any inline secret into ~/.vermes/.env (keyed by the
    provider's ``key_env`` when present, else a derived ``<ID>_API_KEY`` name),
    then strips the ``api_key`` field and ensures ``key_env`` is set.

Scanned sections (three):
  1. ``providers`` (dict)          — key_env present or derived from provider id.
  2. ``custom_providers`` (list)   — key_env reverse-mapped from base_url via
     PROVIDER_TEMPLATES, else derived from name.
  3. ``auxiliary.<task>`` (dict)   — inline key is stripped only when the
     task's ``provider`` can fall back to a key_env-resolvable provider
     (``providers.<provider>.key_env`` or PROVIDER_TEMPLATES).  When the
     fallback key matches (or is written to .env), the inline key is dropped;
     otherwise it is left in place with a warning (auxiliary sections have no
     key_env field of their own, so an unrecoverable strip would break auth).

Safety:
  - If a provider has an inline key but no ``key_env`` AND the derived env key
    is already absent from .env, the key is written to .env first — nothing is
    lost.  If a same-named env key already exists, it is left untouched (env is
    the source of truth).
  - Placeholder values (``ollama``, ``local``, ``none``) are treated as
    non-secrets: they are stripped from config.yaml but NOT copied into .env.
  - config.yaml is rewritten with the round-trip YAML writer so comments /
    ordering / quoting are preserved.

Run:
    python3 scripts/migrate_plaintext_provider_keys.py [--vermes-home ~/.vermes] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

# Resolve repo root so ``vermes_cli``/``utils`` import even when run standalone.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils import load_roundtrip_yaml, atomic_roundtrip_yaml_dump  # noqa: E402

# Values that are placeholders, not real credentials.
_PLACEHOLDER_VALUES = {"", "ollama", "local", "no-key-required", "sk-local", "none"}


def _derived_env_key(provider_id: str) -> str:
    return f"{provider_id.upper().replace('-', '_')}_API_KEY"


def _env_key_from_base_url(base_url: Optional[str]) -> Optional[str]:
    """Reverse-map a base_url to a known provider's ``api_key_env``."""
    if not base_url:
        return None
    try:
        from vermes_cli.blueprints.providers import PROVIDER_TEMPLATES
    except Exception:
        return None
    norm = base_url.strip().rstrip("/").lower()
    for _pid, tpl in PROVIDER_TEMPLATES.items():
        tpl_url = (tpl.get("base_url") or "").strip().rstrip("/").lower()
        if tpl_url and tpl_url == norm:
            return tpl.get("api_key_env")
    return None


def _provider_env_key(provider_id: str, providers: dict) -> Optional[str]:
    """Resolve the env key a provider resolves its credential from."""
    prov_entry = providers.get(provider_id) if isinstance(providers, dict) else None
    if isinstance(prov_entry, dict):
        ke = str(prov_entry.get("key_env", "") or "").strip()
        if ke:
            return ke
    try:
        from vermes_cli.blueprints.providers import PROVIDER_TEMPLATES
        tpl = PROVIDER_TEMPLATES.get(provider_id)
        if tpl:
            ke = tpl.get("api_key_env")
            if ke:
                return ke
    except Exception:
        pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vermes-home", default=os.environ.get("VERMES_HOME") or str(Path.home() / ".vermes"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    home = Path(args.vermes_home)
    cfg_path = home / "config.yaml"
    env_path = home / ".env"

    if not cfg_path.exists():
        print(f"[skip] no config.yaml at {cfg_path}")
        return 0

    # Lazy imports AFTER path setup (so vermes_cli resolves).
    from vermes_cli.config import save_env_value, load_env

    data = load_roundtrip_yaml(cfg_path)
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}

    env_vars = load_env()  # dict of current .env contents

    changed = False
    report = []

    # ── 1. providers (dict) ─────────────────────────────────────────────────
    for pid, entry in list(providers.items()):
        if not isinstance(entry, dict):
            continue
        raw_key = str(entry.get("api_key", "") or "").strip()
        key_env = str(entry.get("key_env", "") or "").strip()

        if not raw_key:
            continue

        is_placeholder = raw_key in _PLACEHOLDER_VALUES

        if not key_env:
            key_env = _derived_env_key(pid)

        action = "none"
        if not is_placeholder:
            existing = (env_vars.get(key_env) or "").strip()
            if not existing:
                action = "copy-to-env"
                if not args.dry_run:
                    save_env_value(key_env, raw_key)
                report.append(f"providers.{pid}: copied {key_env} to .env (len={len(raw_key)})")
            elif existing == raw_key:
                action = "env-already-matches"
                report.append(f"providers.{pid}: {key_env} already matches, stripping inline")
            else:
                action = "env-conflict-skip"
                report.append(
                    f"providers.{pid}: WARNING {key_env} already set to a different value "
                    f"(len={len(existing)}), leaving inline key in place"
                )
                continue  # preserve inline as a fallback; do not strip

        if not args.dry_run:
            entry.pop("api_key", None)
            entry["key_env"] = key_env
        else:
            report.append(f"providers.{pid}: [dry-run] would strip api_key + set key_env={key_env}")
        changed = True

    # ── 2. custom_providers (list) ──────────────────────────────────────────
    custom_providers = data.get("custom_providers")
    if isinstance(custom_providers, list):
        for entry in custom_providers:
            if not isinstance(entry, dict):
                continue
            raw_key = str(entry.get("api_key", "") or "").strip()
            if not raw_key:
                continue
            cname = str(entry.get("name", "?") or "?")
            if raw_key in _PLACEHOLDER_VALUES:
                if not args.dry_run:
                    entry.pop("api_key", None)
                else:
                    report.append(f"custom:{cname}: [dry-run] would strip placeholder api_key")
                changed = True
                continue

            key_env = str(entry.get("key_env", "") or "").strip()
            if not key_env:
                key_env = _env_key_from_base_url(entry.get("base_url", "")) or ""
            if not key_env:
                key_env = _derived_env_key(cname.split()[0] if cname.split() else "custom")

            existing = (env_vars.get(key_env) or "").strip()
            if not existing:
                if not args.dry_run:
                    save_env_value(key_env, raw_key)
                report.append(f"custom:{cname}: copied {key_env} to .env (len={len(raw_key)})")
            elif existing == raw_key:
                report.append(f"custom:{cname}: {key_env} already matches, stripping inline")
            else:
                report.append(
                    f"custom:{cname}: WARNING {key_env} already set to a different value "
                    f"(len={len(existing)}), leaving inline key in place"
                )
                continue

            if not args.dry_run:
                entry.pop("api_key", None)
                entry["key_env"] = key_env
            else:
                report.append(f"custom:{cname}: [dry-run] would strip api_key + set key_env={key_env}")
            changed = True

    # ── 3. auxiliary.<task> (dict) ──────────────────────────────────────────
    aux = data.get("auxiliary")
    if isinstance(aux, dict):
        for task, cfg in list(aux.items()):
            if not isinstance(cfg, dict):
                continue
            raw_key = str(cfg.get("api_key", "") or "").strip()
            if not raw_key or raw_key in _PLACEHOLDER_VALUES:
                continue
            provider = str(cfg.get("provider", "") or "").strip()
            prov_key_env = _provider_env_key(provider, providers) if provider else None
            if not provider or provider == "auto" or not prov_key_env:
                report.append(
                    f"auxiliary.{task}: WARNING provider={provider or '(none)'} has no "
                    f"key_env fallback — keeping inline key in place"
                )
                continue

            existing = (env_vars.get(prov_key_env) or "").strip()
            if existing == raw_key:
                if not args.dry_run:
                    cfg.pop("api_key", None)
                report.append(f"auxiliary.{task}: {prov_key_env} matches, stripping inline")
                changed = True
            elif not existing:
                if not args.dry_run:
                    save_env_value(prov_key_env, raw_key)
                    cfg.pop("api_key", None)
                report.append(f"auxiliary.{task}: copied {prov_key_env} to .env, stripping inline")
                changed = True
            else:
                report.append(
                    f"auxiliary.{task}: WARNING {prov_key_env} already set to a different "
                    f"value (len={len(existing)}), leaving inline key in place"
                )

    if changed and not args.dry_run:
        atomic_roundtrip_yaml_dump(cfg_path, data)
        print(f"[done] rewrote {cfg_path}")

    if not report:
        print("[ok] no inline provider keys found — nothing to migrate")
    else:
        print("\n".join(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
