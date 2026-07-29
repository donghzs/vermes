"""
Vermes fallback — manage the fallback provider chain.

Fallback providers are tried in order when the primary model fails with
rate-limit, overload, or connection errors. See:
https://Vermes-agent.donghzs.com/docs/user-guide/features/fallback-providers

Subcommands:
  Vermes fallback [list]   Show the current fallback chain (default when no subcommand)
  Vermes fallback add      Pick provider + model via the same picker as `vermes model`,
                           then append the selection to the chain
  Vermes fallback remove   Pick an entry to delete from the chain
  Vermes fallback clear    Remove all fallback entries

Storage: ``fallback_providers`` in ``~/.vermes/config.yaml`` (top-level, list of
``{provider, model, base_url?, api_mode?}`` dicts).  The legacy single-dict
``fallback_model`` format is migrated to the new list format on first add.
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
import copy
from typing import Any,Dict, List, Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_chain(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the normalized fallback chain as a list of dicts.

    Accepts both the new list format (``fallback_providers``) and the legacy
    single-dict format (``fallback_model``).  The returned list is always a
    fresh copy — callers can mutate without touching the config dict.
    """
    chain = config.get("fallback_providers") or []
    if isinstance(chain, list):
        result = [dict(e) for e in chain if isinstance(e, dict) and e.get("provider") and e.get("model")]
        if result:
            return result
    legacy = config.get("fallback_model")
    if isinstance(legacy, dict) and legacy.get("provider") and legacy.get("model"):
        return [dict(legacy)]
    if isinstance(legacy, list):
        return [dict(e) for e in legacy if isinstance(e, dict) and e.get("provider") and e.get("model")]
    return []

def _write_chain(config: Dict[str, Any], chain: List[Dict[str, Any]]) -> None:
    """Persist the chain to ``fallback_providers`` and clear legacy key."""
    config["fallback_providers"] = chain
    # Drop the legacy single-dict key on write so there's only one source of truth.
    if "fallback_model" in config:
        config.pop("fallback_model", None)

def _format_entry(entry: Dict[str, Any]) -> str:
    """One-line human-readable rendering of a fallback entry."""
    provider = entry.get("provider", "?")
    model = entry.get("model", "?")
    base = entry.get("base_url")
    suffix = f"  [{base}]" if base else ""
    return f"{model}  (via {provider}){suffix}"

def _extract_fallback_from_model_cfg(model_cfg: Any) -> Optional[Dict[str, Any]]:
    """Pull the ``{provider, model, base_url?, api_mode?}`` dict from a ``config["model"]`` snapshot."""
    if not isinstance(model_cfg, dict):
        return None
    provider = (model_cfg.get("provider") or "").strip()
    # The picker writes the selected model to ``model.default``.
    model = (model_cfg.get("default") or model_cfg.get("model") or "").strip()
    if not provider or not model:
        return None
    entry: Dict[str, Any] = {"provider": provider, "model": model}
    base_url = (model_cfg.get("base_url") or "").strip()
    if base_url:
        entry["base_url"] = base_url
    api_mode = (model_cfg.get("api_mode") or "").strip()
    if api_mode:
        entry["api_mode"] = api_mode
    return entry

def _snapshot_auth_active_provider() -> Any:
    """Return the current ``active_provider`` in auth.json, or a sentinel if unavailable."""
    try:
        from vermes_cli.auth import _load_auth_store
        store = _load_auth_store()
        return store.get("active_provider")
    except Exception:
        return None

def _restore_auth_active_provider(value: Any) -> None:
    """Write back a previously snapshotted ``active_provider`` value."""
    try:
        from vermes_cli.auth import _auth_store_lock, _load_auth_store, _save_auth_store
        with _auth_store_lock():
            store = _load_auth_store()
            store["active_provider"] = value
            _save_auth_store(store)
    except Exception:
        # Best-effort — if auth.json can't be restored, the user's primary
        # provider may have been deactivated by the picker.  They can re-run
        # `vermes model` to fix it.  Don't fail the fallback add.
        pass

# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_fallback_list(args) -> None:  # noqa: ARG001
    """Print the current fallback chain."""
    from vermes_cli.config import load_config

    config = load_config()
    chain = _read_chain(config)

    logger.info()
    if not chain:
        logger.info("  No fallback providers configured.")
        logger.info()
        logger.info("  Add one with:  Vermes fallback add")
        logger.info()
        return

    primary = _describe_primary(config)
    if primary:
        logger.info(f"  Primary:   {primary}")
        logger.info()
    logger.info(f"  Fallback chain ({len(chain)} {'entry' if len(chain) == 1 else 'entries'}):")
    for i, entry in enumerate(chain, 1):
        logger.info(f"    {i}. {_format_entry(entry)}")
    logger.info()
    logger.info("  Tried in order when the primary fails (rate-limit, 5xx, connection errors).")
    logger.info("  Docs: https://Vermes-agent.donghzs.com/docs/user-guide/features/fallback-providers")
    logger.info()

def _describe_primary(config: Dict[str, Any]) -> Optional[str]:
    """One-line description of the primary model for display purposes."""
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        provider = (model_cfg.get("provider") or "?").strip() or "?"
        model = (model_cfg.get("default") or model_cfg.get("model") or "?").strip() or "?"
        return f"{model}  (via {provider})"
    if isinstance(model_cfg, str) and model_cfg.strip():
        return model_cfg.strip()
    return None

def cmd_fallback_add(args) -> None:
    """Launch the same picker as `vermes model`, then append the selection to the chain."""
    from vermes_cli.main import _require_tty, select_provider_and_model
    from vermes_cli.config import load_config, save_config

    _require_tty("fallback add")

    # Snapshot BEFORE the picker runs so we can distinguish "user actually
    # picked something" from "user cancelled" by comparing before/after.
    before_cfg = load_config()
    model_before = copy.deepcopy(before_cfg.get("model"))
    active_provider_before = _snapshot_auth_active_provider()

    logger.info()
    logger.info("  Adding a fallback provider.  The picker below is the same one used by")
    logger.info("  `vermes model` — select the provider + model you want as a fallback.")
    logger.info()

    try:
        select_provider_and_model(args=args)
    except SystemExit:
        # Some provider flows exit on auth failure — restore state and re-raise.
        _restore_model_cfg(model_before)
        _restore_auth_active_provider(active_provider_before)
        raise

    # Read the post-picker state to see what the user selected.
    after_cfg = load_config()
    model_after = after_cfg.get("model")

    new_entry = _extract_fallback_from_model_cfg(model_after)
    if not new_entry:
        # Picker didn't complete (user cancelled or flow bailed).  Nothing to do.
        _restore_model_cfg(model_before)
        _restore_auth_active_provider(active_provider_before)
        logger.info()
        logger.info("  No fallback added.")
        return

    # Picker picked the same thing that's already the primary → nothing changed,
    # and there's nothing useful to add as a fallback to itself.
    primary_entry = _extract_fallback_from_model_cfg(model_before)
    if primary_entry and primary_entry["provider"] == new_entry["provider"] \
            and primary_entry["model"] == new_entry["model"]:
        _restore_model_cfg(model_before)
        _restore_auth_active_provider(active_provider_before)
        logger.info()
        logger.info(f"  Selected model matches the current primary ({_format_entry(new_entry)}).")
        logger.info("  A provider cannot be a fallback for itself — no change.")
        return

    # Reload the config with the primary restored, then append the new entry
    # to ``fallback_providers``.  We deliberately re-load (rather than mutating
    # ``after_cfg``) because the picker may have touched other top-level keys
    # (custom_providers, providers credentials) that we want to keep.
    _restore_model_cfg(model_before)
    _restore_auth_active_provider(active_provider_before)

    final_cfg = load_config()
    chain = _read_chain(final_cfg)

    # Reject exact-duplicate fallback entries.
    for existing in chain:
        if existing.get("provider") == new_entry["provider"] \
                and existing.get("model") == new_entry["model"]:
            logger.info()
            logger.info(f"  {_format_entry(new_entry)} is already in the fallback chain — skipped.")
            return

    chain.append(new_entry)
    _write_chain(final_cfg, chain)
    save_config(final_cfg)

    logger.info()
    logger.info(f"  Added fallback: {_format_entry(new_entry)}")
    logger.info(f"  Chain is now {len(chain)} {'entry' if len(chain) == 1 else 'entries'} long.")
    logger.info()
    logger.info("  Run `Vermes fallback list` to view, or `Vermes fallback remove` to delete.")

