"""
tests/_isolate_plugin.py — per-file 进程隔离（中线，对齐 Hermes 上游思路）

为什么需要它（2026-09-28 实证）：
    ``tests/conftest.py::_reset_module_state`` 靠**手工枚举**清理模块级全局。
    这套办法永远追不上新加的 latch——``gateway.run._vermes_home`` 在 import 期
    锁存真实 ``~/.vermes``，就是漏网的一例。上游 hermes-agent 已经废弃「逐模块
    手工 reset」，改为 subprocess-per-file/per-test 隔离（tests/_isolate_plugin.py
    + scripts/run_tests.sh）。本插件是同一思路的落地。

设计取舍（为什么不默认全开）：
    * 仓库有 1500+ 测试文件；每文件起进程有固定开销。默认路径继续用
      xdist + ``_reset_module_state``（快），隔离模式留给 CI / 排障 / 易污染目录。
    * 进程隔离是一次性「换干净解释器」，不必再维护模块级全局清单。

用法
    pytest --isolate-files                     # 全部测试文件各起一进程
    pytest --isolate-files tests/gateway       # 只隔离该子树
    VERMES_ISOLATE_FILES=1 scripts/run_tests.sh tests/gateway

    也可以在测试文件头声明：
        pytestmark = pytest.mark.isolate_file
    （带该 mark 的文件强制子进程跑，即使主进程没开 --isolate-files）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


# ── CLI / 环境开关 ───────────────────────────────────────────────────────────

_ISOLATE_OPT = "--isolate-files"
_ENV = "VERMES_ISOLATE_FILES"


def _isolate_root(args: Sequence[str]) -> Optional[Path]:
    """从 argv 读 --isolate-files / --isolate-root=；未启用返回 None。"""
    enabled = False
    root = Path("tests")
    for a in args:
        if a == _ISOLATE_OPT or a.startswith(_ISOLATE_OPT + "="):
            enabled = True
            if "=" in a:
                root = Path(a.split("=", 1)[1] or "tests")
        if a.startswith("--isolate-root="):
            root = Path(a.split("=", 1)[1] or "tests")
    env = os.environ.get(_ENV, "").strip()
    if env:
        enabled = True
        if env not in ("1", "true", "yes"):
            root = Path(env)
    return root if enabled else None


def _iter_test_files(root: Path) -> List[Path]:
    root = root if root.exists() else Path("tests")
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("test_*.py") if p.is_file())


def _run_one_file(pytest_args: List[str], path: Path, basetemp: Path) -> dict:
    """子进程跑单文件，返回机器可读结果。"""
    # 去掉 --isolate-files / --isolate-root，避免子进程递归隔离。
    child_args = [
        a for a in pytest_args
        if not a.startswith(_ISOLATE_OPT) and not a.startswith("--isolate-root=")
    ]
    # 去掉路径类参数，改为本文件。
    child_args = [a for a in child_args if not str(a).startswith("tests")]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(path),
        *child_args,
        "-q",
        "--tb=line",
        "-p",
        "no:cacheprovider",
        f"--basetemp={basetemp / path.stem}",
        # 不继承父进程的 xdist 配置：子进程单文件跑即可
        "-p",
        "no:xdist",
    ]
    env = os.environ.copy()
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    # 子进程禁止再进 isolate 模式
    env.pop(_ENV, None)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    tail = "\n".join(proc.stdout.splitlines()[-8:])
    return {
        "file": str(path),
        "rc": proc.returncode,
        "tail": tail,
    }


# ── pytest 入口钩子 ─────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        _ISOLATE_OPT,
        action="store_true",
        default=False,
        help="per-file 子进程隔离（防模块级全局泄漏）",
    )
    parser.addoption(
        "--isolate-root",
        default=None,
        help="isolate 模式下默认收集根（默认 tests）",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "isolate_file: 在独立子进程中运行本测试文件（防模块级全局泄漏）",
    )


def pytest_cmdline_main(config):
    """拦截命令行：启用隔离时改走 per-file 子进程聚合。"""
    if not getattr(config.option, "isolate_files", False):
        if _isolate_root(sys.argv[1:]) is None:
            return None
        # env / = 形式启用，但 option 未置位
        config.option.isolate_files = True

    root_opt = getattr(config.option, "isolate_root", None)
    if not root_opt:
        parsed = _isolate_root(sys.argv[1:])
        root_opt = str(parsed) if parsed else "tests"
    root_path = Path(root_opt)

    user_paths = [
        a for a in config.args
        if not str(a).startswith(_ISOLATE_OPT) and not str(a).startswith("--isolate-root")
    ]
    if user_paths:
        files: List[Path] = []
        for p in user_paths:
            pp = Path(str(p).split("::")[0])
            if pp.suffix == ".py" and pp.is_file():
                files.append(pp)
            else:
                files.extend(_iter_test_files(pp))
        files = sorted(set(files))
    else:
        files = _iter_test_files(root_path)

    if not files:
        print(f"[isolate] no test files under {root_path}")
        return 0

    basetemp = Path(os.environ.get("TMPDIR", "/tmp")) / f"vermes-isolate-{os.getpid()}"
    basetemp.mkdir(parents=True, exist_ok=True)

    inv = list(
        getattr(getattr(config, "invocation_params", None), "args", None) or sys.argv[1:]
    )
    child_pytest_args = [
        a for a in inv
        if not str(a).startswith(_ISOLATE_OPT) and not str(a).startswith("--isolate-root")
    ]
    child_pytest_args = [a for a in child_pytest_args if not str(a).startswith("tests")]

    print(f"[isolate] {len(files)} file(s) in subprocesses …")
    failures = []
    for i, f in enumerate(files, 1):
        r = _run_one_file(child_pytest_args, f, basetemp)
        status = "ok" if r["rc"] == 0 else "FAIL"
        print(f"[{i}/{len(files)}] {status}  {f}")
        if r["rc"] != 0:
            print(r["tail"])
            failures.append(r)

    print()
    print(f"[isolate] done: {len(files) - len(failures)} ok, {len(failures)} failed")
    for r in failures:
        print(f"  FAILED {r['file']}")
    return 1 if failures else 0
