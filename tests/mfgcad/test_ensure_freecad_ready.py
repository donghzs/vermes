"""ensure_freecad_ready 真实逻辑测试（M1-4）。

不下载、不拉 FreeCAD：用临时目录 + 注入 _installer 验证
「定位 freecadcmd → auto_setup 委托安装 → 复检」全链路真实逻辑。
真实默认安装路径（_default_freecad_installer）也用一个 fail-open 用例覆盖，
不触网（monkeypatch install_module_asset 抛 ModuleNotFoundError）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vermes_cli.mfgcad.engine_setup import (
    FREECAD_ENGINE_MODULE,
    FREECAD_SYSTEM_DIRS,
    _find_freecadcmd,
    ensure_freecad_ready,
    get_freecad_engine_dir,
)


@pytest.fixture(autouse=True)
def _isolate_system_freecad(monkeypatch):
    # 屏蔽系统 FreeCAD 发现，使测试不依赖「本机是否装了 FreeCAD」
    # （本机确实装了 FreeCAD 1.1，否则 _find_freecadcmd 会返回系统路径破坏 absent 断言）
    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.FREECAD_SYSTEM_DIRS", ()
    )


def _touch_freecadcmd(engine_dir: Path) -> Path:
    engine_dir.mkdir(parents=True, exist_ok=True)
    fc = engine_dir / "freecadcmd"
    fc.write_text("#!/bin/sh\n")
    fc.chmod(0o755)
    return fc


def test_get_freecad_engine_dir_default(monkeypatch):
    monkeypatch.delenv("VERMES_FREECAD_ENGINE_DIR", raising=False)
    d = get_freecad_engine_dir()
    assert d == Path.home() / ".vermes" / "engines" / "freecad"


def test_find_freecadcmd_present_and_absent(tmp_path):
    assert _find_freecadcmd(tmp_path / "none") is None
    fc = _touch_freecadcmd(tmp_path / "eng")
    assert _find_freecadcmd(tmp_path / "eng") == fc


def test_find_freecadcmd_falls_back_to_system_dirs(tmp_path, monkeypatch):
    # 确定性覆盖系统发现分支：把 FREECAD_SYSTEM_DIRS 指向含 fake freecadcmd 的临时目录
    sys_dir = tmp_path / "sysfreecad"
    fc = _touch_freecadcmd(sys_dir)
    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.FREECAD_SYSTEM_DIRS", (str(fc),)
    )
    # 引擎目录无 freecadcmd 时，应回退到系统目录（autouse fixture 已先置 ()，此处覆盖）
    assert _find_freecadcmd(tmp_path / "eng") == fc


def test_present_no_autosetup_returns_true(tmp_path):
    eng = tmp_path / "eng"
    _touch_freecadcmd(eng)
    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=False)
    assert ok is True
    assert msg == ""


def test_missing_no_autosetup_returns_false_with_guidance(tmp_path):
    eng = tmp_path / "eng"
    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=False)
    assert ok is False
    assert FREECAD_ENGINE_MODULE in msg
    assert "build123d" in msg  # 兜底引导


def test_autosetup_invokes_installer_and_succeeds(tmp_path):
    eng = tmp_path / "eng"

    def fake_installer(engine_dir, progress=None):
        _touch_freecadcmd(engine_dir)
        return True, ""

    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=True, _installer=fake_installer)
    assert ok is True
    assert _find_freecadcmd(eng) is not None  # 复检通过


def test_autosetup_installer_fails_returns_false(tmp_path):
    eng = tmp_path / "eng"

    def fake_installer(engine_dir, progress=None):
        return False, "boom: network down"

    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=True, _installer=fake_installer)
    assert ok is False
    assert "boom" in msg


def test_autosetup_installer_drops_file_not_at_expected_returns_false(tmp_path):
    eng = tmp_path / "eng"

    def fake_installer(engine_dir, progress=None):
        # 安装器成功但 freecadcmd 没落到引擎目录（资产布局错位）
        other = engine_dir / "other"
        other.mkdir(parents=True, exist_ok=True)
        (other / "freecadcmd").write_text("x")
        return True, ""

    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=True, _installer=fake_installer)
    assert ok is False
    assert "未出现" in msg


def test_default_installer_module_not_found_is_failopen(monkeypatch, tmp_path):
    # 模拟 P7 catalog 未发布该模块：install_module_asset 抛 ModuleNotFoundError
    def _raise_module_not_found(*args, **kwargs):
        raise ModuleNotFoundError("no such module")

    monkeypatch.setattr(
        "agent.module_catalog.install_module_asset", _raise_module_not_found
    )
    eng = tmp_path / "eng"
    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=True)
    assert ok is False
    assert FREECAD_ENGINE_MODULE in msg
