"""P0a 歧义澄清 + P0c 行业 preset 测试。"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.clarify import (
    _load_presets,
    get_preset_names,
    get_preset,
    _guess_preset,
    _format_slots_for_llm,
    _build_enhanced_request,
    check_clarity,
)


# ── preset 加载 ──────────────────────────────────────────

class TestPresetLoading:
    def test_loads_four_presets(self):
        names = get_preset_names()
        assert "mechanical_part" in names
        assert "print_part" in names
        assert "ecommerce_display" in names
        assert "film_prop" in names
        assert len(names) == 4

    def test_mechanical_part_required_slots(self):
        p = get_preset("mechanical_part")
        assert p is not None
        required = [s["name"] for s in p["slots"] if s.get("required")]
        assert "geometry_type" in required
        assert "dimensions" in required

    def test_print_part_has_wall_thickness_default(self):
        p = get_preset("print_part")
        wt = [s for s in p["slots"] if s["name"] == "wall_thickness"][0]
        assert wt.get("default") == 2.0

    def test_ecommerce_uses_trellis_engine(self):
        p = get_preset("ecommerce_display")
        assert p.get("engine") == "trellis"

    def test_film_prop_required_is_theme(self):
        p = get_preset("film_prop")
        required = [s["name"] for s in p["slots"] if s.get("required")]
        assert required == ["theme"]

    def test_get_preset_unknown_returns_none(self):
        assert get_preset("nonexistent") is None


# ── preset 猜测 ──────────────────────────────────────────

class TestGuessPreset:
    def test_print_keywords(self):
        presets = _load_presets()
        assert _guess_preset("做一个3D打印的手机支架", presets) == "print_part"
        assert _guess_preset("FDM打印PLA材料齿轮", presets) == "print_part"

    def test_ecommerce_keywords(self):
        presets = _load_presets()
        assert _guess_preset("电商产品展示运动鞋", presets) == "ecommerce_display"

    def test_film_keywords(self):
        presets = _load_presets()
        assert _guess_preset("赛博朋克手枪道具", presets) == "film_prop"
        assert _guess_preset("古风茶具手办", presets) == "film_prop"

    def test_default_mechanical(self):
        presets = _load_presets()
        assert _guess_preset("做一个法兰盘外径100mm", presets) == "mechanical_part"

    def test_no_match_returns_none(self):
        presets = {}
        assert _guess_preset("随便什么", presets) is None


# ── LLM prompt 构建 ────────────────────────────────────────

class TestPromptBuilding:
    def test_format_slots_includes_required(self):
        p = get_preset("mechanical_part")
        s = _format_slots_for_llm(p)
        assert "必填" in s
        assert "几何类型" in s
        assert "关键尺寸" in s

    def test_build_enhanced_adds_defaults(self):
        p = get_preset("mechanical_part")
        extracted = {"geometry_type": "圆柱体", "dimensions": "外径60mm 高100mm"}
        enhanced = _build_enhanced_request("做一个笔筒", extracted, p)
        # 应补默认公差
        assert "公差" in enhanced or "一般公差" in enhanced

    def test_build_enhanced_preserves_original(self):
        p = get_preset("mechanical_part")
        enhanced = _build_enhanced_request("做一个法兰盘 Φ100", {}, p)
        assert "法兰盘" in enhanced


# ── check_clarity fail-open ──────────────────────────────

class TestCheckClarityFailOpen:
    @pytest.mark.asyncio
    async def test_no_api_key_fails_open(self):
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value=""):
            result = await check_clarity("做一个笔筒")
        assert result["is_clear"] is True
        assert result["enhanced_request"] == "做一个笔筒"

    @pytest.mark.asyncio
    async def test_unknown_preset_fails_open(self):
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value="sk-test"):
            result = await check_clarity("test", preset_name="nonexistent")
        assert result["is_clear"] is True


# ── check_clarity 正常流程 ───────────────────────────────

class TestCheckClarityFlow:
    @pytest.mark.asyncio
    async def test_clear_request_returns_enhanced(self):
        mock_llm_response = {
            "is_clear": True,
            "extracted": {"geometry_type": "圆柱体", "dimensions": "外径60mm 壁厚3mm 高100mm"},
            "missing": [],
            "conflicts": [],
            "clarification_question": "",
        }
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value="sk-test"), \
             patch("vermes_cli.mfgcad.clarify._call_llm_for_clarify", new=AsyncMock(return_value=mock_llm_response)):
            result = await check_clarity(
                "做一个笔筒：圆柱体外径60mm壁厚3mm高100mm",
                preset_name="mechanical_part",
            )
        assert result["is_clear"] is True
        assert result["preset"] == "mechanical_part"
        assert "enhanced_request" in result

    @pytest.mark.asyncio
    async def test_ambiguous_request_returns_question(self):
        mock_llm_response = {
            "is_clear": False,
            "extracted": {"geometry_type": "圆柱体"},
            "missing": [{"name": "dimensions", "label": "关键尺寸", "reason": "说了圆柱体但未给高度和壁厚"}],
            "conflicts": [],
            "clarification_question": "请提供圆柱体的高度和壁厚（mm）。",
        }
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value="sk-test"), \
             patch("vermes_cli.mfgcad.clarify._call_llm_for_clarify", new=AsyncMock(return_value=mock_llm_response)):
            result = await check_clarity("做一个圆柱体笔筒", preset_name="mechanical_part")
        assert result["is_clear"] is False
        assert len(result["missing"]) > 0
        assert result["clarification_question"] != ""

    @pytest.mark.asyncio
    async def test_conflict_detected(self):
        mock_llm_response = {
            "is_clear": False,
            "extracted": {"geometry_type": "圆柱体", "dimensions": "外径4mm 壁厚3mm"},
            "missing": [],
            "conflicts": [{"items": ["壁厚3mm", "外径4mm"], "reason": "壁厚超过外径一半，几何不可行"}],
            "clarification_question": "壁厚3mm超过了外径4mm的一半，请减小壁厚或增大外径。",
        }
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value="sk-test"), \
             patch("vermes_cli.mfgcad.clarify._call_llm_for_clarify", new=AsyncMock(return_value=mock_llm_response)):
            result = await check_clarity("圆柱体外径4mm壁厚3mm", preset_name="mechanical_part")
        assert result["is_clear"] is False
        assert len(result["conflicts"]) > 0

    @pytest.mark.asyncio
    async def test_llm_exception_fails_open(self):
        with patch("vermes_cli.mfgcad.clarify._resolve_api_key", return_value="sk-test"), \
             patch("vermes_cli.mfgcad.clarify._call_llm_for_clarify", new=AsyncMock(side_effect=Exception("network"))):
            result = await check_clarity("做一个笔筒", preset_name="mechanical_part")
        assert result["is_clear"] is True  # fail-open
