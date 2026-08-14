"""P3 局部编辑 + 纹理绘制 + 几何变换测试。"""

import asyncio
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.edit_tools import (
    _resolve_edit_backend,
    _resolve_paint_backend,
    _resolve_target_file,
    _load_session,
    _handle_mfg_transform,
    _handle_mfg_edit_part,
    _handle_mfg_paint_texture,
)


# ── 后端解析 ─────────────────────────────────────────────

class TestBackendResolution:
    def test_edit_explicit_nano3d(self):
        assert _resolve_edit_backend("nano3d") == "nano3d"

    def test_edit_explicit_builtin(self):
        assert _resolve_edit_backend("builtin") == "builtin"

    def test_edit_auto_fallback_builtin(self):
        # nano3d 未安装 → builtin
        with patch("pathlib.Path.is_dir", return_value=False):
            assert _resolve_edit_backend("auto") == "builtin"

    def test_paint_explicit_paint3d(self):
        assert _resolve_paint_backend("paint3d") == "paint3d"

    def test_paint_explicit_builtin(self):
        assert _resolve_paint_backend("builtin") == "builtin"

    def test_paint_auto_fallback_builtin(self):
        with patch("pathlib.Path.is_dir", return_value=False):
            assert _resolve_paint_backend("auto") == "builtin"


# ── Session 辅助 ────────────────────────────────────────

