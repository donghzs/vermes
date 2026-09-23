"""Idempotent logging shim: make ``logger.info()`` (no args) safe.

Why this exists
---------------
The Vermes fork replaced upstream ``print()`` separators with ``logger.info()``,
which introduced hundreds of no-arg ``logger.info()`` calls. ``logging.Logger.info``
requires ``msg`` as a positional argument, so every no-arg call raises
``TypeError: info() missing 1 required positional argument: 'msg'`` at runtime.

``cli.py`` installs the same shim inline, but ``vermes_cli/gateway.py`` (the
``vermes gateway ...`` subcommand path) does NOT import ``cli``. If that path is
reached without ``cli.py`` having loaded first, the no-arg calls crash in
production even though the test suite is green (tests install a conftest shim).

Importing this module guarantees the shim is active for any code path that loads
``vermes_cli.gateway`` — independent of entry point / import order.

This is a stopgap. Root fix (P2-1) reverts the no-arg calls to ``print()`` to
realign with upstream Vermes; at that point this module can be deleted.
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


# ── print-style kwargs 兼容层（2026-09-23 补）────────────────────────────
# print() → logger.info() 的迁移没做干净：有 5 处把 print 专属 kwargs 原样留下
#   vermes_cli/doctor.py:1852  logger.info(..., end="")
#   vermes_cli/kanban.py:2097/2254/2302/2333  logger.info(..., flush=True)
# logging 从不接受这三个参数 → 走到 stdlib 的 Logger._log 直接抛
#   TypeError: Logger._log() got an unexpected keyword argument 'end'
# 后果不止 CLI 崩：既有的 tests/vermes_cli/ 里有 49 个用例因为这个全红。
#
# 这里只吞 print 三件套（end/flush/sep），其余 kwargs 照常抛出 —— 不掩盖
# 真正的 logging API 误用（如把位置参数写错），只是让「换行/刷缓冲」这类
# 已无意义的遗留参数不再炸。**logging 语义里 end/flush 本就不存在**，
# 吞掉不改变任何输出行为（handler 自己管理 flush）。
#
# 同为 stopgap：那 5 处的根修是把 kwargs 删掉，届时本段连同 info shim 一并删除。
_SEP_SENTINEL = "_vermes_tolerant_log_shim"
_PRINT_ONLY_KWARGS = ("end", "flush", "sep")

if not getattr(_logging.Logger._log, _SEP_SENTINEL, False):
    _orig_log = _logging.Logger._log

    def _tolerant_log(self, level, msg, args, **kwargs):
        for _k in _PRINT_ONLY_KWARGS:
            kwargs.pop(_k, None)
        return _orig_log(self, level, msg, args, **kwargs)

    setattr(_tolerant_log, _SEP_SENTINEL, True)
    _logging.Logger._log = _tolerant_log
