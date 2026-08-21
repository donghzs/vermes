"""L2a/L2b 单元测试（沙箱可跑，无 LLM / 不需目标软件安装）。

覆盖：
- BackendLocator 两层发现（CLI 二进制 + 后端；macOS 双候选 + 环境变量兜底）
- route_toolset 纯索引粗筛（含中文意图跨语言桥接）
- select_tool 阈值降级 NEEDS_CLARIFY（消化 argmax 无门槛反模式）
- TrustGate 三态（ALLOW / DENY / ASK_USER，默认 deny-unless-declared）
- SoftwareAdapter.invoke() 注入后端路径环境变量 + 闸门拦截
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import mock

from vermes_cli.adapters.discovery import (
    BackendLocator,
    CapabilityIndex,
    CLI_NATIVE,
    SDK_BRIDGE,
    ToolSummary,
    select_tool,
    NEEDS_CLARIFY,
    route_toolset,
)
from vermes_cli.adapters.discovery_registry import CAPABILITY_REGISTRY
from vermes_cli.adapters.software_adapter import (
    CLITool,
    SoftwareAdapter,
    SoftwareAdapterSpec,
)
from vermes_cli.adapters.trust_gate import (
    ALLOW,
    ASK_USER,
    DENY,
    GateResult,
    PermissionSpec,
    TrustGate,
)


# ---------------------------------------------------------------------------
# L2a: BackendLocator 两层发现
# ---------------------------------------------------------------------------

def test_backend_locator_cli_and_backend_via_path(tmp_path, monkeypatch):
    """Layer1 CLI 二进制 + Layer2 后端都在 PATH 时可解析。"""
    monkeypatch.setenv("PATH", str(tmp_path))
    cli = tmp_path / "cli-anything-freecad"
    backend = tmp_path / "freecadcmd"
    cli.write_text("#!/bin/sh\n")
    backend.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    backend.chmod(0o755)
    target = BackendLocator().locate("freecad", "cli-anything-freecad")
    assert target.cli_resolved == str(cli)
    assert target.backend_resolved == str(backend)
    assert target.env_var == "FREECAD_PATH"
    assert target.env_value == str(backend)


def test_backend_locator_env_var_override(tmp_path, monkeypatch):
    """FREECAD_PATH 环境变量兜底：即使 PATH 无后端也能定位（env 路径须真实存在）。"""
    env_path = tmp_path / "Resources" / "bin" / "freecadcmd"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("")
    monkeypatch.setenv("FREECAD_PATH", str(env_path))
    fake_cli = "/usr/local/bin/cli-anything-freecad"
    monkeypatch.setattr(shutil, "which", lambda x: fake_cli)
    target = BackendLocator().locate("freecad", "cli-anything-freecad")
    assert target.backend_resolved == str(env_path)
    assert target.env_value == str(env_path)


def test_backend_locator_macos_dual_candidate(monkeypatch):
    """macOS 双候选路径兜底：CLI-Anything 写死的 MacOS 路径不存在时回退 Resources/bin。"""
    monkeypatch.delenv("FREECAD_PATH", raising=False)
    real = "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == real)
    monkeypatch.setattr(shutil, "which", lambda x: None)
    target = BackendLocator().locate("freecad", "cli-anything-freecad")
    assert target.backend_resolved == real
    assert target.env_value == real


def test_backend_locator_blender(tmp_path, monkeypatch):
    """Blender 后端（第二域）两层发现。"""
    monkeypatch.setenv("PATH", str(tmp_path))
    cli = tmp_path / "cli-anything-blender"
    backend = tmp_path / "blender"
    cli.write_text("#!/bin/sh\n")
    backend.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    backend.chmod(0o755)
    target = BackendLocator().locate("blender", "cli-anything-blender")
    assert target.backend_resolved == str(backend)
    assert target.env_var == "BLENDER_PATH"


# ---------------------------------------------------------------------------
# L2a: route_toolset 纯索引粗筛
# ---------------------------------------------------------------------------

def _sample_index() -> list[CapabilityIndex]:
    return [
        CapabilityIndex(
            toolset="freecad_adapter",
            domain="3d",
            operation_mechanism=CLI_NATIVE,
            intent_keywords=["3d", "freecad", "cad", "fillet", "part", "draft", "export", "step"],
            tools=[
                ToolSummary("freecad_part_fillet_3d", "Apply a 3D fillet", ["part", "fillet-3d"], "freecad_adapter", CLI_NATIVE, ["part", "fillet", "3d"]),
                ToolSummary("freecad_export_render", "Export a STEP file", ["export", "render"], "freecad_adapter", CLI_NATIVE, ["export", "step", "render"]),
            ],
        ),
        CapabilityIndex(
            toolset="blender_adapter",
            domain="3d",
            operation_mechanism=CLI_NATIVE,
            intent_keywords=["3d", "blender", "render", "mesh", "animation"],
            tools=[ToolSummary("blender_render", "Render scene", ["render"], "blender_adapter", CLI_NATIVE, ["render", "mesh"])],
        ),
        CapabilityIndex(
            toolset="office_adapter",
            domain="office",
            operation_mechanism=CLI_NATIVE,
            intent_keywords=["office", "word", "doc"],
            tools=[ToolSummary("office_export", "Export doc", ["export"], "office_adapter", CLI_NATIVE, ["export", "doc"])],
        ),
    ]


def test_route_toolset_english_intent():
    """英文意图 'fillet a box' 粗筛到 freecad_adapter。"""
    refs = route_toolset("apply a 3d fillet to the box", _sample_index())
    assert refs
    assert refs[0].toolset == "freecad_adapter"


def test_route_toolset_chinese_intent_cross_language():
    """中文意图 '给这个盒子倒角 2mm' 经双语桥接也能粗筛到 freecad_adapter。"""
    refs = route_toolset("给这个盒子倒角 2mm", _sample_index())
    assert refs
    assert refs[0].toolset == "freecad_adapter"


def test_route_toolset_unrelated_intent_empty():
    """无关意图（不匹配任何 domain）返回空候选。"""
    refs = route_toolset("今天天气真好", _sample_index())
    assert refs == []


# ---------------------------------------------------------------------------
# L2a: select_tool 阈值降级
# ---------------------------------------------------------------------------

def test_select_tool_heuristic_above_threshold():
    """启发式选出 fillet 工具且相关度达标。"""
    tools = _sample_index()[0].tools
    choice = select_tool(tools, "apply a 3d fillet to the box")
    assert choice.decision == "allow_tool"
    assert choice.tool.name == "freecad_part_fillet_3d"
    assert choice.score >= 0.2


def test_select_tool_below_threshold_needs_clarify():
    """不相关意图：最高相关度低于阈值 → NEEDS_CLARIFY（不静默选噪声）。"""
    tools = _sample_index()[0].tools
    choice = select_tool(tools, "translate this document to french")
    assert choice.decision == NEEDS_CLARIFY
    assert choice.tool is None


def test_select_tool_llm_chooser_below_threshold_needs_clarify():
    """LLM 选中但相关度不达标 → 仍 NEEDS_CLARIFY（消化 argmax 无门槛反模式）。"""
    tools = _sample_index()[0].tools
    choice = select_tool(
        tools, "translate this document", llm_chooser=lambda ts, intent, ctx: "freecad_part_fillet_3d"
    )
    assert choice.decision == NEEDS_CLARIFY


def test_select_tool_llm_chooser_above_threshold():
    """LLM 选中且相关度达标 → 放行。"""
    tools = _sample_index()[0].tools
    choice = select_tool(
        tools, "apply a 3d fillet", llm_chooser=lambda ts, intent, ctx: "freecad_part_fillet_3d"
    )
    assert choice.decision == "allow_tool"
    assert choice.tool.name == "freecad_part_fillet_3d"


def test_select_tool_llm_chooser_none_falls_back_to_heuristic():
    """llm_chooser 返回 None（LLM 不可用/未配置）→ 降级启发式 argmax，而非 NEEDS_CLARIFY。"""
    tools = _sample_index()[0].tools
    choice = select_tool(
        tools, "apply a 3d fillet", llm_chooser=lambda ts, intent, ctx: None
    )
    assert choice.decision == "allow_tool"
    assert choice.tool.name == "freecad_part_fillet_3d"


def test_select_tool_empty():
    """空候选工具集 → NEEDS_CLARIFY。"""
    choice = select_tool([], "anything")
    assert choice.decision == NEEDS_CLARIFY


# ---------------------------------------------------------------------------
# L2b: TrustGate 三态
# ---------------------------------------------------------------------------

def test_trust_gate_cli_native_default_allow():
    """cli_native 默认信任分级 → ALLOW（不阻断 273 工具）。"""
    spec = TrustGate.default_for_mechanism(CLI_NATIVE)
    assert spec.requires_explicit_consent is False
    assert spec.network is False
    assert TrustGate.check(spec).decision == ALLOW


def test_trust_gate_sdk_bridge_default_ask_user():
    """sdk_bridge 默认需显式授权 → ASK_USER。"""
    spec = TrustGate.default_for_mechanism(SDK_BRIDGE)
    assert spec.requires_explicit_consent is True
    res = TrustGate.check(spec)
    assert res.decision == ASK_USER


def test_trust_gate_undeclared_denied():
    """未声明权限（deny-unless-declared）→ DENY。"""
    res = TrustGate.check(None)
    assert res.decision == DENY
    assert "deny" in res.reason.lower()


def test_trust_gate_network_without_sandbox_denied():
    """network=true 但 sandbox=none → DENY（防静默外联）。"""
    spec = PermissionSpec(network=True, sandbox="none")
    res = TrustGate.check(spec)
    assert res.decision == DENY


def test_trust_gate_network_in_sandbox_allowed():
    """network=true 且 sandbox=container → ALLOW。"""
    spec = PermissionSpec(network=True, sandbox="container")
    res = TrustGate.check(spec)
    assert res.decision == ALLOW


# ---------------------------------------------------------------------------
# 集成：SoftwareAdapter.invoke() 闸门 + 后端环境变量注入
# ---------------------------------------------------------------------------

def test_invoke_cli_native_runs_and_injects_backend_env(monkeypatch):
    """cli_native：TrustGate ALLOW + 注入 FREECAD_PATH 到 subprocess env。"""
    spec = SoftwareAdapterSpec(
        domain="3d", software="freecad", cli_bin="cli-anything-freecad",
        backend="freecad", operation_mechanism=CLI_NATIVE,
    )
    adapter = SoftwareAdapter(spec)
    adapter._backend = type("_B", (), {
        "env_var": "FREECAD_PATH",
        "env_value": "/opt/freecad/Resources/bin/freecadcmd",
    })()

    tool = CLITool("freecad_part_fillet_3d", ["part", "fillet-3d"], {}, "desc", "freecad_adapter")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env", {})
        class _P:
            stdout = '{"ok": true}'
            returncode = 0
        return _P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = adapter.invoke(tool, {"radius": 2.0})
    assert result == {"ok": True}
    assert captured["env"].get("FREECAD_PATH") == "/opt/freecad/Resources/bin/freecadcmd"
    assert captured["cmd"][0] == "cli-anything-freecad"


def test_invoke_sdk_bridge_blocked_by_gate(monkeypatch):
    """sdk_bridge：TrustGate ASK_USER → 不执行 subprocess，返回结构化结果。"""
    spec = SoftwareAdapterSpec(
        domain="3d", software="blender", cli_bin="cli-anything-blender",
        backend="blender", operation_mechanism=SDK_BRIDGE,
    )
    adapter = SoftwareAdapter(spec)
    tool = CLITool("blender_render", ["render"], {}, "desc", "blender_adapter")
    ran = {"called": False}

    def fake_run(cmd, **kwargs):
        ran["called"] = True
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = adapter.invoke(tool, {})
    assert result["gate"] == ASK_USER
    assert ran["called"] is False


def test_register_builds_capability_index():
    """register() 顺带把 CapabilityIndex 写入进程内 registry。"""
    CAPABILITY_REGISTRY.clear()
    spec = SoftwareAdapterSpec(
        domain="3d", software="freecad", cli_bin="cli-anything-freecad",
        backend="freecad", operation_mechanism=CLI_NATIVE,
    )
    adapter = SoftwareAdapter(spec)
    tools = [
        CLITool("freecad_part_fillet_3d", ["part", "fillet-3d"], {}, "Apply a 3D fillet", "freecad_adapter", CLI_NATIVE, ["part", "fillet"]),
    ]
    # register 内部会 import tools.registry；沙箱无该模块，用 fake
    fake_registry = mock.MagicMock()
    fake_registry.register.return_value = None
    with mock.patch.dict("sys.modules", {"tools.registry": mock.MagicMock(registry=fake_registry)}):
        n = adapter.register(tools)
    assert n == 1
    cap = CAPABILITY_REGISTRY.get("freecad_adapter")
    assert cap is not None
    assert cap.domain == "3d"
    assert cap.tools[0].name == "freecad_part_fillet_3d"
    CAPABILITY_REGISTRY.clear()


def test_ctx_threads_from_dispatch_to_invoke(monkeypatch):
    """L2 最后一公里：model_tools 注入的 ctx 经 registry.dispatch → handler → invoke → TrustGate.check。"""
    from tools.registry import registry

    CAPABILITY_REGISTRY.clear()
    spec = SoftwareAdapterSpec(
        domain="3d", software="freecad", cli_bin="cli-anything-freecad",
        backend="freecad", operation_mechanism=CLI_NATIVE,
    )
    adapter = SoftwareAdapter(spec)
    adapter._backend = type("_B", (), {
        "env_var": "FREECAD_PATH",
        "env_value": "/opt/freecad/Resources/bin/freecadcmd",
    })()
    tool = CLITool("freecad_part_fillet_3d", ["part", "fillet-3d"], {}, "desc", "freecad_adapter")

    seen_ctx = {}

    def fake_check(spec, ctx=None):
        seen_ctx["ctx"] = ctx
        return GateResult(ALLOW, reason="allow")

    monkeypatch.setattr(TrustGate, "check", staticmethod(fake_check))

    def fake_run(cmd, **kwargs):
        class _P:
            stdout = '{"ok": true}'
            returncode = 0
        return _P()

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert adapter.register([tool]) == 1
    ctx = {"session_id": "sess-1", "task_id": "t-1", "enabled_tools": ["freecad_part_fillet_3d"]}
    out = registry.dispatch("freecad_part_fillet_3d", {}, ctx=ctx)
    assert json.loads(out) == {"ok": True}
    assert seen_ctx["ctx"] == ctx

    registry.deregister("freecad_part_fillet_3d")
    CAPABILITY_REGISTRY.clear()
