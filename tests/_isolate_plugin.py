"""
tests/_isolate_plugin.py — per-file 进程隔离（中线，对齐 Hermes 上游思路）

为什么需要它（2026-09-28 实证）：
    ``tests/conftest.py::_reset_module_state`` 靠**手工枚举**清理模块级全局。
    这套办法永远追不上新加的 latch——``gateway.run._vermes_home`` 在 import 期
    锁存真实 ``~/.vermes``，就是漏网的一例。上游 hermes-agent 已经废弃「逐模块
    手工 reset」，改为 subprocess-per-file/per-test 隔离。本插件是同一思路的落地。

设计取舍（为什么不默认全开）：
    * 仓库有 1500+ 测试文件；每文件起进程有固定开销。默认路径继续用
      xdist + ``_reset_module_state``（快），隔离模式留给 CI / 排障 / 易污染目录。
    * 进程隔离是一次性「换干净解释器」，不必再维护模块级全局清单。

用法
    pytest --isolate-files                     # 全部测试文件各起一进程
    pytest --isolate-files tests/gateway       # 只隔离该子树
    VERMES_ISOLATE_FILES=1 scripts/run_tests.sh tests/gateway

    支持：-k / -m / -v / --tb / --junitxml=（多文件自动合并为一份）
    不支持：--lf / --ff（子进程无共享 cache，语义会撒谎）——显式报错退出。

注册：根目录 conftest.py 的 ``pytest_plugins``（rootdir 位置，pytest 9 安全）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional, Sequence


# ── CLI / 环境开关 ───────────────────────────────────────────────────────────

_ISOLATE_OPT = "--isolate-files"
_ENV = "VERMES_ISOLATE_FILES"
# 这些选项依赖 pytest cache / 单进程收集，per-file 子进程下语义会撒谎
_UNSUPPORTED_OPTS = ("--lf", "--ff", "--last-failed", "--failed-first", "--cache-show")


def _isolate_root(args: Sequence[str]) -> Optional[Path]:
    """从 argv 读 --isolate-files / --isolate-root=；未启用返回 None。"""
    enabled = False
    root = Path("tests")
    for a in args:
        if a == _ISOLATE_OPT:
            enabled = True
        if a.startswith(_ISOLATE_OPT + "="):
            enabled = True
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


def _strip_isolate_opts(args: Sequence[str]) -> List[str]:
    out = []
    for a in args:
        s = str(a)
        if s.startswith(_ISOLATE_OPT) or s.startswith("--isolate-root"):
            continue
        if s.startswith("tests"):
            continue
        out.append(s)
    return out


def _reject_unsupported(args: Sequence[str]) -> Optional[str]:
    for a in args:
        if a in _UNSUPPORTED_OPTS or a.startswith("--lf") or a.startswith("--ff"):
            return a
    return None


def _run_one_file(
    pytest_args: List[str],
    path: Path,
    basetemp: Path,
    junit_dir: Optional[Path],
) -> dict:
    """子进程跑单文件，返回机器可读结果（失败时带全文输出）。"""
    child_args = _strip_isolate_opts(pytest_args)
    cmd = [
        sys.executable, "-m", "pytest", str(path),
        *child_args,
        # 排障友好：失败看短 traceback 而不是单行；成功仍可 -q
        "--tb=short",
        "-p", "no:cacheprovider",
        f"--basetemp={basetemp / path.stem}",
        "-p", "no:xdist",
    ]
    if junit_dir is not None:
        cmd.append(f"--junitxml={junit_dir / (path.stem + '.xml')}")

    env = os.environ.copy()
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    env.pop(_ENV, None)  # 子进程禁止递归隔离
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "file": str(path),
        "rc": proc.returncode,
        "output": out,
    }


def _merge_junitxml(junit_dir: Path, dest: Path) -> bool:
    """把各文件的 junit 片段合并为单一 reports.xml（CI 用）。"""
    parts = sorted(junit_dir.glob("*.xml"))
    if not parts:
        return False
    suite = ET.Element("testsuites", name="vermes-isolate")
    total = fails = errors = skipped = 0
    for p in parts:
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        # pytest 写的是 <testsuites><testsuite/></testsuites> 或 <testsuite/>
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        for s in suites:
            suite.append(s)
            total += int(s.get("tests", 0) or 0)
            fails += int(s.get("failures", 0) or 0)
            errors += int(s.get("errors", 0) or 0)
            skipped += int(s.get("skipped", 0) or 0)
    suite.set("tests", str(total))
    suite.set("failures", str(fails))
    suite.set("errors", str(errors))
    suite.set("skipped", str(skipped))
    dest.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(dest, encoding="utf-8", xml_declaration=True)
    return True


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
    inv = list(
        getattr(getattr(config, "invocation_params", None), "args", None) or sys.argv[1:]
    )

    if not getattr(config.option, "isolate_files", False):
        if _isolate_root(sys.argv[1:]) is None:
            return None
        config.option.isolate_files = True

    bad = _reject_unsupported(inv)
    if bad:
        print(
            f"[isolate] 不支持 {bad}：per-file 子进程没有共享 pytest cache，"
            f"--lf/--ff 会静默跑全文件、结果撒谎。请去掉该选项，"
            f"或不用 --isolate-files。"
        )
        return 2

    root_opt = getattr(config.option, "isolate_root", None)
    if not root_opt:
        parsed = _isolate_root(sys.argv[1:])
        root_opt = str(parsed) if parsed else "tests"
    root_path = Path(root_opt)

    user_paths = [a for a in config.args if not str(a).startswith(_ISOLATE_OPT)
                  and not str(a).startswith("--isolate-root")]
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

    # junitxml：父选项 → 各子文件写片段 → 末尾合并
    junit_dest: Optional[Path] = None
    junit_dir: Optional[Path] = None
    for i, a in enumerate(inv):
        if str(a).startswith("--junitxml="):
            junit_dest = Path(str(a).split("=", 1)[1])
        elif str(a) == "--junitxml" and i + 1 < len(inv):
            junit_dest = Path(str(inv[i + 1]))
    child_pytest_args = _strip_isolate_opts(inv)
    child_pytest_args = [a for a in child_pytest_args
                         if not str(a).startswith("--junitxml")]
    if junit_dest is not None:
        junit_dir = basetemp / "junit"
        junit_dir.mkdir(parents=True, exist_ok=True)

    verbose = "-v" in inv or "--verbose" in inv
    print(f"[isolate] {len(files)} file(s) in subprocesses …")
    failures = []
    for i, f in enumerate(files, 1):
        r = _run_one_file(child_pytest_args, f, basetemp, junit_dir)
        status = "ok" if r["rc"] == 0 else "FAIL"
        print(f"[{i}/{len(files)}] {status}  {f}")
        if r["rc"] != 0:
            # 失败给全文（不是 tail 8），CI 能直接排障
            print(r["output"])
            failures.append(r)
        elif verbose:
            print(r["output"])

    if junit_dest is not None and junit_dir is not None:
        if _merge_junitxml(junit_dir, junit_dest):
            print(f"[isolate] junitxml → {junit_dest}")

    print()
    print(f"[isolate] done: {len(files) - len(failures)} ok, {len(failures)} failed")
    for r in failures:
        print(f"  FAILED {r['file']}")
    return 1 if failures else 0