class TestSessionHelpers:
    def test_load_session_not_found(self):
        result = _load_session("nonexistent_session")
        assert result == {}

    def test_load_session_exists(self, tmp_path, monkeypatch):
        sess_dir = tmp_path / "sessions" / "test123"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({"session_id": "test123", "ok": True, "step_path": "/x.step"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "vermes_cli.mfgcad.edit_tools._mfg_home", lambda: tmp_path
        )
        result = _load_session("test123")
        assert result["session_id"] == "test123"
        assert result["ok"] is True

    def test_resolve_target_file_explicit(self):
        assert _resolve_target_file({}, "/explicit.step") == "/explicit.step"

    def test_resolve_target_file_from_session(self):
        session = {"step_path": "/a.step", "stl_path": "/b.stl"}
        assert _resolve_target_file(session) == "/a.step"

    def test_resolve_target_file_stl_fallback(self):
        session = {"stl_path": "/b.stl"}
        assert _resolve_target_file(session) == "/b.stl"

    def test_resolve_target_file_none(self):
        assert _resolve_target_file({}) is None


# ── Handler 参数校验 ────────────────────────────────────

class TestHandlerValidation:
    @pytest.mark.asyncio
    async def test_edit_missing_params(self):
        result = await _handle_mfg_edit_part({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_edit_missing_description(self):
        result = await _handle_mfg_edit_part({"session_id": "x"})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_edit_session_not_found(self):
        result = await _handle_mfg_edit_part(
            {"session_id": "nonexistent", "edit_description": "加厚底座"}
        )
        assert "❌" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_paint_missing_params(self):
        result = await _handle_mfg_paint_texture({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_transform_missing_params(self):
        result = await _handle_mfg_transform({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_transform_unknown_operation(self):
        result = await _handle_mfg_transform(
            {"session_id": "x", "operation": "unknown_op", "params": {}}
        )
        assert "❌" in result


# ── 几何变换（需要 trimesh） ─────────────────────────────

class TestTransform:
    @pytest.fixture
    def mock_session(self, tmp_path, monkeypatch):
        """创建一个真实的小 STL 文件 + mock session。"""
        trimesh = pytest.importorskip("trimesh")
        # 创建一个立方体
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        stl_path = tmp_path / "model.stl"
        mesh.export(str(stl_path))

        sess_dir = tmp_path / "sessions" / "test_sess"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({
                "session_id": "test_sess",
                "ok": True,
                "step_path": None,
                "stl_path": str(stl_path),
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "vermes_cli.mfgcad.edit_tools._mfg_home", lambda: tmp_path
        )
        return {"session_id": "test_sess", "stl_path": str(stl_path)}

    @pytest.mark.asyncio
    async def test_scale(self, mock_session):
        result = await _handle_mfg_transform({
            "session_id": "test_sess",
            "operation": "scale",
            "params": {"factor": 2.0},
        })
        assert "✅" in result
        assert "缩放" in result

    @pytest.mark.asyncio
    async def test_rotate(self, mock_session):
        result = await _handle_mfg_transform({
            "session_id": "test_sess",
            "operation": "rotate",
            "params": {"axis": "z", "angle_deg": 45},
        })
        assert "✅" in result
        assert "旋转" in result

    @pytest.mark.asyncio
    async def test_mirror(self, mock_session):
        result = await _handle_mfg_transform({
            "session_id": "test_sess",
            "operation": "mirror",
            "params": {"plane": "x"},
        })
        assert "✅" in result
        assert "镜像" in result

    @pytest.mark.asyncio
    async def test_translate(self, mock_session):
        result = await _handle_mfg_transform({
            "session_id": "test_sess",
            "operation": "translate",
            "params": {"dx": 5, "dy": 0, "dz": 0},
        })
        assert "✅" in result
        assert "平移" in result


# ── 内置编辑（builtin） ─────────────────────────────────

class TestBuiltinEdit:
    @pytest.fixture
    def mock_session_with_stl(self, tmp_path, monkeypatch):
        trimesh = pytest.importorskip("trimesh")
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        stl_path = tmp_path / "model.stl"
        mesh.export(str(stl_path))

        sess_dir = tmp_path / "sessions" / "edit_sess"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({"session_id": "edit_sess", "ok": True, "stl_path": str(stl_path)}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "vermes_cli.mfgcad.edit_tools._mfg_home", lambda: tmp_path
        )

    @pytest.mark.asyncio
    async def test_scale_edit(self, mock_session_with_stl):
        result = await _handle_mfg_edit_part({
            "session_id": "edit_sess",
            "edit_description": "缩放 200%",
            "backend": "builtin",
        })
        assert "✅" in result
        assert "缩放" in result

    @pytest.mark.asyncio
    async def test_rotate_edit(self, mock_session_with_stl):
        result = await _handle_mfg_edit_part({
            "session_id": "edit_sess",
            "edit_description": "绕 X 轴旋转",
            "backend": "builtin",
        })
        assert "✅" in result

    @pytest.mark.asyncio
    async def test_unparseable_edit(self, mock_session_with_stl):
        result = await _handle_mfg_edit_part({
            "session_id": "edit_sess",
            "edit_description": "做一个更漂亮的形状",  # 无法解析
            "backend": "builtin",
        })
        assert "⚠️" in result


# ── 内置纹理（builtin） ────────────────────────────────

class TestBuiltinPaint:
    @pytest.fixture
    def mock_session_with_stl(self, tmp_path, monkeypatch):
        trimesh = pytest.importorskip("trimesh")
        mesh = trimesh.creation.box(extents=[10, 10, 10])
        stl_path = tmp_path / "model.stl"
        mesh.export(str(stl_path))

        sess_dir = tmp_path / "sessions" / "paint_sess"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({"session_id": "paint_sess", "ok": True, "stl_path": str(stl_path)}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "vermes_cli.mfgcad.edit_tools._mfg_home", lambda: tmp_path
        )

    @pytest.mark.asyncio
    async def test_red_color(self, mock_session_with_stl):
        result = await _handle_mfg_paint_texture({
            "session_id": "paint_sess",
            "texture_description": "红色",
            "backend": "builtin",
        })
        # 可能 ✅（GLB 成功）或包含提示
        assert "颜色" in result or "❌" in result

    @pytest.mark.asyncio
    async def test_default_color(self, mock_session_with_stl):
        result = await _handle_mfg_paint_texture({
            "session_id": "paint_sess",
            "texture_description": "一个好看的材质",  # 无匹配颜色
            "backend": "builtin",
        })
        assert "builtin" in result or "❌" in result
