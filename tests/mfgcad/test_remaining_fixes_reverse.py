"""剩余账项（#7/#9/#10/#12）的反向验证测试（R5）。

断言修复后的「正确行为」，设计上必须能在 pre-fix 代码（commit 117caa4cd）上失败，
否则就是「测试镜像实现，没测到真实 bug」。

覆盖：
- #7 TrellisBackend 不再裸 `python3`，改为 venv/env 解析的解释器
- #9 TRELLIS 云 API 端点缺省时明确报错（不再静默打到 example.com 占位符）
- #10 clarifier 模型按活跃 provider 派生（不再写死 deepseek-chat）
- #12 删除 `_image_to_description` 死桩（恒返 "" 的占位函数）
"""

from pathlib import Path
from unittest.mock import patch

import pytest

import sys as _sys

_sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.engine_backends import TrellisBackend  # noqa: E402


# ── #7 TrellisBackend 解释器解析 ──────────────────────────


class TestTrellisPythonExe:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TRELLIS_ENGINE_PY", "/custom/python")
        monkeypatch.delenv("TRELLIS_ENGINE_DIR", raising=False)
        assert TrellisBackend()._python_exe() == "/custom/python"

    def test_venv_candidate(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRELLIS_ENGINE_PY", raising=False)
        monkeypatch.setenv("TRELLIS_ENGINE_DIR", str(tmp_path))
        venv_py = tmp_path / ".venv" / "bin" / "python"
        venv_py.parent.mkdir(parents=True)
        venv_py.write_text("#!/bin/sh\n")
        assert TrellisBackend()._python_exe() == str(venv_py)

    def test_missing_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TRELLIS_ENGINE_PY", raising=False)
        monkeypatch.setenv("TRELLIS_ENGINE_DIR", str(tmp_path))
        with pytest.raises(RuntimeError):
            TrellisBackend()._python_exe()


# ── #9 云 API 端点配置 ───────────────────────────────────


class _FakeCloudResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"glb_url": None}


class _FakeCloudClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.url = url
        self.json_body = json
        return _FakeCloudResp()

    async def get(self, url, **k):
        return _FakeCloudResp()


@pytest.mark.asyncio
async def test_cloud_config_missing_without_base(monkeypatch):
    """TRELLIS_CLOUD_API_BASE 未设 → 明确报错，而非静默打到占位符 example.com。"""
    monkeypatch.delenv("TRELLIS_CLOUD_API_BASE", raising=False)
    res = await TrellisBackend()._run_cloud("prompt", "/tmp/out", {"TRELLIS_CLOUD_API_KEY": "x"})
    assert res.ok is False
    assert res.error_type == "config_missing"


@pytest.mark.asyncio
async def test_cloud_proceeds_with_base(monkeypatch):
    """BASE 已设 → 真正用该 BASE 发请求（占位符已移除）。"""
    clients = []

    def _maker(*a, **k):
        c = _FakeCloudClient()
        clients.append(c)
        return c

    with patch("httpx.AsyncClient", _maker):
        res = await TrellisBackend()._run_cloud(
            "prompt",
            "/tmp/out",
            {"TRELLIS_CLOUD_API_KEY": "x", "TRELLIS_CLOUD_API_BASE": "https://trellis.acme.test/v1"},
        )
    assert clients and clients[0].url == "https://trellis.acme.test/v1/generate"
    assert res.ok is True


# ── #10 clarifier 模型派生 ────────────────────────────────


def test_clarify_model_provider_derived(monkeypatch):
    """活跃 provider=qwen → 派生 qwen-plus（旧代码写死 deepseek-chat）。"""
    from vermes_cli.mfgcad.clarify import _resolve_clarify_model

    monkeypatch.delenv("MFGCAD_CLARIFY_MODEL", raising=False)
    monkeypatch.setattr("vermes_cli.auth.get_active_provider", lambda: "qwen")
    monkeypatch.setattr(
        "vermes_cli.auth.resolve_api_key_provider_credentials",
        lambda pid: {"provider": "qwen"},
    )
    assert _resolve_clarify_model() == "qwen-plus"


def test_clarify_model_env_override(monkeypatch):
    from vermes_cli.mfgcad.clarify import _resolve_clarify_model

    monkeypatch.setenv("MFGCAD_CLARIFY_MODEL", "claude-3-opus")
    assert _resolve_clarify_model() == "claude-3-opus"


def test_clarify_model_default_when_no_provider(monkeypatch):
    from vermes_cli.mfgcad.clarify import _resolve_clarify_model

    monkeypatch.delenv("MFGCAD_CLARIFY_MODEL", raising=False)
    monkeypatch.setattr("vermes_cli.auth.get_active_provider", lambda: None)
    assert _resolve_clarify_model() == "deepseek-chat"


# ── #12 死桩删除 ─────────────────────────────────────────


def test_image_to_description_stub_removed():
    """`_image_to_description` 是恒返 "" 的死桩，已删除；避免 `description or 死桩` 静默吞描述。"""
    import vermes_cli.mfgcad.multimodal_tools as m

    assert not hasattr(m, "_image_to_description")
