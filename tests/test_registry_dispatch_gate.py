"""A1 阶段 0：dispatch 主执行点统一信任闸门提级 + 命中率计数。

不依赖全量套件：直接实例化 ToolRegistry，注册最小 handler 验证闸门接线与
fail-open / fail-closed 行为。每条用例自带 reset_gate_stats() 隔离。

覆盖：
- 未声明权限的内置工具 → 默认 cli_native ALLOW，fail-open 仍执行（零回归）。
- fail-closed 下 network+sandbox=none → 阻断（DENY），命中规则 network_no_sandbox。
- fail-closed 下 requires_explicit_consent → 阻断（ASK_USER），命中规则 consent_required。
- fail-open 下即便 DENY 仍执行 handler（零回归保证），但命中率计入 deny。
- 未知工具走 P3 分支、不计入 gate 统计。
"""

import json

from tools.registry import ToolRegistry
from vermes_cli.adapters.trust_gate import (
    ALLOW,
    DENY,
    ASK_USER,
    PermissionSpec,
    SANDBOX_NONE,
    get_gate_stats,
    reset_gate_stats,
)


def _make_registry(mode="fail_open"):
    reg = ToolRegistry()
    reg.dispatch_gate_mode = mode
    return reg


def _marker_handler(args, **kwargs):
    return json.dumps({"ok": True, "args": args})


def test_dispatch_default_undeclared_tool_is_allow_and_executes():
    reset_gate_stats()
    reg = _make_registry("fail_open")
    reg.register(name="t_allow", toolset="ts", schema={"name": "t_allow"},
                 handler=_marker_handler)
    out = reg.dispatch("t_allow", {"x": 1})
    assert json.loads(out)["ok"] is True
    stats = get_gate_stats()
    assert stats["decisions"]["allow"] == 1
    assert stats["rules"]["default_allow"] == 1


def test_dispatch_fail_closed_blocks_deny_network_no_sandbox():
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    reg.register(
        name="t_net", toolset="ts", schema={"name": "t_net"},
        handler=_marker_handler,
        permission_spec=PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ),
    )
    out = reg.dispatch("t_net", {})
    blocked = json.loads(out)
    assert blocked["error"] == "permission denied by dispatch gate"
    assert blocked["gate"] == DENY
    stats = get_gate_stats()
    assert stats["decisions"]["deny"] == 1
    assert stats["rules"]["network_no_sandbox"] == 1


def test_dispatch_fail_closed_blocks_ask_user_consent():
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    reg.register(
        name="t_sdk", toolset="ts", schema={"name": "t_sdk"},
        handler=_marker_handler,
        permission_spec=PermissionSpec(
            reads_fs=False, writes_fs=False, network=False,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=True,
        ),
    )
    out = reg.dispatch("t_sdk", {})
    blocked = json.loads(out)
    assert blocked["gate"] == ASK_USER
    stats = get_gate_stats()
    assert stats["rules"]["consent_required"] == 1


def test_dispatch_fail_open_proceeds_even_when_denied():
    reset_gate_stats()
    reg = _make_registry("fail_open")
    reg.register(
        name="t_net2", toolset="ts", schema={"name": "t_net2"},
        handler=_marker_handler,
        permission_spec=PermissionSpec(network=True, sandbox=SANDBOX_NONE),
    )
    out = reg.dispatch("t_net2", {})
    # fail-open：仍执行 handler，返回 marker（零回归保证）
    assert json.loads(out)["ok"] is True
    stats = get_gate_stats()
    assert stats["decisions"]["deny"] == 1  # 记录但不阻断


def test_unknown_tool_still_returns_error_and_is_not_gated():
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    out = reg.dispatch("no_such_tool", {})
    assert "Unknown tool" in json.loads(out)["error"]
    # 未知工具不经过 check，不应计入 gate 统计
    assert get_gate_stats()["total"] == 0


def test_permission_spec_roundtrips_through_register():
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    spec = PermissionSpec(network=True, sandbox=SANDBOX_NONE)
    reg.register(name="t_spec", toolset="ts", schema={"name": "t_spec"},
                 handler=_marker_handler, permission_spec=spec)
    # register 必须原样保存 spec，fail-closed 才据此阻断
    assert reg.get_entry("t_spec").permission_spec is spec
    out = reg.dispatch("t_spec", {})
    assert json.loads(out)["gate"] == DENY
