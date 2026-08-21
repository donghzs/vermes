"""ensure_freecad_ready 发现逻辑测试（M1-4 · discovery-first）。

不下载、不拉 FreeCAD：用临时目录 + monkeypatch 验证
「定位 freecadcmd（引擎目录→系统路径→PATH）→ 未找到则返回安装指引」全链路。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vermes_cli.mfgcad.engine_setup import (
    FREECAD_SYSTEM_DIRS,
    _find_freecadcmd,
    _install_guide,
    ensure_freecad_ready,
    get_freecad_engine_dir,
)


@pytest.fixture(autouse=True)
def _isolate_system_freecad(monkeypatch):
    # 屏蔽系统 FreeCAD 发现，使测试不依赖「本机是否装了 FreeCAD」
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


def test_get_freecad_engine_dir_env_override(monkeypatch):
    monkeypatch.setenv("VERMES_FREECAD_ENGINE_DIR", "/custom/fc")
    d = get_freecad_engine_dir()
    assert d == Path("/custom/fc")


def test_find_freecadcmd_absent(tmp_path):
    assert _find_freecadcmd(tmp_path / "none") is None


def test_find_freecadcmd_in_engine_dir(tmp_path):
    fc = _touch_freecadcmd(tmp_path / "eng")
    assert _find_freecadcmd(tmp_path / "eng") == fc


def test_find_freecadcmd_falls_back_to_system_dirs(tmp_path, monkeypatch):
    sys_dir = tmp_path / "sysfreecad"
    fc = _touch_freecadcmd(sys_dir)
    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.FREECAD_SYSTEM_DIRS", (str(fc),)
    )
    assert _find_freecadcmd(tmp_path / "eng") == fc


def test_find_freecadcmd_falls_back_to_path(tmp_path, monkeypatch):
    # 屏蔽系统目录，模拟 PATH 发现
    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.FREECAD_SYSTEM_DIRS", ()
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fc = fake_bin / "freecadcmd"
    fc.write_text("#!/bin/sh\n")
    fc.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda _: str(fc))
    assert _find_freecadcmd(tmp_path / "eng") == fc


def test_find_freecadcmd_engine_dir_takes_priority(tmp_path, monkeypatch):
    # 引擎目录有 freecadcmd 时，不应回退到系统路径
    eng_fc = _touch_freecadcmd(tmp_path / "eng")
    sys_dir = tmp_path / "sysfreecad"
    sys_fc = _touch_freecadcmd(sys_dir)
    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.FREECAD_SYSTEM_DIRS", (str(sys_fc),)
    )
    assert _find_freecadcmd(tmp_path / "eng") == eng_fc


def test_present_returns_true(tmp_path):
    eng = tmp_path / "eng"
    _touch_freecadcmd(eng)
    ok, msg = ensure_freecad_ready(engine_dir=eng)
    assert ok is True
    assert msg == ""


def test_missing_returns_false_with_install_guide(tmp_path, monkeypatch):
    eng = tmp_path / "eng"
    # 确保系统 FreeCAD 不被发现
    monkeypatch.setattr("shutil.which", lambda _: None)
    ok, msg = ensure_freecad_ready(engine_dir=eng)
    assert ok is False
    # 安装指引应包含 freecad.org 和平台关键词
    assert "freecad.org" in msg
    assert "安装" in msg


def test_install_guide_macos(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    guide = _install_guide()
    assert "brew" in guide
    assert "DMG" in guide


def test_install_guide_windows(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    guide = _install_guide()
    assert "winget" in guide


def test_install_guide_linux(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    guide = _install_guide()
    assert "apt" in guide or "dnf" in guide


def test_auto_setup_ignored_after_discovery_redesign(tmp_path, monkeypatch):
    # auto_setup=True 不再触发下载，与 auto_setup=False 行为一致
    eng = tmp_path / "eng"
    monkeypatch.setattr("shutil.which", lambda _: None)
    ok, msg = ensure_freecad_ready(engine_dir=eng, auto_setup=True)
    assert ok is False
    assert "freecad.org" in msg
