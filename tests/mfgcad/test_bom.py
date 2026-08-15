"""切片② BOM + 组装指南测试。"""
import json
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def tmp_session(tmp_path, monkeypatch):
    """创建临时 session 目录和数据。"""
    sessions_dir = tmp_path / "sessions" / "test_bom_001"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "session.json").write_text(json.dumps({
        "session_id": "test_bom_001",
        "request": "做一个笔筒：外径60mm，高度100mm，壁厚3mm",
        "ok": True,
        "volume_mm3": 53721.23,
        "step_path": "/tmp/fake.step",
    }), encoding="utf-8")
    (sessions_dir / "build123d_source.py").write_text(
        "from build123d import Cylinder\n"
        "outer = Cylinder(30, 100)\n"
        "inner = Cylinder(27, 97)\n"
        "result = outer - inner\n",
        encoding="utf-8",
    )
    (sessions_dir / "parameters.json").write_text(json.dumps([
        {"name": "OUTER_RADIUS", "value": 30.0, "unit": "mm", "desc": "外半径"},
        {"name": "HEIGHT", "value": 100.0, "unit": "mm", "desc": "高度"},
        {"name": "WALL_THICKNESS", "value": 3.0, "unit": "mm", "desc": "壁厚"},
    ]), encoding="utf-8")

    # monkeypatch _mfg_home
    monkeypatch.setattr(
        "vermes_cli.mfgcad.bom._mfg_home",
        lambda: tmp_path / "mfgcad",
    )
    # 重建 session 目录
    target = tmp_path / "mfgcad" / "sessions" / "test_bom_001"
    target.mkdir(parents=True)
    (target / "session.json").write_text(json.dumps({
        "session_id": "test_bom_001",
        "request": "做一个笔筒：外径60mm，高度100mm，壁厚3mm",
        "ok": True,
        "volume_mm3": 53721.23,
    }), encoding="utf-8")
    (target / "build123d_source.py").write_text(
        "from build123d import Cylinder\nresult = Cylinder(30, 100)\n",
        encoding="utf-8",
    )
    (target / "parameters.json").write_text(json.dumps([
        {"name": "OUTER_RADIUS", "value": 30.0, "unit": "mm", "desc": "外半径"},
    ]), encoding="utf-8")
    return "test_bom_001"


class TestBOMGeneration:
    """BOM 生成核心逻辑。"""

    @pytest.mark.asyncio
    async def test_generate_bom_success(self, tmp_session):
        """正常生成 BOM。"""
        from vermes_cli.mfgcad.bom import generate_bom

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "## BOM 表\n| 序号 | 零件 | 规格 |"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await generate_bom(
                session_id=tmp_session,
                api_key="sk-test",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
            )

        assert "BOM" in result
        assert "序号" in result

    @pytest.mark.asyncio
    async def test_generate_bom_session_not_found(self, tmp_path, monkeypatch):
        """session 不存在。"""
        monkeypatch.setattr(
            "vermes_cli.mfgcad.bom._mfg_home",
            lambda: tmp_path / "mfgcad",
        )
        from vermes_cli.mfgcad.bom import generate_bom

        result = await generate_bom(
            session_id="nonexistent",
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
        )
        assert "未找到会话" in result

    @pytest.mark.asyncio
    async def test_generate_bom_http_error(self, tmp_session):
        """LLM 调用失败。"""
        import httpx
        from vermes_cli.mfgcad.bom import generate_bom

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Internal Server Error", request=MagicMock(), response=MagicMock()
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                await generate_bom(
                    session_id=tmp_session,
                    api_key="sk-test",
                    base_url="https://api.deepseek.com/v1",
                    model="deepseek-chat",
                )


