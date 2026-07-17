"""Shared lazy accessor for gateway.run module-level attributes.

Extracted (W2) from 4 byte-for-byte duplicate copies that lived in
config_loader_mixin / session_mixin / slash_commands_mixin / watcher_mixin.

Mixins must NOT import ``gateway.run`` at module load time (circular import:
``gateway.run`` composes the mixins). This helper defers the import to call
time and resolves the attribute dynamically, which also honors test
monkeypatching of ``gateway.run.<name>``.
"""


def _get_run_attr(name):
    """Dynamically resolve a module-level attribute from gateway.run."""
    from gateway import run as _run
    return getattr(_run, name)
