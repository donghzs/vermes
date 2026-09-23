"""契约测：跨安装文件句柄分级 —— 只读 WARN / 写 FAIL（T2 canary 收尾）。

QClaw 交接点名：`check_cross_handles` 此前不区分访问模式，只读窥探与写入污染同判 FAIL。
真隔离被**写**破坏才算硬红；只读降 WARN。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "vermes" / "check_coexistence.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_coexistence", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_handle_severity_read_is_warn_write_is_fail():
    """只读→WARN；写/读写→FAIL；未知 mode 按只读处理（宁松勿假红）。"""
    sev = _load().handle_severity
    assert sev("r") == "WARN"
    assert sev("w") == "FAIL"
    assert sev("u") == "FAIL"
    assert sev("?") == "WARN"


def test_open_paths_returns_mode_pairs():
    """open_paths 必须返回 (path, mode) 而非裸路径字符串（否则无法分级）。"""
    import inspect

    mod = _load()
    sig = inspect.signature(mod.open_paths)
    # 返回类型注解应体现 tuple；至少源码里不得再 `list[str]` 只还路径
    src = inspect.getsource(mod.open_paths)
    assert "list[tuple[str, str]]" in src or "-> list[tuple" in src.replace(" ", "")
    assert "handle_severity" in inspect.getsource(mod.check_cross_handles)


def test_open_paths_parses_lsof_f_mode(monkeypatch):
    """解析 lsof -Ffn 输出：f 块携带 r/w/u，n 行挂到最近一次 mode。"""
    mod = _load()
    fake = "\n".join(
        [
            "p12345",
            "fcwd",
            "n/proc/self",
            "f3r",
            "n/tmp/other-install/config.yaml",
            "f4w",
            "n/tmp/other-install/state.db",
            "f5u",
            "n/tmp/other-install/cache.bin",
            "f6r",
            "n/tmp/mine/ok.txt",
        ]
    )
    monkeypatch.setattr(mod, "run", lambda *a, **k: fake)
    rows = mod.open_paths([12345])
    by_path = dict(rows)
    assert by_path["/tmp/other-install/config.yaml"] == "r"
    assert by_path["/tmp/other-install/state.db"] == "w"
    assert by_path["/tmp/other-install/cache.bin"] == "u"
    assert by_path["/tmp/mine/ok.txt"] == "r"
    # cwd/txt 无模式位 → ?
    assert by_path["/proc/self"] == "?"
    assert mod.handle_severity(by_path["/tmp/other-install/state.db"]) == "FAIL"
    assert mod.handle_severity(by_path["/tmp/other-install/config.yaml"]) == "WARN"