class TestBOMHelpers:
    """BOM 辅助函数。"""

    def test_infer_material_aluminum(self):
        from vermes_cli.mfgcad.bom import _infer_material
        assert "铝" in _infer_material("做一个铝合金支架")

    def test_infer_material_steel(self):
        from vermes_cli.mfgcad.bom import _infer_material
        assert "钢" in _infer_material("碳钢零件")

    def test_infer_material_plastic(self):
        from vermes_cli.mfgcad.bom import _infer_material
        assert "ABS" in _infer_material("ABS塑料外壳")

    def test_infer_material_default(self):
        from vermes_cli.mfgcad.bom import _infer_material
        assert "通用" in _infer_material("做一个笔筒")

    def test_build_bom_prompt_contains_all_sections(self, tmp_session):
        """提示词包含所有必需段落。"""
        from vermes_cli.mfgcad.bom import _build_bom_prompt, _load_session, _load_source, _load_parameters
        session = _load_session(tmp_session)
        source = _load_source(tmp_session)
        parameters = _load_parameters(tmp_session)
        prompt = _build_bom_prompt(session, source, parameters, "做一个笔筒")
        assert "BOM 表" in prompt
        assert "组装指南" in prompt
        assert "成本估算" in prompt
        assert "3D 打印建议" in prompt
        assert "笔筒" in prompt

    def test_build_bom_prompt_with_preset(self, tmp_session):
        """带 preset 时提示词包含材料信息。"""
        from vermes_cli.mfgcad.bom import _build_bom_prompt, _load_session
        session = _load_session(tmp_session)
        preset = {"material": "ABS", "process": "注塑"}
        prompt = _build_bom_prompt(session, None, [], "做外壳", preset)
        assert "ABS" in prompt

    def test_load_session(self, tmp_session):
        from vermes_cli.mfgcad.bom import _load_session
        s = _load_session(tmp_session)
        assert s["session_id"] == tmp_session
        assert s["ok"] is True

    def test_load_source(self, tmp_session):
        from vermes_cli.mfgcad.bom import _load_source
        src = _load_source(tmp_session)
        assert src is not None
        assert "build123d" in src

    def test_load_parameters(self, tmp_session):
        from vermes_cli.mfgcad.bom import _load_parameters
        params = _load_parameters(tmp_session)
        assert len(params) == 1
        assert params[0]["name"] == "OUTER_RADIUS"


class TestBOMHandler:
    """tools.py handler 集成。"""

    @pytest.mark.asyncio
    async def test_handler_missing_session_id(self):
        from vermes_cli.mfgcad.tools import _handle_mfg_generate_bom
        result = await _handle_mfg_generate_bom({})
        assert "缺少 session_id" in result

    @pytest.mark.asyncio
    async def test_handler_no_api_key(self, monkeypatch):
        """无 API key 时返回明确错误。"""
        from vermes_cli.mfgcad.tools import _handle_mfg_generate_bom
        monkeypatch.setattr(
            "vermes_cli.mfgcad.tools._resolve_mfgcad_service_creds",
            lambda: ("", "", ""),
        )
        result = await _handle_mfg_generate_bom({"session_id": "test"})
        assert "API Key" in result or "未配置" in result

    @pytest.mark.asyncio
    async def test_handler_success(self, tmp_session, monkeypatch):
        """正常路径。"""
        from vermes_cli.mfgcad.tools import _handle_mfg_generate_bom

        monkeypatch.setattr(
            "vermes_cli.mfgcad.tools._resolve_mfgcad_service_creds",
            lambda: ("sk-test", "https://api.deepseek.com/v1", "deepseek-chat"),
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "## BOM 表\n| 1 | 筒身 | OD60 | ABS | 1 |"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            result = await _handle_mfg_generate_bom({"session_id": tmp_session})

        assert "BOM" in result


class TestToolsetConsistency:
    """工具注册一致性。"""

    def test_mfg_generate_bom_in_toolsets(self):
        import toolsets
        assert "mfg_generate_bom" in toolsets.TOOLSETS["mfgcad"]["tools"]

    def test_mfg_rebuild_parametric_in_toolsets(self):
        import toolsets
        assert "mfg_rebuild_parametric" in toolsets.TOOLSETS["mfgcad"]["tools"]

    def test_toolset_count_matches_10(self):
        import toolsets
        tools = toolsets.TOOLSETS["mfgcad"]["tools"]
        assert len(tools) == 10, f"Expected 10 mfgcad tools, got {len(tools)}: {tools}"
