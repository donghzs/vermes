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

import json
import logging
import os
import shutil
import sqlite3
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
    """
    source: str                          # "agent" | "capability_evolver" | "skill_extractor" | "domain_modules"
    target_path: str                     # absolute path
    content: str                         # full file content
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeResult:
    """Outcome of a change proposal."""
    committed: bool                      # True if changes were applied
    target_path: str = ""
    error: str = ""                      # non-empty if failed
    raw_event_id: Optional[int] = None   # raw_event rowid for traceability


# ---------------------------------------------------------------------------
# Validator — objective format/compatibility checks (NOT risk assessment)
# ---------------------------------------------------------------------------

def _validate_yaml(content: str) -> bool:
    """Check if content is valid YAML."""
    try:
        import yaml
        yaml.safe_load(content)
        return True
    except Exception:
        return False


def _validate_json(content: str) -> bool:
    """Check if content is valid JSON."""
    try:
        json.loads(content)
        return True
    except Exception:
        return False


def _validate_python_syntax(content: str) -> bool:
    """Check if content is valid Python (syntax only, no execution)."""
    try:
        import ast
        ast.parse(content)
        return True
    except SyntaxError:
        return False


def _validate_file_format(target_path: str, content: str) -> Tuple[bool, str]:
    """Validate content format based on file extension.

    This is an OBJECTIVE check — "is this valid YAML/JSON/Python?"
    It does NOT assess risk or decide whether the change should proceed.
    """
    ext = Path(target_path).suffix.lower()
    if ext in (".yaml", ".yml"):
        if not _validate_yaml(content):
            return False, f"Invalid YAML syntax"
    elif ext == ".json":
        if not _validate_json(content):
            return False, f"Invalid JSON syntax"
    elif ext == ".py":
        if not _validate_python_syntax(content):
            return False, f"Invalid Python syntax"
    # Unknown extensions: no format validation (can't be objective)
    return True, ""


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

    def __init__(self, hermes_home: str = "") -> None:
        self.hermes_home = hermes_home or os.environ.get(
            "HERMES_HOME", os.path.expanduser("~/.hermes")
        )
        self.staging_dir = Path(self.hermes_home) / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def apply_change(self, proposal: ChangeProposal) -> ChangeResult:
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
                backup_path = str(target) + ".bak." + datetime.now().strftime("%Y%m%d%H%M%S")
                shutil.copy2(str(target), backup_path)

            shutil.copy2(staging_path, str(target))
            logger.info("Change committed: %s (source=%s)", proposal.target_path, proposal.source)

            # Clean up staging
            os.unlink(staging_path)

            # Step 4: record raw_event
            event_id = self._record_change_event(proposal, committed=True, backup_path=backup_path)

            return ChangeResult(
                committed=True,
                target_path=proposal.target_path,
                raw_event_id=event_id,
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

    def rollback_change(self, target_path: str, backup_path: Optional[str] = None) -> bool:
        """Rollback a previously applied change.

        If backup_path is provided, restore from backup.
        Otherwise, delete the file (if it was a creation, not a modification).
        """
        try:
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, target_path)
                os.unlink(backup_path)
                logger.info("Change rolled back (restored): %s", target_path)
            elif os.path.exists(target_path):
                os.unlink(target_path)
                logger.info("Change rolled back (deleted): %s", target_path)

            # Record rollback as raw_event
            self._record_rollback_event(target_path)
            return True
        except Exception as e:
            logger.error("Rollback failed: %s — %s", target_path, e)
            return False

    # ── Internal helpers ────────────────────────────────────────────────────

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
    ) -> Optional[int]:
        """Record a change commit/rollback as a raw_event.

        This is critical — the raw_event feeds back into emergent_clusterer,
        which over time clusters commit/rollback patterns. The system
        learns from its own modification history what patterns are safe
        and what patterns tend to cause problems.
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
            }
            result = (
                f"committed: {proposal.target_path}"
                if committed
                else f"rejected: {reason}"
            )
            return record_raw_event(
                tool_name=tool_name,
                tool_args=tool_args,
                result=result,
                is_error=not committed,
                duration=0.0,
            )
        except Exception:
            logger.debug("Failed to record change raw_event", exc_info=True)
            return None

    def _record_rollback_event(self, target_path: str) -> None:
        """Record a user-initiated rollback as a raw_event."""
        try:
            from agent.raw_event import record_raw_event

            record_raw_event(
                tool_name="self_modify_rollback",
                tool_args={"target_path": target_path},
                result=f"rolled back: {target_path}",
                is_error=False,
                duration=0.0,
            )
        except Exception:
            logger.debug("Failed to record rollback raw_event", exc_info=True)


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

    Returns:
        ChangeResult with committed status and traceability info
    """
    proposal = ChangeProposal(
        source=source,
        target_path=target_path,
        content=content,
        description=description,
        metadata=metadata or {},
    )
    return get_pipeline().apply_change(proposal)
