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

Safety:
  - If a provider has an inline key but no ``key_env`` AND the derived env key
    is already absent from .env, the key is written to .env first — nothing is
    lost.  If a same-named env key already exists, it is left untouched (env is
    the source of truth).
  - Placeholder values (``ollama``, ``local``) are treated as non-secrets:
    they are stripped from config.yaml but NOT copied into .env.
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

# Resolve repo root so ``vermes_cli``/``utils`` import even when run standalone.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from utils import load_roundtrip_yaml, atomic_roundtrip_yaml_dump  # noqa: E402

# Values that are placeholders, not real credentials.
_PLACEHOLDER_VALUES = {"", "ollama", "local", "no-key-required", "sk-local", "local"}


def _derived_env_key(provider_id: str) -> str:
    return f"{provider_id.upper().replace('-', '_')}_API_KEY"


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
        print("[skip] no providers map in config.yaml")
        return 0

    env_vars = load_env()  # dict of current .env contents

    changed = False
    report = []

    for pid, entry in list(providers.items()):
        if not isinstance(entry, dict):
            continue
        raw_key = str(entry.get("api_key", "") or "").strip()
        key_env = str(entry.get("key_env", "") or "").strip()

        if not raw_key:
            # No inline key at all — nothing to do (but still ensure key_env stays).
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
                report.append(f"{pid}: copied {key_env} to .env (len={len(raw_key)})")
            elif existing == raw_key:
                action = "env-already-matches"
                report.append(f"{pid}: {key_env} already matches, stripping inline")
            else:
                # Env already has a DIFFERENT key for this name — do not clobber.
                action = "env-conflict-skip"
                report.append(
                    f"{pid}: WARNING {key_env} already set to a different value "
                    f"(len={len(existing)}), leaving inline key in place"
                )
                continue  # preserve inline as a fallback; do not strip

        # Strip the inline key and ensure key_env is recorded.
        if not args.dry_run:
            entry.pop("api_key", None)
            entry["key_env"] = key_env
        else:
            report.append(f"{pid}: [dry-run] would strip api_key + set key_env={key_env}")
        changed = True

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
