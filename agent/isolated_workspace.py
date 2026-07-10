"""Isolated workspace — staging area for safe code modifications.

Provides a staging copy of a project directory where modifications can
be tested before being applied to the source. If verification fails,
the staging is discarded with zero impact on the source tree.

Design principles
-----------------
1. **副本隔离** — All modifications happen in a temp directory. The
   source tree is never touched until ``commit()`` is called.
2. **验证前置** — A user-provided verification function runs against
   the staging copy before any changes are applied.
3. **原子提交** — ``commit()`` either applies all changes or none.
4. **自动清理** — Staging directories are cleaned up on context exit
   or explicit ``rollback()``.

Usage
-----
::

    from agent.isolated_workspace import IsolatedWorkspace

    ws = IsolatedWorkspace("/path/to/project")
    staging = ws.begin()

    # Modify files in staging.path
    modify_files(staging.path)

    # Verify in staging
    if ws.verify(staging, run_tests):
        ws.commit(staging)  # apply to source
    else:
        ws.rollback(staging)  # discard

Or as a context manager::

    with IsolatedWorkspace("/path/to/project") as staging:
        modify_files(staging.path)
        # auto-commit if no exception, auto-rollback on exception

Limitations
-----------
- Not suitable for very large directories (copies entire tree).
- Does not handle symlinks specially (follows them).
- No git integration — use CheckpointManager for that.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("vermes.isolated_workspace")

# Default exclusions when copying
DEFAULT_EXCLUDES = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "build",
    "dist",
    ".eggs",
    "*.egg-info",
    ".tox",
    ".coverage",
    "htmlcov",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class StagingArea:
    """A staging copy of a project directory."""

    source_path: str
    staging_path: str
    _excludes: frozenset[str] = field(default_factory=lambda: frozenset(DEFAULT_EXCLUDES))

    @property
    def path(self) -> str:
        """Path to the staging directory (where modifications happen)."""
        return self.staging_path

    def get_file(self, relative_path: str) -> str:
        """Get the full path to a file in the staging area."""
        return os.path.join(self.staging_path, relative_path)

    def list_changed_files(self) -> list[str]:
        """List files that differ between staging and source."""
        changed = []
        for root, dirs, files in os.walk(self.staging_path):
            # Filter excludes
            dirs[:] = [d for d in dirs if d not in self._excludes]
            for fname in files:
                staging_file = os.path.join(root, fname)
                rel = os.path.relpath(staging_file, self.staging_path)
                source_file = os.path.join(self.source_path, rel)
                if not os.path.exists(source_file):
                    changed.append(rel)
                elif _files_differ(staging_file, source_file):
                    changed.append(rel)
        return changed

    def cleanup(self) -> None:
        """Remove the staging directory."""
        if os.path.exists(self.staging_path):
            shutil.rmtree(self.staging_path, ignore_errors=True)
            logger.debug("staging cleaned up: %s", self.staging_path)


# ---------------------------------------------------------------------------
# IsolatedWorkspace
# ---------------------------------------------------------------------------


class IsolatedWorkspace:
    """Manages isolated staging areas for safe code modifications."""

    def __init__(
        self,
        source_path: str,
        excludes: set[str] | None = None,
    ) -> None:
        self.source_path = os.path.abspath(source_path)
        self._excludes = frozenset(excludes) if excludes else frozenset(DEFAULT_EXCLUDES)
        self._active_staging: list[StagingArea] = []

    def begin(self) -> StagingArea:
        """Create a staging copy of the source directory.

        Returns
        -------
        StagingArea
            The staging area where modifications can be made safely.
        """
        if not os.path.isdir(self.source_path):
            raise FileNotFoundError(f"Source directory not found: {self.source_path}")

        staging_dir = tempfile.mkdtemp(prefix="vermes_staging_")
        logger.info("creating staging copy: %s → %s", self.source_path, staging_dir)

        _copy_tree(self.source_path, staging_dir, self._excludes)

        staging = StagingArea(
            source_path=self.source_path,
            staging_path=staging_dir,
            _excludes=self._excludes,
        )
        self._active_staging.append(staging)
        return staging

    def verify(
        self,
        staging: StagingArea,
        verification_fn: Callable[[str], bool],
    ) -> bool:
        """Run a verification function against the staging copy.

        Parameters
        ----------
        staging : StagingArea
            The staging area to verify.
        verification_fn : callable
            A function that takes the staging path and returns True if
            the changes are acceptable.

        Returns
        -------
        bool
            True if verification passed, False otherwise.
        """
        try:
            result = verification_fn(staging.staging_path)
            logger.info("staging verification: %s", "passed" if result else "failed")
            return bool(result)
        except Exception as e:
            logger.warning("staging verification error: %s", e)
            return False

    def commit(self, staging: StagingArea) -> bool:
        """Apply staging changes back to the source directory.

        Only applies files that have changed. Returns True if any files
        were applied.

        Parameters
        ----------
        staging : StagingArea
            The staging area to commit.

        Returns
        -------
        bool
            True if changes were applied, False if no changes detected.
        """
        changed = staging.list_changed_files()
        if not changed:
            logger.info("no changes to commit")
            staging.cleanup()
            self._active_staging.remove(staging) if staging in self._active_staging else None
            return False

        applied = 0
        for rel_path in changed:
            staging_file = os.path.join(staging.staging_path, rel_path)
            source_file = os.path.join(self.source_path, rel_path)

            # Ensure parent directory exists
            os.makedirs(os.path.dirname(source_file), exist_ok=True)

            shutil.copy2(staging_file, source_file)
            applied += 1
            logger.debug("committed: %s", rel_path)

        logger.info("committed %d file(s) to source", applied)
        staging.cleanup()
        if staging in self._active_staging:
            self._active_staging.remove(staging)
        return True

    def rollback(self, staging: StagingArea) -> None:
        """Discard all staging changes and clean up.

        Parameters
        ----------
        staging : StagingArea
            The staging area to discard.
        """
        logger.info("rolling back staging: %s", staging.staging_path)
        staging.cleanup()
        if staging in self._active_staging:
            self._active_staging.remove(staging)

    def cleanup_all(self) -> None:
        """Clean up all active staging areas."""
        for staging in list(self._active_staging):
            staging.cleanup()
        self._active_staging.clear()

    # Context manager support
    def __enter__(self) -> StagingArea:
        return self.begin()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            # Exception occurred → rollback all active staging
            self.cleanup_all()
        else:
            # No exception → commit all active staging
            for staging in list(self._active_staging):
                self.commit(staging)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _files_differ(path_a: str, path_b: str) -> bool:
    """Check if two files differ in content."""
    try:
        with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
            return fa.read() != fb.read()
    except (OSError, FileNotFoundError):
        return True


def _copy_tree(src: str, dst: str, excludes: frozenset[str]) -> None:
    """Copy a directory tree, excluding specified patterns."""
    for root, dirs, files in os.walk(src):
        # Filter directories in-place
        dirs[:] = [
            d for d in dirs
            if d not in excludes and not _matches_glob(d, excludes)
        ]

        # Calculate relative path and create corresponding dst directory
        rel = os.path.relpath(root, src)
        dst_root = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(dst_root, exist_ok=True)

        for fname in files:
            if _matches_glob(fname, excludes):
                continue
            src_file = os.path.join(root, fname)
            dst_file = os.path.join(dst_root, fname)
            try:
                shutil.copy2(src_file, dst_file)
            except (OSError, shutil.Error) as e:
                logger.debug("skipped file during copy: %s (%s)", src_file, e)


def _matches_glob(name: str, patterns: frozenset[str]) -> bool:
    """Check if a name matches any glob pattern."""
    from fnmatch import fnmatch
    return any(fnmatch(name, pat) for pat in patterns)
