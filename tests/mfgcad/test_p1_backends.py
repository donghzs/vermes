"""P1 多后端引擎抽象层测试。"""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.engine_backends import (
    EngineBackend,
    EngineResult,
    MACBackend,
    TrellisBackend,
    register_backend,
    get_backend,
    list_backends,
    resolve_backend,
    _parse_json_line,
)


# ── 后端注册与路由 ───────────────────────────────────────

class TestBackendRegistry:
    def test_mac_registered(self):
        assert "mac" in list_backends()

    def test_trellis_registered(self):
        assert "trellis" in list_backends()

    def test_get_backend(self):
        b = get_backend("mac")
        assert isinstance(b, MACBackend)

    def test_get_unknown_returns_none(self):
        assert get_backend("nonexistent") is None

    def test_register_custom_backend(self):
        class FakeBackend(EngineBackend):
            @property
            def name(self): return "fake"
            @property
            def output_formats(self): return ["txt"]
            async def generate(self, **kw): return EngineResult(ok=True)

        register_backend("fake", FakeBackend())
        assert "fake" in list_backends()
        b = resolve_backend({"engine": "fake"})
        assert b.name == "fake"

    def test_resolve_default_mac(self):
        b = resolve_backend(None)
        assert b.name == "mac"

    def test_resolve_by_preset_engine(self):
        with patch.object(TrellisBackend, "is_available", return_value=True):
            b = resolve_backend({"engine": "trellis"})
        assert b.name == "trellis"

    def test_resolve_trellis_unavailable_raises(self):
        with patch.object(TrellisBackend, "is_available", return_value=False):
            with pytest.raises(RuntimeError, match="未就绪"):
                resolve_backend({"engine": "trellis"})

    def test_resolve_unknown_raises(self):
        with pytest.raises(RuntimeError, match="未知引擎"):
            resolve_backend({"engine": "nonexistent"})


# ── EngineResult ─────────────────────────────────────────

class TestEngineResult:
    def test_defaults(self):
        r = EngineResult(ok=True)
        assert r.ok is True
        assert r.files == {}
        assert r.volume_mm3 is None
        assert r.qa == {}

    def test_to_dict(self):
        r = EngineResult(ok=True, files={"step": "/x.step"}, volume_mm3=100.0)
        d = r.to_dict()
        assert d["ok"] is True
        assert d["files"]["step"] == "/x.step"
        assert d["volume_mm3"] == 100.0


# ── MAC 后端 ─────────────────────────────────────────────

class TestMACBackend:
    def test_name(self):
        assert MACBackend().name == "mac"

    def test_output_formats(self):
        assert "step" in MACBackend().output_formats
        assert "stl" in MACBackend().output_formats

    def test_is_available_checks_run_mac(self):
        b = MACBackend()
        # 引擎目录不存在 → unavailable
        with patch.dict(os.environ, {"MFG_CAD_ENGINE_DIR": "/nonexistent/path"}):
            assert b.is_available() is False


# ── TRELLIS 后端 ─────────────────────────────────────────

class TestTrellisBackend:
    def test_name(self):
        assert TrellisBackend().name == "trellis"

    def test_output_formats(self):
        assert "glb" in TrellisBackend().output_formats

    def test_is_available_no_engine_no_key(self):
        b = TrellisBackend()
        with patch.dict(os.environ, {}, clear=True):
            # 无引擎目录 + 无 cloud key → unavailable
            with patch.object(b, "_engine_dir", return_value=Path("/nonexistent")):
                with patch.dict(os.environ, {"TRELLIS_CLOUD_API_KEY": ""}):
                    assert b.is_available() is False

    def test_is_available_with_cloud_key(self):
        b = TrellisBackend()
        with patch.object(b, "_engine_dir", return_value=Path("/nonexistent")):
            with patch.dict(os.environ, {"TRELLIS_CLOUD_API_KEY": "sk-test"}):
                assert b.is_available() is True

    def test_detect_mode_cloud(self):
        b = TrellisBackend()
        with patch.object(b, "_engine_dir", return_value=Path("/nonexistent")):
            with patch.dict(os.environ, {"TRELLIS_CLOUD_API_KEY": "sk-test"}):
                assert b._detect_mode() == "cloud_api"

    def test_detect_mode_unavailable(self):
        b = TrellisBackend()
        with patch.object(b, "_engine_dir", return_value=Path("/nonexistent")):
            with patch.dict(os.environ, {}, clear=True):
                assert b._detect_mode() == "unavailable"

    @pytest.mark.asyncio
    async def test_generate_unavailable_returns_error(self):
        b = TrellisBackend()
        with patch.object(b, "_detect_mode", return_value="unavailable"):
            result = await b.generate("test", "/tmp/out")
        assert result.ok is False
        assert result.error_type == "engine_not_ready"

    @pytest.mark.asyncio
    async def test_generate_cloud_api_success(self):
        b = TrellisBackend()
        # mock _detect_mode → cloud_api
        with patch.object(b, "_detect_mode", return_value="cloud_api"):
            # mock httpx calls
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"glb_url": "https://example.com/model.glb"}
            mock_resp.raise_for_status = MagicMock()

            mock_glb_resp = MagicMock()
            mock_glb_resp.content = b"FAKE_GLB_DATA"
            mock_glb_resp.raise_for_status = MagicMock()

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_resp)
                mock_client.get = AsyncMock(return_value=mock_glb_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                import tempfile
                with tempfile.TemporaryDirectory() as tmpdir:
                    result = await b.generate(
                        "一只赛博朋克猫", tmpdir,
                        env={"TRELLIS_CLOUD_API_KEY": "sk-test"},
                    )

        assert result.ok is True
        assert result.files.get("glb") is not None
        assert result.files["glb"].endswith(".glb")


# ── JSON 解析工具 ────────────────────────────────────────

class TestParseJsonLine:
    def test_normal_json(self):
        assert _parse_json_line('{"ok": true}') == {"ok": True}

    def test_json_with_noise(self):
        stdout = "INFO: starting\nprogress: 50%\n{\"ok\": true, \"step_path\": \"/x.step\"}"
        result = _parse_json_line(stdout)
        assert result == {"ok": True, "step_path": "/x.step"}

    def test_empty_stdout(self):
        assert _parse_json_line("") is None
        assert _parse_json_line(None) is None

    def test_no_json(self):
        assert _parse_json_line("just text\nno json here") is None
