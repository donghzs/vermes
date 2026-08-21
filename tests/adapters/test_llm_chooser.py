"""默认 llm_chooser 单元测试：复用 agent.auxiliary_client.call_llm，失败/未配置降级 None。

沙箱可跑：mock call_llm，不真实调 LLM、不需 API key。
"""

from __future__ import annotations

from vermes_cli.adapters.discovery import ToolSummary
from vermes_cli.adapters.llm_chooser import default_llm_chooser


def _tools():
    return [
        ToolSummary("freecad_part_fillet_3d", "Apply a 3D fillet", ["part", "fillet-3d"], "freecad_adapter"),
        ToolSummary("freecad_part_box", "Add a box", ["part", "add-box"], "freecad_adapter"),
    ]


def _fake_resp(content):
    from types import SimpleNamespace

    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_chooser_returns_matching_tool(monkeypatch):
    """LLM 回精确工具名 → 返回该名。"""
    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        lambda **kw: _fake_resp("freecad_part_fillet_3d"),
    )
    assert default_llm_chooser(_tools(), "apply a 3d fillet") == "freecad_part_fillet_3d"


def test_chooser_substring_match_with_noise(monkeypatch):
    """LLM 输出带废话/引号 → 子串匹配出候选名。"""
    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        lambda **kw: _fake_resp('工具名是 "freecad_part_fillet_3d"。'),
    )
    assert default_llm_chooser(_tools(), "fillet") == "freecad_part_fillet_3d"


def test_chooser_returns_none_when_llm_unconfigured(monkeypatch):
    """LLM 未配置（call_llm 抛 RuntimeError）→ 返回 None（降级启发式）。"""

    def _boom(**kw):
        raise RuntimeError("No provider configured")

    monkeypatch.setattr("agent.auxiliary_client.call_llm", _boom)
    assert default_llm_chooser(_tools(), "fillet") is None


def test_chooser_returns_none_when_name_not_in_candidates(monkeypatch):
    """LLM 编造工具名（不在候选集）→ 返回 None（不静默采用编造名）。"""
    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        lambda **kw: _fake_resp("totally_made_up_tool"),
    )
    assert default_llm_chooser(_tools(), "fillet") is None


def test_chooser_returns_none_on_empty_tools():
    """空候选集 → 直接 None。"""
    assert default_llm_chooser([], "fillet") is None


def test_chooser_prefers_longer_name_on_prefix_collision(monkeypatch):
    """短名前缀误配防护：LLM 回长名时按长度降序匹配，不会误配成短名。"""
    tools = [
        ToolSummary("freecad_part", "Part operations", ["part"], "freecad_adapter"),
        ToolSummary("freecad_part_fillet_3d", "Apply a 3D fillet", ["part", "fillet-3d"], "freecad_adapter"),
    ]
    monkeypatch.setattr(
        "agent.auxiliary_client.call_llm",
        lambda **kw: _fake_resp("freecad_part_fillet_3d"),
    )
    assert default_llm_chooser(tools, "fillet") == "freecad_part_fillet_3d"