def _restore_model_cfg(model_before: Any) -> None:
    """Restore ``config["model"]`` to a previously-captured snapshot."""
    from vermes_cli.config import load_config, save_config

    cfg = load_config()
    if model_before is None:
        cfg.pop("model", None)
    else:
        cfg["model"] = copy.deepcopy(model_before)
    save_config(cfg)

def cmd_fallback_remove(args) -> None:  # noqa: ARG001
    """Pick an entry from the chain and remove it."""
    from vermes_cli.config import load_config, save_config

    config = load_config()
    chain = _read_chain(config)

    if not chain:
        logger.info()
        logger.info("  No fallback providers configured — nothing to remove.")
        logger.info()
        return

    choices = [_format_entry(e) for e in chain]
    choices.append("Cancel")

    try:
        from vermes_cli.setup import _curses_prompt_choice
        idx = _curses_prompt_choice("Select a fallback to remove:", choices, 0)
    except Exception:
        idx = _numbered_pick("Select a fallback to remove:", choices)

    if idx is None or idx < 0 or idx >= len(chain):
        logger.info()
        logger.info("  Cancelled — no change.")
        return

    removed = chain.pop(idx)
    _write_chain(config, chain)
    save_config(config)

    logger.info()
    logger.info(f"  Removed fallback: {_format_entry(removed)}")
    if chain:
        logger.info(f"  Chain is now {len(chain)} {'entry' if len(chain) == 1 else 'entries'} long.")
    else:
        logger.info("  Fallback chain is now empty.")
    logger.info()

def cmd_fallback_clear(args) -> None:  # noqa: ARG001
    """Remove all fallback entries (with confirmation)."""
    from vermes_cli.config import load_config, save_config

    config = load_config()
    chain = _read_chain(config)

    if not chain:
        logger.info()
        logger.info("  No fallback providers configured — nothing to clear.")
        logger.info()
        return

    logger.info()
    logger.info(f"  Current fallback chain ({len(chain)} {'entry' if len(chain) == 1 else 'entries'}):")
    for i, entry in enumerate(chain, 1):
        logger.info(f"    {i}. {_format_entry(entry)}")
    logger.info()
    try:
        resp = input("  Clear all entries? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        logger.info()
        logger.info("  Cancelled.")
        return
    if resp not in {"y", "yes"}:
        logger.info("  Cancelled — no change.")
        return

    _write_chain(config, [])
    save_config(config)
    logger.info()
    logger.info("  Fallback chain cleared.")
    logger.info()

def _numbered_pick(question: str, choices: List[str]) -> Optional[int]:
    """Fallback numbered-list picker when curses is unavailable."""
    logger.info(question)
    for i, c in enumerate(choices, 1):
        logger.info(f"  {i}. {c}")
    logger.info()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}]: ").strip()
            if not val:
                return None
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            logger.info(f"Please enter 1-{len(choices)}")
        except ValueError:
            logger.info("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            logger.info()
            return None

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def cmd_fallback(args) -> None:
    """Top-level dispatcher for ``Vermes fallback [subcommand]``."""
    sub = getattr(args, "fallback_command", None)
    if sub in {None, "", "list", "ls"}:
        cmd_fallback_list(args)
    elif sub == "add":
        cmd_fallback_add(args)
    elif sub in {"remove", "rm"}:
        cmd_fallback_remove(args)
    elif sub == "clear":
        cmd_fallback_clear(args)
    else:
        logger.info(f"Unknown fallback subcommand: {sub}")
        logger.info("Use one of: list, add, remove, clear")
        raise SystemExit(2)
