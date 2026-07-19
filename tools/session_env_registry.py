"""Session-level registry for credential files and environment variable passthrough.

Consolidates credential_files and env_passthrough into a single registry,
while preserving ContextVar as internal storage for concurrency isolation.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Dict, Iterable
import logging

logger = logging.getLogger(__name__)


class SessionEnvRegistry:
    """Per-session registry for credential files and env passthrough.

    This class provides a unified interface for what was previously
    managed by two separate ContextVars in credential_files.py and
    env_passthrough.py. ContextVar is used internally for concurrency
    isolation (same as before), but callers can now use a single
    registry object.
    """

    def __init__(self) -> None:
        self._credential_files: Dict[str, str] = {}
        self._allowed_env_vars: set[str] = set()

    # Credential file management
    def register_credential_file(self, name: str, path: str) -> None:
        """Register a credential file for the current session."""
        self._credential_files[name] = path

    def register_credential_files(self, files: Dict[str, str]) -> None:
        """Register multiple credential files."""
        self._credential_files.update(files)

    def get_credential_files(self) -> Dict[str, str]:
        """Get all registered credential files."""
        return dict(self._credential_files)

    # Env passthrough management
    def register_env_passthrough(self, var_names: Iterable[str]) -> None:
        """Register environment variables allowed for passthrough."""
        self._allowed_env_vars.update(var_names)

    def is_env_passthrough(self, var_name: str) -> bool:
        """Check if a variable is in the passthrough whitelist."""
        return var_name in self._allowed_env_vars

    def get_all_passthrough(self) -> frozenset[str]:
        """Get all passthrough variables."""
        return frozenset(self._allowed_env_vars)

    def clear(self) -> None:
        """Clear all registrations (for session cleanup)."""
        self._credential_files.clear()
        self._allowed_env_vars.clear()


# ContextVar-backed singleton for per-session isolation
_registry_var: ContextVar[SessionEnvRegistry] = ContextVar("_session_env_registry")


def get_session_env_registry() -> SessionEnvRegistry:
    """Get or create the current session's registry."""
    try:
        return _registry_var.get()
    except LookupError:
        registry = SessionEnvRegistry()
        _registry_var.set(registry)
        return registry


def reset_session_env_registry() -> None:
    """Reset the registry for the current session (for testing)."""
    try:
        _registry_var.set(SessionEnvRegistry())
    except LookupError:
        pass
