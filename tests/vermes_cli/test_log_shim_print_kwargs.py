"""print() → logger.info() 迁移遗留的冻结清单（2026-09-23）。

背景
----
Vermes fork 把上游的 ``print()`` 分隔符/状态行换成了 ``logger.info()``，但有 5 处
把 print 专属 kwargs 原样留下：

    vermes_cli/doctor.py:1852            logger.info(..., end="")
    vermes_cli/kanban.py:2097/2254/2302/2333   logger.info(..., flush=True)

``logging`` 从不接受 end/flush/sep → 直接抛
``TypeError: Logger._log() got an unexpected keyword argument 'end'``。

这不是"跑起来没事"的小问题：既有的 ``tests/vermes_cli/`` 里有 49 个用例因此全红，
``vermes doctor`` 走到 API Connectivity 也必崩。

处置
----
``vermes_cli/_log_shim.py`` 加了 print 三件套（end/flush/sep）容忍层兜住；
**根修是把这 5 处的 kwargs 删掉**（logging 语义里它们本就不存在，删掉输出完全不变）。

本测试是防回潮闸门：全仓 AST 扫描,任何**新增**的 print 风格 kwargs 调入 logging
必须在此变红。清理掉既有位置后，请同步收紧 ``KNOWN_LEGACY``。
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRINT_ONLY = ("end", "flush", "sep")

# 冻结清单：清理一处就删一行，使 KNOWN_LEGACY 始终 == 实扫结果。
# 注意：行号会随编辑漂移，以 _scan() 出来的实际集合为准。
KNOWN_LEGACY = {
    ("vermes_cli/kanban.py", 2097),
    ("vermes_cli/kanban.py", 2254),
    ("vermes_cli/kanban.py", 2302),
    ("vermes_cli/kanban.py", 2333),
    ("vermes_cli/doctor.py", 1852),
}

_SKIP_PARTS = (".venv", "node_modules", "/dist/", "dist*/", "__pycache__")


def _scan() -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for path in ROOT.rglob("*.py"):
        s = str(path)
        if any(p in s for p in _SKIP_PARTS):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            try:
                name = ast.unparse(node.func)
            except Exception:
                continue
            if "logger" not in name and not name.endswith(".log"):
                continue
            if any(k.arg in PRINT_ONLY for k in node.keywords if k.arg):
                out.add((path.relative_to(ROOT).as_posix(), node.lineno))
    return out


def test_no_new_print_style_kwargs():
    """不得新增 print 风格 kwargs 调入 logging（冻结清单，清理后收紧）。"""
    found = _scan()
    extra = found - KNOWN_LEGACY
    assert not extra, (
        "新增了 print 风格 kwargs 调入 logging"
        f"（{sorted(extra)}）—— logging 不接受 end/flush/sep，"
        "请把 kwargs 删掉而不是依赖 _log_shim 兜底"
    )


def test_log_shim_tolerates_print_kwargs():
    """_log_shim 必须真的兜住： loader 之后 logger.info(msg, end='') 不抛。"""
    import vermes_cli._log_shim  # noqa: F401  （导入即安装 shim）

    import logging

    logging.getLogger("vermes.print.kwargs.probe").info("x", end="", flush=True)


def test_log_shim_still_rejects_real_api_misuse():
    """双探针：只吞 print 三件套，不得把真正的 logging API 误用也一起放过。"""
    import vermes_cli._log_shim  # noqa: F401

    import logging

    try:
        logging.getLogger("vermes.print.kwargs.probe2").info("x", stacklevel="NOPE")
    except TypeError:
        return  # 期望：非 print 三件套的非法 kwargs 仍然抛
    raise AssertionError("shim 吞掉了非 end/flush/sep 的 kwargs —— 掩盖面过大")
