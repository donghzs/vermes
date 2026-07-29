"""Idempotent logging shim: make ``logger.info()`` (no args) safe.

Why this exists
---------------
The Vermes fork replaced upstream ``print()`` separators with ``logger.info()``,
which introduced hundreds of no-arg ``logger.info()`` calls. ``logging.Logger.info``
requires ``msg`` as a positional argument, so every no-arg call raises
``TypeError: info() missing 1 required positional argument: 'msg'`` at runtime.

``cli.py`` installs the same shim inline, but ``vermes_cli/gateway.py`` (the
``hermes gateway ...`` subcommand path) does NOT import ``cli``. If that path is
reached without ``cli.py`` having loaded first, the no-arg calls crash in
production even though the test suite is green (tests install a conftest shim).

Importing this module guarantees the shim is active for any code path that loads
``vermes_cli.gateway`` — independent of entry point / import order.

This is a stopgap. Root fix (P2-1) reverts the no-arg calls to ``print()`` to
realign with upstream Hermes; at that point this module can be deleted.
"""

import logging as _logging

# Idempotency guard: never wrap more than once, even if cli.py's inline shim
# (or a re-import) already patched Logger.info. Double-wrapping is harmless
# functionally (each layer just defaults msg=""), but the sentinel keeps the
# call chain flat and makes the patch observable/testable.
_SENTINEL = "_vermes_safe_info_shim"

if not getattr(_logging.Logger.info, _SENTINEL, False):
    _orig_log_info = _logging.Logger.info

    def _safe_log_info(self, msg="", *args, **kwargs):
        return _orig_log_info(self, msg, *args, **kwargs)

    setattr(_safe_log_info, _SENTINEL, True)
    _logging.Logger.info = _safe_log_info
