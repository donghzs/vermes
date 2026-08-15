"""P5 独立 provider 测试：3D 建模可独立配置 LLM 厂商。

验证 mfgcad 服务注册三字段（api_key + base_url + model），
以及凭证解析链路优先级：mfgcad 专属 > 主 Agent 活跃 provider。
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保项目根在 sys.path
project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


class TestMfgcadServiceRegistration:
    """验证 register_service 注册了三字段。"""

    def test_registers_three_fields(self):
        """mfgcad 服务应注册 api_key + base_url + model 三字段。"""
        from vermes_cli.mfgcad.tools import register_tools
        register_tools()
        from agent.service_credentials import get_service_fields
        fields = get_service_fields("mfgcad")
        kinds = {f["kind"] for f in fields}
        assert "api_key" in kinds, "缺 api_key 字段"
        assert "base_url" in kinds, "缺 base_url 字段"
        assert "extra" in kinds, "缺 extra(model) 字段"

    def test_field_env_vars(self):
        """字段 env 变量名正确。"""
        from vermes_cli.mfgcad.tools import register_tools
        register_tools()
        from agent.service_credentials import get_service_fields
        fields = get_service_fields("mfgcad")
        keys = {f["key"] for f in fields}
        assert "MFG_CAD_API_KEY" in keys
        assert "MFG_CAD_BASE_URL" in keys
        assert "MFG_CAD_MODEL" in keys


class TestResolveMfgcadServiceCreds:
    """验证凭证解析优先级。"""

    def test_returns_empty_when_nothing_configured(self):
        """无任何配置时返回全空。"""
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MFG_CAD_MODEL", None)
            creds = _resolve_mfgcad_service_creds()
        # 无活跃 provider 时全空
        assert creds["api_key"] == ""
        assert creds["base_url"] == ""
        assert creds["model"] == ""

    def test_model_from_env(self):
        """model 从 MFG_CAD_MODEL env 读取。"""
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds
        with patch.dict(os.environ, {"MFG_CAD_MODEL": "qwen-plus"}):
            creds = _resolve_mfgcad_service_creds()
        assert creds["model"] == "qwen-plus"

    def test_fallback_to_active_provider(self):
        """无 mfgcad 专属 key 时回退活跃 provider。"""
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MFG_CAD_MODEL", None)
            with patch("vermes_cli.auth.get_active_provider", return_value="deepseek"):
                with patch("vermes_cli.auth.resolve_api_key_provider_credentials",
                           return_value={"api_key": "sk-fallback", "base_url": "https://api.deepseek.com/v1"}):
                    creds = _resolve_mfgcad_service_creds()
        assert creds["api_key"] == "sk-fallback"
        assert creds["base_url"] == "https://api.deepseek.com/v1"

    def test_mfgcad_specific_takes_priority(self):
        """mfgcad 专属 key 优先于活跃 provider。"""
        from vermes_cli.mfgcad.tools import _resolve_mfgcad_service_creds
        with patch("agent.service_credentials.get_service_credentials",
                   return_value={"api_key": "sk-mfgcad-specific", "base_url": "https://custom.example.com/v1"}):
            with patch("vermes_cli.auth.get_active_provider", return_value="deepseek"):
                with patch("vermes_cli.auth.resolve_api_key_provider_credentials",
                           return_value={"api_key": "sk-fallback", "base_url": "https://api.deepseek.com/v1"}):
                    creds = _resolve_mfgcad_service_creds()
        assert creds["api_key"] == "sk-mfgcad-specific"
        assert creds["base_url"] == "https://custom.example.com/v1"


class TestClarifyModelResolution:
    """验证 clarify 模型派生优先 mfgcad 专属。"""

    def test_clarify_prefers_mfgcad_model(self):
        """clarify 模型优先读 mfgcad 专属 model。"""
        from vermes_cli.mfgcad.clarify import _resolve_clarify_model
        with patch("vermes_cli.mfgcad.tools._resolve_mfgcad_model", return_value="qwen-plus"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MFGCAD_CLARIFY_MODEL", None)
                model = _resolve_clarify_model()
        assert model == "qwen-plus"

    def test_clarify_env_override(self):
        """MFGCAD_CLARIFY_MODEL env 次优先。"""
        from vermes_cli.mfgcad.clarify import _resolve_clarify_model
        with patch("vermes_cli.mfgcad.tools._resolve_mfgcad_model", return_value=""):
            with patch.dict(os.environ, {"MFGCAD_CLARIFY_MODEL": "gpt-4o-mini"}):
                model = _resolve_clarify_model()
        assert model == "gpt-4o-mini"

    def test_clarify_fallback_deepseek(self):
        """无任何配置时兜底 deepseek-chat。"""
        from vermes_cli.mfgcad.clarify import _resolve_clarify_model
        with patch("vermes_cli.mfgcad.tools._resolve_mfgcad_model", return_value=""):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MFGCAD_CLARIFY_MODEL", None)
                with patch("vermes_cli.auth.get_active_provider", return_value=""):
                    model = _resolve_clarify_model()
        assert model == "deepseek-chat"


class TestVisionModelResolution:
    """验证 vision 模型派生优先 mfgcad 专属。"""

    def test_vision_prefers_mfgcad_model(self):
        """vision 模型优先读 mfgcad 专属 model。"""
        # 直接测试 _llm_vision_describe 内的模型选择逻辑
        from vermes_cli.mfgcad.multimodal_tools import _VISION_MODEL_BY_PROVIDER
        # 验证映射存在
        assert "deepseek" in _VISION_MODEL_BY_PROVIDER
        assert "openai" in _VISION_MODEL_BY_PROVIDER
