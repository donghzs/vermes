"""env-hints 真值断言（Hermes 2026-09-23 建议）：归一化不吞「填错行」。

`_MACHINE_NORMALIZERS` 把 Host/$HOME/cwd 换成 {{HOST}}/{{HOME}}/{{CWD}} 只负责
跨机器可移植。若哪天代码把 home 填进了 cwd 那一行，gold 两边都是占位符比对不会红。
故正确性由本文件在**归一化之前**钉真值：
  cwd 行 == os.getcwd()；home 行 == $HOME；host 行 == 平台标识；且 cwd 行不得被 home 污染。
"""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "s2_snapshot.py"


def _load_s2():
    spec = importlib.util.spec_from_file_location("s2_snapshot", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hints() -> str:
    import agent.prompt_builder as _pb

    _pb._clear_backend_probe_cache()
    return _pb.build_environment_hints()


def _line_startswith(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"未找到以 {prefix!r} 开头的行")


def test_cwd_line_is_true_cwd():
    """归一化前：Current working directory 行必须等于真实 cwd。"""
    line = _line_startswith(_hints(), "Current working directory:")
    assert line == f"Current working directory: {os.getcwd()}"


def test_home_line_is_true_home():
    """归一化前：User home directory 行必须等于 $HOME。"""
    line = _line_startswith(_hints(), "User home directory:")
    assert line == f"User home directory: {os.path.expanduser('~')}"


def test_host_line_matches_platform():
    """归一化前：Host 行必须带真实平台标识（darwin→macOS / win32→Windows / 其他→system）。"""
    line = _line_startswith(_hints(), "Host:")
    if sys.platform == "darwin":
        assert line.startswith("Host: macOS")
    elif sys.platform == "win32":
        assert line.startswith("Host: Windows")
    else:
        assert platform.system() in line


def test_cwd_line_not_polluted_with_home():
    """Hermes 点名的填错行：cwd 行不得是 home 路径（归一化会吞掉这类错，这里兜住）。"""
    cwd_line = _line_startswith(_hints(), "Current working directory:")
    home = os.path.expanduser("~")
    # 真值 cwd 在 macOS 上常是 home 的子路径——所以断言「整行 == cwd 行格式」
    # 而不是「不含 home 子串」。若误把 home 填进 cwd 行，整行会等于 home 行。
    home_line = _line_startswith(_hints(), "User home directory:")
    assert cwd_line != home_line, "cwd 行被 home 填充 —— 归一化后 gold 两边都是占位符，比对不会红"
    assert cwd_line == f"Current working directory: {os.getcwd()}"
    assert home_line == f"User home directory: {home}"


def test_normalize_machine_swaps_true_for_placeholders():
    """归一化分工：真值行 → 占位符；不再残留真实 cwd/home 路径。"""
    mod = _load_s2()
    raw = _hints()
    norm = mod.normalize_machine(raw)
    assert "Current working directory: {{CWD}}" in norm
    assert "User home directory: {{HOME}}" in norm
    assert "Host: {{HOST}}" in norm
    assert os.getcwd() not in norm, "归一化后不得残留真实 cwd"
    # home 是 cwd 的前缀时不能只查 cwd；单独断言 home 行已变占位符
    home_line = _line_startswith(norm, "User home directory:")
    assert home_line == "User home directory: {{HOME}}"


def test_normalize_machine_is_identity_on_placeholders():
    """幂等：已归一文本再跑一遍不得再变（避免生成/比对两侧不一致）。"""
    mod = _load_s2()
    once = mod.normalize_machine(_hints())
    twice = mod.normalize_machine(once)
    assert once == twice
