"""P4 多模态控制工具测试。"""

import asyncio
import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.multimodal_tools import (
    _resolve_image_backend,
    _save_uploaded_image,
    _handle_mfg_image_to_cad,
    _handle_mfg_bbox_to_cad,
    _handle_mfg_multi_view_to_cad,
)


# ── 后端解析 ─────────────────────────────────────────────

class TestImageBackendResolution:
    def test_explicit_trellis(self):
        assert _resolve_image_backend("trellis") == "trellis"

    def test_explicit_cloud_api(self):
        assert _resolve_image_backend("cloud_api") == "cloud_api"

    def test_explicit_mac(self):
        assert _resolve_image_backend("mac") == "mac"

    def test_auto_fallback_mac(self, monkeypatch):
        # trellis 未安装 + 无 cloud key → mac
        monkeypatch.setattr("pathlib.Path.is_dir", lambda self: False)
        monkeypatch.delenv("TRELLIS_CLOUD_API_KEY", raising=False)
        assert _resolve_image_backend("auto") == "mac"


# ── 参数校验 ─────────────────────────────────────────────

class TestHandlerValidation:
    @pytest.mark.asyncio
    async def test_image_missing_path(self):
        result = await _handle_mfg_image_to_cad({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_image_file_not_found(self):
        result = await _handle_mfg_image_to_cad({"image_path": "/nonexistent.jpg"})
        assert "❌" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_bbox_missing_path(self):
        result = await _handle_mfg_bbox_to_cad({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_multi_view_missing_front(self):
        result = await _handle_mfg_multi_view_to_cad({})
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_multi_view_missing_side(self):
        result = await _handle_mfg_multi_view_to_cad({"front_image": "/x.jpg"})
        assert "❌" in result


# ── 图片保存 ─────────────────────────────────────────────

class TestImageSave:
    def test_save_image(self, tmp_path, monkeypatch):
        # 创建假图片
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")  # JPEG magic bytes

        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )
        result = _save_uploaded_image(str(img), "test_session")
        assert result
        assert "test.jpg" in result
        assert Path(result).is_file()

    def test_save_image_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )
        result = _save_uploaded_image("/nonexistent.jpg", "test_session")
        assert result == ""


# ── MAC fallback 路径 ───────────────────────────────────

class TestMacFallback:
    @pytest.mark.asyncio
    async def test_mac_no_key(self, tmp_path, monkeypatch):
        # 创建假图片
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )
        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._resolve_image_backend",
            lambda x: "mac",
        )

        with patch("vermes_cli.mfgcad.tools._resolve_api_key", return_value=""):
            result = await _handle_mfg_image_to_cad({
                "image_path": str(img),
                "backend": "mac",
            })
        assert "❌" in result
        assert "key" in result.lower() or "api" in result.lower()

    @pytest.mark.asyncio
    async def test_mac_with_description(self, tmp_path, monkeypatch):
        """有 description 时跳过 vision 提取，直接走 MAC。"""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )

        # mock MAC backend
        mock_backend = MagicMock()
        mock_result = MagicMock()
        mock_result.files = {"step": "/out.step", "stl": "/out.stl"}
        mock_backend.generate = AsyncMock(return_value=mock_result)

        with patch(
            "vermes_cli.mfgcad.multimodal_tools._resolve_image_backend",
            lambda x: "mac",
        ), patch(
            "vermes_cli.mfgcad.tools._resolve_api_key", return_value="fake_key"
        ), patch(
            "vermes_cli.mfgcad.engine_backends.resolve_backend", return_value=mock_backend
        ):
            result = await _handle_mfg_image_to_cad({
                "image_path": str(img),
                "description": "圆柱体，外径50mm，高100mm",
                "backend": "mac",
            })

        assert "✅" in result
        assert "step" in result.lower() or "stl" in result.lower()
        assert "mac" in result.lower()


# ── Bbox 转发 ──────────────────────────────────────────

class TestBboxForward:
    @pytest.mark.asyncio
    async def test_bbox_forwards_to_image(self, tmp_path, monkeypatch):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")

        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )

        # mock _handle_mfg_image_to_cad
        async def mock_handler(args):
            return f"✅ forwarded with: {args.get('description', '')}"

        with patch(
            "vermes_cli.mfgcad.multimodal_tools._handle_mfg_image_to_cad",
            mock_handler,
        ):
            result = await _handle_mfg_bbox_to_cad({
                "image_path": str(img),
                "bboxes": [
                    {"label": "主体", "x": 10, "y": 10, "width": 100, "height": 200},
                    {"label": "把手", "x": 120, "y": 50, "width": 30, "height": 80},
                ],
                "description": "不锈钢水杯",
            })

        assert "✅" in result
        assert "主体" in result
        assert "把手" in result


# ── 多视图 ─────────────────────────────────────────────

class TestMultiView:
    @pytest.mark.asyncio
    async def test_multi_view_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )
        result = await _handle_mfg_multi_view_to_cad({
            "front_image": "/nonexistent.jpg",
            "side_image": "/also_nonexistent.jpg",
        })
        assert "❌" in result

    @pytest.mark.asyncio
    async def test_multi_view_forwards(self, tmp_path, monkeypatch):
        front = tmp_path / "front.jpg"
        front.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        side = tmp_path / "side.jpg"
        side.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")

        monkeypatch.setattr(
            "vermes_cli.mfgcad.multimodal_tools._mfg_home", lambda: tmp_path
        )

        async def mock_handler(args):
            return f"✅ multi-view → {args.get('description', '')}"

        with patch(
            "vermes_cli.mfgcad.multimodal_tools._handle_mfg_image_to_cad",
            mock_handler,
        ):
            result = await _handle_mfg_multi_view_to_cad({
                "front_image": str(front),
                "side_image": str(side),
                "description": "零件三视图",
            })

        assert "✅" in result
        assert "多视图" in result
