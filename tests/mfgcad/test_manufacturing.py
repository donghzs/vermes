"""制造链路测试。"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from vermes_cli.mfgcad.manufacturing import export_dxf, slice_gcode, send_print


def test_export_dxf_file_not_found():
    result = export_dxf(Path("/nonexistent/file.step"))
    assert result["ok"] is False
    assert "不存在" in result["error"]


def test_export_dxf_venv_not_found(tmp_path):
    # 创建假 STEP 文件
    step = tmp_path / "test.step"
    step.write_text("dummy")
    # venv 不存在
    result = export_dxf(step)
    assert result["ok"] is False
    assert "引擎" in result["error"] or "venv" in result["error"]


def test_slice_gcode_file_not_found():
    result = slice_gcode(Path("/nonexistent/file.stl"))
    assert result["ok"] is False
    assert "不存在" in result["error"]


def test_slice_gcode_no_slicer(tmp_path):
    stl = tmp_path / "test.stl"
    stl.write_text("dummy")
    with patch("shutil.which", return_value=None):
        result = slice_gcode(stl, dry_run=True)
    assert result["ok"] is False
    assert "未找到切片软件" in result["error"]


def test_slice_gcode_dry_run(tmp_path):
    stl = tmp_path / "test.stl"
    stl.write_text("dummy")
    with patch("shutil.which", return_value="/usr/bin/OrcaSlicer"):
        result = slice_gcode(stl, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "gcode_path" in result
    assert result["slicer"] == "orca"


def test_send_print_file_not_found():
    result = send_print(Path("/nonexistent/file.gcode"))
    assert result["ok"] is False
    assert "不存在" in result["error"]


def test_send_print_no_ip(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("dummy")
    result = send_print(gcode, printer_ip="")
    assert result["ok"] is False
    assert "未配置" in result["error"]


def test_send_print_dry_run(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("dummy")
    result = send_print(gcode, printer_ip="192.168.1.100", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert "192.168.1.100" in result["printer"]
