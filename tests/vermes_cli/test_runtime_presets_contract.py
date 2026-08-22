"""A3 preset 化运行时 — 契约测试（取法 dsh agent.cordis.yml）。

锁定：
- runtime_presets.load_preset 的 fail-open 语义（未知/路径遍历 → None）
- _stamp_preset 只附加软提示字段，不推翻凭证级事实（provider/api_mode/base_url/api_key）
- resolve_runtime_provider(preset=...) 在真实推导路径上正确 stamp preset 字段
"""

import sys
from unittest.mock import patch, MagicMock

import pytest

# 确保主仓在 import 路径最前（避免 editable-install 命中其他位置）
sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from vermes_cli import runtime_presets
from vermes_cli.runtime_provider import _stamp_preset, resolve_runtime_provider


def test_load_preset_builtin_returns_copy():
    spec = runtime_presets.load_preset("scholarforge")
    assert spec is not None
    assert spec["toolset"] == "scholarforge"
    assert spec["context_budget"] == "large"
    # 返回的是副本，修改不影响内置
    spec["toolset"] = "MUTATED"
    assert runtime_presets.load_preset("scholarforge")["toolset"] == "scholarforge"


def test_load_preset_unknown_is_none():
    assert runtime_presets.load_preset("does-not-exist") is None


def test_load_preset_rejects_path_traversal():
    assert runtime_presets.load_preset("../etc/passwd") is None
    assert runtime_presets.load_preset("a/../b") is None
    assert runtime_presets.load_preset(".hidden") is None


def test_register_preset_overrides():
    runtime_presets.register_preset("temp_x", {"toolset": "x", "sandbox": "none"})
    assert runtime_presets.load_preset("temp_x")["toolset"] == "x"
    # 清理
    runtime_presets._REGISTERED_PRESETS.pop("temp_x", None)


def test_stamp_preset_attaches_soft_hints_only():
    resolved = {
        "provider": "openai",
        "api_mode": "chat_completions",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-xxx",
        "source": "explicit",
    }
    preset = {"toolset": "mfgcad", "context_budget": "large", "sandbox": "inherit",
              "model": "gpt-4o"}
    out = _stamp_preset(resolved, preset, "mfgcad")
    # 凭证级事实不被 preset 推翻
    assert out["provider"] == "openai"
    assert out["api_mode"] == "chat_completions"
    assert out["base_url"] == "https://api.openai.com/v1"
    assert out["api_key"] == "sk-xxx"
    # 软提示被附加
    assert out["preset"] == "mfgcad"
    assert out["toolset"] == "mfgcad"
    assert out["context_budget"] == "large"
    assert out["sandbox"] == "inherit"
    assert out["preferred_model"] == "gpt-4o"
    # source 标记拼接
    assert out["source"].startswith("preset:mfgcad|")


def test_stamp_preset_none_is_noop():
    resolved = {"provider": "openai", "api_mode": "chat_completions"}
    assert _stamp_preset(resolved, None, None) is resolved


def test_resolve_with_preset_stamps_field():
    """集成契约：传 preset 时推导结果含 preset 软提示字段。"""
    fake_resolved = {
        "provider": "openai",
        "api_mode": "chat_completions",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "source": "explicit",
        "requested_provider": "openai",
    }
    with patch.object(
        sys.modules["vermes_cli.runtime_provider"],
        "resolve_runtime_provider",
        wraps=resolve_runtime_provider,
    ):
        # 隔离凭证解析链路，直接让底层返回 fake；这里用 patch 内部函数
        with patch("vermes_cli.runtime_provider._resolve_explicit_runtime", return_value=fake_resolved), \
             patch("vermes_cli.runtime_provider.resolve_requested_provider", return_value="openai"), \
             patch("vermes_cli.runtime_provider._get_model_config", return_value={}), \
             patch("vermes_cli.runtime_provider.resolve_provider", return_value="openai"):
            out = resolve_runtime_provider(requested="openai", preset="scholarforge")
    assert out["preset"] == "scholarforge"
    assert out["toolset"] == "scholarforge"
    assert out["api_mode"] == "chat_completions"  # 凭证事实保持


def test_resolve_unknown_preset_falls_back():
    """fail-open：未知 preset 不报错，推导结果不含 preset 字段。"""
    fake_resolved = {
        "provider": "openai",
        "api_mode": "chat_completions",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "source": "explicit",
        "requested_provider": "openai",
    }
    with patch("vermes_cli.runtime_provider._resolve_explicit_runtime", return_value=fake_resolved), \
         patch("vermes_cli.runtime_provider.resolve_requested_provider", return_value="openai"), \
         patch("vermes_cli.runtime_provider._get_model_config", return_value={}), \
         patch("vermes_cli.runtime_provider.resolve_provider", return_value="openai"):
        out = resolve_runtime_provider(requested="openai", preset="nope-unknown")
    assert "preset" not in out
    assert out["api_mode"] == "chat_completions"
