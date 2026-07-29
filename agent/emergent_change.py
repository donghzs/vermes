"""Emergent change pipeline — the only channel for self-modification.

Design principle: 机制与策略分离 (Mechanism vs Policy separation)
- 引擎的决定逻辑不可改 (engine logic is immutable)
- 引擎读取的内容可改 (engine-readable content is mutable)
- ALL modifications go through IsolatedWorkspace → self_validator → raw_event
- Zero hardcoded risk levels — safety emerges from commit/rollback data

Flow:
    1. Agent (or emergence system) proposes a change
    2. Change written to IsolatedWorkspace staging area
    3. self_validator verifies format/compatibility (objective check, not risk)
    4. If format OK → commit to target path
    5. commit/rollback recorded as raw_event
    6. emergent_clusterer clusters commit/rollback patterns over time
    7. "Needs confirmation?" emerges from data, not from hardcoded rules

Initially: ALL changes auto-commit after format validation.
    - No preset "high risk" / "low risk" categories
    - If a change causes problems, the user rollback/feedback creates
      raw_events that cluster → pattern emerges → future similar changes
      get flagged automatically
    - This is true adaptation: 1000 users → 1000 different safety profiles
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vermes.emergent_change")

# ---------------------------------------------------------------------------
# Change Proposal — describes what the Agent wants to modify
# ---------------------------------------------------------------------------

@dataclass
class ChangeProposal:
    """A proposed self-modification.

    Attributes:
        source:        Who proposed this change (agent, capability_evolver, skill_extractor, domain_modules)
        target_path:   Absolute path to the file being created/modified
        content:       New file content (full write, not patch)
        description:   Human-readable description of what this change does
        metadata:      Extra context (cluster_id, skill_id, etc.)
        initiator:     Who initiated (agent/user/system). Default 'agent'.
                       'user' = user explicitly requested, 'system' = auto-cleanup/restart,
                       'agent' = agent decided on its own.
    """
    source: str                          # "agent" | "capability_evolver" | "skill_extractor" | "domain_modules"
    target_path: str                     # absolute path
    content: str                         # full file content
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    initiator: str = "agent"             # agent | user | system


@dataclass
class ChangeResult:
    """Outcome of a change proposal."""
    committed: bool                      # True if changes were applied
    target_path: str = ""
    error: str = ""                      # non-empty if failed
    raw_event_id: Optional[int] = None   # raw_event rowid for traceability
    backup_path: Optional[str] = None    # backup file path (for rollback)
    pending_confirmation: bool = False   # True if held for user confirmation (cold-start gate)


# ---------------------------------------------------------------------------
# Format validation — delegated to self_validator module
# ---------------------------------------------------------------------------

def _validate_file_format(target_path: str, content: str) -> Tuple[bool, str]:
    """Delegate format validation to self_validator's FileFormatStrategy.

    This is a thin wrapper that calls the singleton format validator.
    All format rules (Python/YAML/JSON) are defined in self_validator.py,
    so future extensions (diff size, compatibility checks) automatically
    apply here without code changes in emergent_change.
    """
    from agent.self_validator import get_format_validator

    result = get_format_validator().verify_format(target_path, content)
    if result.ok:
        return True, ""
    return False, result.message


# ---------------------------------------------------------------------------
# EmergentChangePipeline — the single channel for self-modification
# ---------------------------------------------------------------------------

class EmergentChangePipeline:
    """The only channel through which Vermes modifies its own content.

    All self-modifications (config, skills, domain_modules, prompt templates)
    go through this pipeline:

        propose → stage → validate format → commit → record raw_event

    No hardcoded risk levels. No preset "needs confirmation" rules.
    Safety emerges from commit/rollback data clustering over time.
    """

    # Maximum backups to keep per target file (oldest auto-deleted)
    MAX_BACKUPS_PER_FILE = 5

    def __init__(self, VERMES_home: str = "") -> None:
        self.VERMES_home = VERMES_home or os.environ.get(
            "VERMES_HOME", os.path.expanduser("~/.Vermes")
        )
        self.staging_dir = Path(self.VERMES_home) / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        # Clean up stale staging files from previous crashes
        self._cleanup_stale_staging()

    def _cleanup_stale_staging(self) -> int:
        """Remove leftover staging files from previous runs.

        If the pipeline crashed between _write_to_staging() and commit(),
        the staging file stays around. Clean it on startup.
        """
        try:
            removed = 0
            for f in self.staging_dir.iterdir():
                if f.is_file() and f.name.startswith("change_"):
                    f.unlink(missing_ok=True)
                    removed += 1
            if removed:
                logger.info("Cleaned up %d stale staging file(s)", removed)
            return removed
        except Exception:
            logger.debug("Staging cleanup failed", exc_info=True)
            return 0

    def apply_change(self, proposal: ChangeProposal, force: bool = False) -> ChangeResult:
        """Apply a proposed change through the pipeline.

        Steps:
            1. Validate format (objective, extension-based)
            2. Write to staging
            3. Copy to target
            4. Record raw_event (commit)
            5. Return result

        If any step fails, rollback (delete staging) and record raw_event (rollback).
        """
        # Step 1: format validation
        ok, err = _validate_file_format(proposal.target_path, proposal.content)
        if not ok:
            logger.warning("Change rejected (format): %s — %s", proposal.target_path, err)
            event_id = self._record_change_event(proposal, committed=False, reason=err)
            return ChangeResult(committed=False, target_path=proposal.target_path, error=err, raw_event_id=event_id)

        # Step 1b: cold-start safety gate
        # During the data vacuum period (before enough rollback samples exist for
        # the target file), agent/system-initiated changes are held for user
        # confirmation. User-initiated changes always auto-commit. Once rollback
        # clustering accumulates enough data, this gate naturally relaxes —
        # fully data-driven, no hardcoded risk rules.
        # ``force=True`` bypasses the gate — only call it after the user has
        # explicitly confirmed the change (e.g. via the gateway approval flow
        # in tools/self_modify_tool.py).
        if not force and proposal.initiator != "user":
            min_samples = int(proposal.metadata.get("min_rollback_samples", 5))
            if not self._has_sufficient_rollback_history(proposal.target_path, min_samples):
                logger.info(
                    "Change held for confirmation (cold-start): %s — initiator=%s, insufficient rollback history",
                    proposal.target_path, proposal.initiator,
                )
                event_id = self._record_change_event(
                    proposal, committed=False,
                    reason=f"pending_confirmation: initiator={proposal.initiator}, "
                           f"rollback_samples < {min_samples}",
                )
                return ChangeResult(
                    committed=False,
                    target_path=proposal.target_path,
                    error=f"Held for confirmation: insufficient rollback history "
                          f"for {proposal.target_path} (initiator={proposal.initiator}). "
                          f"User approval required until enough data accumulates.",
                    raw_event_id=event_id,
                    pending_confirmation=True,
                )

        # Step 2: write to staging
        staging_path = self._write_to_staging(proposal)
        if not staging_path:
            return ChangeResult(
                committed=False,
                target_path=proposal.target_path,
                error="Failed to write staging file",
            )

        # Step 3: copy to target
        try:
            target = Path(proposal.target_path)
            target.parent.mkdir(parents=True, exist_ok=True)

            # If target exists, back up
            backup_path: Optional[str] = None
            if target.exists():
                backup_path = str(target) + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S%f")
                shutil.copy2(str(target), backup_path)
                # Clean up old backups (keep only MAX_BACKUPS_PER_FILE)
                self._cleanup_old_backups(str(target))

            shutil.copy2(staging_path, str(target))
            logger.info("Change committed: %s (source=%s)", proposal.target_path, proposal.source)

            # Clean up staging
            os.unlink(staging_path)

            # Step 3b: import validation for Python files
            # After writing, try to import the module. If import fails
            # (syntax error that passed format check, missing dependency,
            # circular import, etc.), automatically rollback to the backup.
            import_error = self._validate_import(target)
            if import_error:
                logger.warning("Import validation failed for %s — rolling back: %s",
                               proposal.target_path, import_error)
                self._do_rollback(target, backup_path)
                event_id = self._record_change_event(
                    proposal, committed=False,
                    reason=f"import_validation_failed: {import_error}",
                )
                return ChangeResult(
                    committed=False,
                    target_path=proposal.target_path,
                    error=f"Import validation failed: {import_error}. "
                          f"File rolled back to previous version.",
                    raw_event_id=event_id,
                    backup_path=backup_path,
                )

            # Step 4: record raw_event
            event_id = self._record_change_event(proposal, committed=True, backup_path=backup_path)

            return ChangeResult(
                committed=True,
                target_path=proposal.target_path,
                raw_event_id=event_id,
                backup_path=backup_path,
            )

        except Exception as e:
            logger.error("Change commit failed: %s — %s", proposal.target_path, e)
            # Clean up staging
            try:
                os.unlink(staging_path)
            except OSError:
                pass
            self._record_change_event(proposal, committed=False, reason=str(e))
            return ChangeResult(
                committed=False,
                target_path=proposal.target_path,
                error=str(e),
            )

    def propose_change(self, proposal: ChangeProposal) -> ChangeResult:
        """Validate a proposed self-modification and record it as pending.

        Does NOT write to the target file or to staging. The agent is expected
        to present the diff to the user and only call apply_change(force=True)
        after explicit user confirmation (see tools/self_modify_tool.py).
        """
        ok, err = _validate_file_format(proposal.target_path, proposal.content)
        if not ok:
            event_id = self._record_change_event(proposal, committed=False, reason=err)
            return ChangeResult(
                committed=False, target_path=proposal.target_path,
                error=err, raw_event_id=event_id,
            )
        event_id = self._record_change_event(
            proposal, committed=False,
            reason="proposed: awaiting user approval", is_error=False,
        )
        return ChangeResult(
            committed=False, target_path=proposal.target_path,
            raw_event_id=event_id, pending_confirmation=True,
        )

    def rollback_change(
        self,
        target_path: str,
        backup_path: Optional[str] = None,
        initiator: str = "agent",
        force: bool = False,
    ) -> bool:
        """Rollback a previously applied change.

        If backup_path is provided, restore from backup.
        Otherwise, delete the file (if it was a creation, not a modification).

        Safety gate (mirrors the 🔒 tool-approval policy for dangerous
        commands): an *autonomous* rollback (initiator in agent/system) is a
        destructive file operation, so unless YOLO is enabled it must be
        approved through the Gateway (desktop / Gateway approval dialog). A
        *user*-initiated rollback (e.g. the panel "undo" button,
        initiator="user") skips the gate because the click IS the
        confirmation. ``force=True`` bypasses the gate — only call it after a
        privileged approval already resolved upstream.

        Args:
            target_path:  The file to rollback
            backup_path:  Backup file to restore from (optional)
            initiator:   Who initiated the rollback (agent/user/system)
            force:       Bypass the approval gate (only after explicit approval)
        """
        # ── Autonomous (agent/system) approval gate ─────────────────────────
        if initiator != "user" and not force:
            try:
                from tools.approval import (
                    get_current_session_key,
                    approve_privileged_action,
                )
                session_key = get_current_session_key(default="")
                # Look up the original committed event's timestamp for context
                committed_at = self._lookup_committed_time(target_path)
                action_desc = (
                    f"恢复备份 → {target_path}" if backup_path
                    else f"删除文件 {target_path}"
                )
                if committed_at:
                    description = (
                        f"⚠️ 撤销确认: 正在撤销 {committed_at} 的改写 → {target_path}"
                    )
                else:
                    description = f"⚠️ 撤销确认: Agent 请求回滚改写 {target_path}"
                approved = approve_privileged_action(
                    session_key,
                    {
                        "command": f"rollback {target_path}",
                        "description": description,
                        "category": "self_modify_rollback",
                        "pattern_key": "self_modify_rollback",
                        "pattern_keys": ["self_modify_rollback"],
                        "diff": action_desc,
                        "target_path": target_path,
                        "backup_path": backup_path or "",
                        "committed_at": committed_at or "",
                        "surface": "gui",
                    },
                    surface="gateway",
                )
            except Exception:
                logger.debug("Rollback approval check failed", exc_info=True)
                approved = False
            if not approved:
                # User denied / timeout / no active session → do NOT touch the file.
                self._record_rollback_event(target_path, initiator=initiator, denied=True)
                return False

        # ── Execute rollback ────────────────────────────────────────────────
        try:
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, target_path)
                os.unlink(backup_path)
                logger.info("Change rolled back (restored): %s", target_path)
            elif backup_path:
                # A backup was referenced but is gone (e.g. pruned by
                # _cleanup_old_backups). Refuse to delete the current file —
                # that would be destructive and wrong (we can't restore).
                logger.error("Rollback aborted: backup missing %s", backup_path)
                return False
            elif os.path.exists(target_path):
                # No backup → treat as a creation rollback (delete the file).
                os.unlink(target_path)
                logger.info("Change rolled back (deleted): %s", target_path)

            # Record rollback as raw_event
            self._record_rollback_event(target_path, initiator=initiator)
            return True
        except Exception as e:
            logger.error("Rollback failed: %s — %s", target_path, e)
            return False

    def _cleanup_old_backups(self, target_path: str) -> int:
        """Remove old backup files for a target, keeping only MAX_BACKUPS_PER_FILE.

        Backups are named: <target_path>.bak.<timestamp>
        Returns number of files deleted.
        """
        try:
            target = Path(target_path)
            parent = target.parent
            prefix = target.name + ".bak."

            backups = sorted(
                [p for p in parent.iterdir() if p.name.startswith(prefix)],
                key=lambda p: p.name,  # timestamp in name = lexicographic = chronological
                reverse=True,  # newest first
            )

            deleted = 0
            for old_backup in backups[self.MAX_BACKUPS_PER_FILE:]:
                old_backup.unlink(missing_ok=True)
                deleted += 1

            if deleted:
                logger.debug("Cleaned up %d old backup(s) for %s", deleted, target_path)
            return deleted
        except Exception:
            logger.debug("Backup cleanup failed for %s", target_path, exc_info=True)
            return 0

    # ── Internal helpers ────────────────────────────────────────────────────

    # ── Import validation ───────────────────────────────────────────────────

    def _validate_import(self, target: Path) -> Optional[str]:
        """Try to import the written .py file. Return error message if import fails.

        For non-.py files, skip (return None = no error).
        Uses importlib.util.spec_from_file_location to load the module in
        isolation, then attempts to exec it. This catches:
          - Syntax errors that slipped past format validation
          - Missing dependencies (ImportError)
          - Circular imports
          - Runtime errors at module level (NameError, AttributeError, etc.)

        The import is done in a temporary module name to avoid polluting
        sys.modules or shadowing the existing loaded version.
        """
        if target.suffix != ".py":
            return None

        try:
            # Generate a unique temporary module name to avoid sys.modules collision
            tmp_mod_name = f"_emergent_validate_{target.stem}_{datetime.now().strftime('%H%M%S%f')}"
            spec = importlib.util.spec_from_file_location(tmp_mod_name, str(target))
            if spec is None or spec.loader is None:
                return f"Cannot create module spec for {target}"

            module = importlib.util.module_from_spec(spec)
            # Temporarily add to sys.modules so relative imports work
            sys.modules[tmp_mod_name] = module
            try:
                spec.loader.exec_module(module)
            finally:
                # Clean up: remove the temp module from sys.modules
                sys.modules.pop(tmp_mod_name, None)

            logger.debug("Import validation passed: %s", target)
            return None

        except SyntaxError as e:
            return f"SyntaxError: {e.msg} (line {e.lineno})"
        except ImportError as e:
            return f"ImportError: {e}"
        except Exception as e:
            return f"{type(e).__name__}: {e}"

    def _do_rollback(self, target: Path, backup_path: Optional[str]) -> None:
        """Restore target from backup (or delete if no backup = creation rollback)."""
        try:
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, str(target))
                logger.info("Auto-rollback restored from %s", backup_path)
            elif target.exists():
                os.unlink(str(target))
                logger.info("Auto-rollback deleted (no backup = was creation)")
        except Exception as e:
            logger.error("Auto-rollback FAILED for %s: %s", target, e)

    def _write_to_staging(self, proposal: ChangeProposal) -> Optional[str]:
        """Write proposal content to a staging file."""
        try:
            staging_name = (
                f"change_{datetime.now().strftime('%Y%m%d%H%M%S_%f')}"
                f"_{Path(proposal.target_path).name}"
            )
            staging_path = self.staging_dir / staging_name
            staging_path.write_text(proposal.content, encoding="utf-8")
            return str(staging_path)
        except Exception as e:
            logger.error("Staging write failed: %s", e)
            return None

    def _record_change_event(
        self,
        proposal: ChangeProposal,
        committed: bool,
        reason: str = "",
        backup_path: Optional[str] = None,
        is_error: Optional[bool] = None,
    ) -> Optional[int]:
        """Record a change commit/rollback as a raw_event.

        This is critical — the raw_event feeds back into emergent_clusterer,
        which over time clusters commit/rollback patterns. The system
        learns from its own modification history what patterns are safe
        and what patterns tend to cause problems.

        The initiator field distinguishes who triggered the change:
        - 'agent':  Agent decided on its own (most common)
        - 'user':   User explicitly requested (higher confidence)
        - 'system': System event (restart, cleanup, migration)
        Future: 'passive' for user-side file deletions detected by watchers
        """
        try:
            from agent.raw_event import record_raw_event

            tool_name = "self_modify"
            tool_args = {
                "source": proposal.source,
                "target_path": proposal.target_path,
                "description": proposal.description,
                "file_ext": Path(proposal.target_path).suffix,
                "backup_path": backup_path or "",
                "initiator": proposal.initiator,
            }
            # Persist backup_path in result_preview so the Evolution panel can
            # surface it for one-click rollback. args_preview is capped at 200
            # chars and truncates backup_path away, so the only reliable place
            # to recover it is here (result_preview allows 500 chars).
            result = (
                (f"committed: {proposal.target_path}"
                 + (f" || backup: {backup_path}" if backup_path else ""))
                if committed
                else f"rejected: {reason}"
            )
            return record_raw_event(
                tool_name=tool_name,
                tool_args=tool_args,
                result=result,
                is_error=(not committed) if is_error is None else is_error,
                duration=0.0,
            )
        except Exception:
            logger.debug("Failed to record change raw_event", exc_info=True)
            return None

    def _record_rollback_event(
        self,
        target_path: str,
        initiator: str = "agent",
        denied: bool = False,
    ) -> None:
        """Record a rollback as a raw_event.

        The initiator field is critical for the clustering system:
        - 'agent':  Agent decided to rollback (self-correction signal)
        - 'user':   User explicitly rolled back (strong negative signal)
        - 'system': System-triggered rollback (restart/cleanup)
        Future: 'passive' for detected user-side file deletions

        ``denied=True`` records a user/system rejection of an autonomous
        rollback request (no file was touched) — a negative signal that the
        clustering system can learn from.
        """
        try:
            from agent.raw_event import record_raw_event

            record_raw_event(
                tool_name="self_modify_rollback",
                tool_args={
                    "target_path": target_path,
                    "initiator": initiator,
                    "denied": denied,
                },
                result=(f"denied: {target_path}" if denied
                        else f"rolled back: {target_path}"),
                is_error=False,
                duration=0.0,
            )
        except Exception:
            logger.debug("Failed to record rollback raw_event", exc_info=True)

    def _lookup_committed_time(self, target_path: str) -> str:
        """Look up the timestamp of the most recent committed event for target_path.

        Used to provide context in rollback approval dialogs so the user knows
        *which* change is being rolled back.
        """
        try:
            from agent.evolution_manager import get_self_model_db
            import sqlite3
            db_path = str(get_self_model_db())
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT timestamp FROM raw_events
                   WHERE tool_name = 'self_modify'
                     AND result_preview LIKE ?
                   ORDER BY rowid DESC LIMIT 1""",
                (f"committed: {target_path}%",),
            ).fetchone()
            conn.close()
            if row:
                ts = row["timestamp"]
                # Format: "2026-07-17T06:05:00" → "07-17 06:05"
                return ts[5:16].replace("T", " ") if ts else ""
        except Exception as e:
            logger.debug("emergent_change.py:  lookup committed time failed: %s", e)
        return ""

    def _has_sufficient_rollback_history(self, target_path: str, min_samples: int = 5) -> bool:
        """Check if enough rollback data exists for a target file.

        During cold-start (no data), returns False — agent/system changes need
        user confirmation. Once enough rollback/commit samples accumulate in
        raw_events, returns True and the gate relaxes.

        This is the data-driven safety gate: the threshold is soft and will be
        superseded by emergent clustering once enough data exists.
        """
        try:
            from agent.evolution_manager import get_self_model_db
            db_path = str(get_self_model_db())
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM raw_events "
                "WHERE tool_name IN ('self_modify', 'self_modify_rollback') "
                "AND result_preview LIKE ?",
                (f"%{target_path}%",),
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count >= min_samples
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Convenience: apply a change in one call
# ---------------------------------------------------------------------------

_pipeline: Optional[EmergentChangePipeline] = None


def get_pipeline() -> EmergentChangePipeline:
    """Get the singleton pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = EmergentChangePipeline()
    return _pipeline


def apply_change(
    source: str,
    target_path: str,
    content: str,
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    initiator: str = "agent",
) -> ChangeResult:
    """Apply a self-modification through the emergent change pipeline.

    This is the main entry point. All self-modification modules
    (skill_extractor, domain_modules, capability_evolver, agent itself)
    should call this function instead of writing files directly.

    Args:
        source:       Who is making this change
        target_path:  Absolute path to the file
        content:      Full file content
        description:  Human-readable description
        metadata:     Extra context
        initiator:    Who initiated (agent/user/system)

    Returns:
        ChangeResult with committed status and traceability info
    """
    proposal = ChangeProposal(
        source=source,
        target_path=target_path,
        content=content,
        description=description,
        metadata=metadata or {},
        initiator=initiator,
    )
    return get_pipeline().apply_change(proposal)
