"""A1 阶段 0：dispatch 主执行点统一信任闸门提级 + 命中率计数。

不依赖全量套件：直接实例化 ToolRegistry，注册最小 handler 验证闸门接线与
fail-open / fail-closed 行为。每条用例自带 reset_gate_stats() 隔离。

覆盖：
- 未声明权限的内置工具 → 默认 cli_native ALLOW，fail-open 仍执行（零回归）。
- fail-closed 下 network+sandbox=none → 阻断（DENY），命中规则 network_no_sandbox。
- fail-closed 下 requires_explicit_consent → 阻断（ASK_USER），命中规则 consent_required。
- fail-open 下即便 DENY 仍执行 handler（零回归保证），但命中率计入 deny。
- 未知工具走 P3 分支、不计入 gate 统计。

A1.1 追加：
- 闸门模块整体不可用时 dispatch 仍执行（fail-open 不被击穿）—— 回归 registry.py
  曾在 dispatch 顶部裸 import trust_gate.ALLOW，降级逻辑生效前就抛 ImportError。
- _GATE_ALLOW 字面量与 trust_gate.ALLOW 不漂移。
- 命中转发 agent.metrics 真实递增（防「except 吞掉 → 死指标」重演）。
- 命中率跨进程重启累计（磁盘快照合并）。
- record_count 符号存在，memory_fabric 的 memory_capacity_degraded 死指标已复活。
"""

import json
import os
import sys
import tempfile

# 持久化隔离：必须在任何落盘发生前设好，且**不要**用 monkeypatch 还原——
# atexit flush 在所有 fixture 拆除之后才跑，届时若环境变量已还原，
# 测试数据就会被写进用户真实的 ~/.vermes/gate_stats.json。
os.environ["VERMES_GATE_STATS_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="vermes_gate_stats_test_"), "gate_stats.json"
)

from agent import metrics as agent_metrics  # noqa: E402
from tools.registry import ToolRegistry  # noqa: E402
import vermes_cli.adapters.trust_gate as tg  # noqa: E402
from vermes_cli.adapters.trust_gate import (  # noqa: E402
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


# ---------------------------------------------------------------------------
# A1.1：fail-open 不可被击穿 + 观测数据真的可取
# ---------------------------------------------------------------------------


def test_gate_allow_literal_does_not_drift_from_trust_gate():
    """registry 用字面量 "allow" 比对决策，必须与 trust_gate.ALLOW 保持一致。

    若哪天 trust_gate 改了 ALLOW 的取值，本断言先红，而不是让闸门静默失效
    （decision 永不等于字面量 → 所有工具在 fail_closed 下被全量误杀）。
    """
    assert ToolRegistry._GATE_ALLOW == ALLOW


def test_dispatch_survives_trust_gate_module_unavailable(monkeypatch):
    """R5 回归：闸门模块整体不可用时，dispatch 必须降级放行而非抛异常。

    这条测试在修复前会失败——彼时 dispatch 顶部有一句裸的
    `from vermes_cli.adapters.trust_gate import ALLOW`，
    在 _evaluate_dispatch_gate 的 fail-open 降级生效**之前**就抛 ImportError，
    把 273 个工具全部打死。即便处于 fail_closed 也不能因闸门自身故障阻断业务。
    """
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    reg.register(name="t_broken_gate", toolset="ts",
                 schema={"name": "t_broken_gate"}, handler=_marker_handler)
    # sys.modules 中置 None → `from ... import X` 抛 ImportError
    monkeypatch.setitem(sys.modules, "vermes_cli.adapters.trust_gate", None)
    out = reg.dispatch("t_broken_gate", {"x": 2})
    assert json.loads(out)["ok"] is True, "闸门模块故障不得阻断工具执行"


def test_gate_hit_forwards_to_agent_metrics_and_prometheus():
    """转发必须真实递增。

    `record_gate_hit` 里的转发包在 try/except 中（fail-open 需要），
    但正是这种吞异常写法让 memory_fabric 的 record_count 变成死指标。
    这里断言状态真的动了 + Prometheus 文本真的渲染出来。
    """
    agent_metrics.get_state().reset()
    reset_gate_stats()
    reg = _make_registry("fail_open")
    reg.register(name="t_fwd", toolset="ts", schema={"name": "t_fwd"},
                 handler=_marker_handler)
    reg.dispatch("t_fwd", {})
    state = agent_metrics.get_state()
    assert state.gate_decisions_total["allow"] == 1
    assert state.gate_rule_hits_total["default_allow"] == 1
    text = agent_metrics.render_prometheus()
    assert 'vermes_gate_decisions_total{decision="allow"} 1' in text
    assert 'vermes_gate_rule_hits_total{rule="default_allow"} 1' in text


def test_gate_stats_survive_process_restart_via_disk_snapshot():
    """观测期基线必须跨 app 重启累计，否则无法据数据切 fail-closed。

    模拟：记 3 次 → flush 落盘 → 归零并放开 _loaded（等价于新进程首次读盘）
    → 累计值应从磁盘恢复。
    """
    reset_gate_stats(clear_persisted=True)
    reg = _make_registry("fail_open")
    reg.register(name="t_persist", toolset="ts", schema={"name": "t_persist"},
                 handler=_marker_handler)
    for _ in range(3):
        reg.dispatch("t_persist", {})
    assert get_gate_stats()["total"] == 3
    tg.flush_gate_stats()
    assert os.path.exists(os.environ["VERMES_GATE_STATS_PATH"])

    # 模拟新进程：内存计数归零，且允许重新从磁盘合并
    reset_gate_stats()
    tg._loaded = False
    restored = get_gate_stats()
    assert restored["total"] == 3, "重启后应从磁盘快照恢复累计基线"
    assert restored["decisions"]["allow"] == 3
    assert restored["rules"]["default_allow"] == 3


def test_record_count_revives_memory_fabric_dead_metric():
    """agent/memory_fabric.py:514 一直 `from agent.metrics import record_count`，

    而该函数此前并不存在 → ImportError 被外层 except 吞成 debug 日志 →
    memory_capacity_degraded 自始至终没被记录过。此处锁死符号存在且真的计数。
    """
    from agent.metrics import record_count  # 缺失即 ImportError，测试直接红

    agent_metrics.get_state().reset()
    record_count("memory_capacity_degraded")
    record_count("memory_capacity_degraded")
    assert agent_metrics.get_state().named_counts["memory_capacity_degraded"] == 2
    assert "vermes_count_memory_capacity_degraded 2" in agent_metrics.render_prometheus()


# ---------------------------------------------------------------------------
# Phase 1.4：兜底 spec 可配置 + undeclared_deny 生效
#
# 反向验证设计：test_undeclared_deny_blocks_under_fail_closed 在 Phase 1.4 前
# 必然失败——彼时 _evaluate_dispatch_gate 无条件把 None 替换成 cli_native
# 允许 spec，TrustGate.check 永远收不到 None，rule=undeclared_deny 从不命中，
# deny-unless-declared 被静默掏空。本组用例锁死该缺口已堵上。
# ---------------------------------------------------------------------------


def test_undeclared_deny_blocks_under_fail_closed():
    """Phase 1.4 核心：policy=deny + fail_closed 下，未声明工具被 DENY。

    反向验证：修复前（无 Phase 1.4）此用例必失败——None 被替换成允许 spec，
    gate 返回 ALLOW 而非 DENY。现在必须命中 rule=undeclared_deny 并阻断。
    """
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    reg.set_undeclared_tool_policy("deny")
    reg.register(name="t_undeclared", toolset="ts",
                 schema={"name": "t_undeclared"}, handler=_marker_handler)
    out = reg.dispatch("t_undeclared", {})
    blocked = json.loads(out)
    assert blocked["error"] == "permission denied by dispatch gate"
    assert blocked["gate"] == DENY
    stats = get_gate_stats()
    assert stats["decisions"]["deny"] == 1
    assert stats["rules"]["undeclared_deny"] == 1


def test_undeclared_deny_under_fail_open_still_executes():
    """安全网：policy=deny + fail_open 下仍执行 handler（零回归保证）。

    deny 激活 deny-unless-declared，但 fail_open 仍观测不阻断——
    避免在 1.2/1.4 全就绪前误开 fail_closed 把联网/浏览器/委托工具瞬间打死。
    DENY 必须被记录，但 handler 必须执行。
    """
    reset_gate_stats()
    reg = _make_registry("fail_open")
    reg.set_undeclared_tool_policy("deny")
    reg.register(name="t_undeclared_open", toolset="ts",
                 schema={"name": "t_undeclared_open"}, handler=_marker_handler)
    out = reg.dispatch("t_undeclared_open", {})
    assert json.loads(out)["ok"] is True  # 仍执行
    stats = get_gate_stats()
    assert stats["decisions"]["deny"] == 1  # 记录但不阻断
    assert stats["rules"]["undeclared_deny"] == 1


def test_default_allow_policy_undeclared_is_allow_no_regression():
    """默认 policy=allow：未声明工具仍 ALLOW（零回归）。

    Phase 1.4 不改变默认行为——只有显式 set_undeclared_tool_policy('deny')
    或 VERMES_UNDECLARED_TOOL_POLICY=deny 才激活 deny-unless-declared。
    """
    reset_gate_stats()
    reg = _make_registry("fail_open")  # 默认 policy=allow
    assert reg.undeclared_tool_policy == "allow"
    reg.register(name="t_default", toolset="ts",
                 schema={"name": "t_default"}, handler=_marker_handler)
    out = reg.dispatch("t_default", {})
    assert json.loads(out)["ok"] is True
    stats = get_gate_stats()
    assert stats["decisions"]["allow"] == 1
    assert stats["rules"]["default_allow"] == 1


def test_set_undeclared_tool_policy_rejects_invalid():
    """setter 只接受 allow/deny，防止拼写错误静默退回 allow。"""
    reg = _make_registry("fail_open")
    try:
        reg.set_undeclared_tool_policy("Deny")  # 大小写敏感
        raise AssertionError("应拒绝非法 policy 值")
    except ValueError:
        pass
    assert reg.undeclared_tool_policy == "allow"  # 未被污染


def test_declared_tool_unaffected_by_deny_policy():
    """deny 策略只针对**未声明**工具；已挂 spec 的工具按自身 spec 判定。

    防止 deny 策略误伤已正确声明权限的工具（如纯读工具应 ALLOW）。
    """
    reset_gate_stats()
    reg = _make_registry("fail_closed")
    reg.set_undeclared_tool_policy("deny")
    reg.register(
        name="t_readonly", toolset="ts", schema={"name": "t_readonly"},
        handler=_marker_handler,
        permission_spec=PermissionSpec(
            reads_fs=True, writes_fs=False, network=False,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ),
    )
    out = reg.dispatch("t_readonly", {})
    # 已声明纯读 spec → ALLOW，handler 执行
    assert json.loads(out)["ok"] is True
    stats = get_gate_stats()
    assert stats["decisions"]["allow"] == 1
    assert stats["rules"].get("undeclared_deny", 0) == 0  # 未走 undeclared 分支


# ───────────────────────── Phase 3.4: 三模式统一开关 ─────────────────────────
def test_observe_mode_executes_despite_non_allow():
    """observe 态与 fail_open 同语义：NON-ALLOW 仍记录+告警+执行（不阻断）。

    这是 Phase 3.4 的核心不变量：observe 是「观测增强态」，绝不隐含放行基线，
    但在未达 fail_closed 前不阻断 273 工具。
    """
    reset_gate_stats()
    reg = _make_registry("observe")
    reg.register(
        name="t_net", toolset="ts", schema={"name": "t_net"},
        handler=_marker_handler,
        permission_spec=PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ),
    )
    out = reg.dispatch("t_net", {})  # network=True + sandbox=none → DENY
    # observe 下仍执行（零回归），但决策记为 deny
    assert json.loads(out)["ok"] is True
    stats = get_gate_stats()
    assert stats["decisions"]["deny"] == 1


def test_set_dispatch_gate_mode_roundtrips_all_three():
    """set_dispatch_gate_mode 三态切换 + 非法值拒绝。

    反向控制：非法 mode 抛 ValueError 而非静默退化为 fail_open，
    防止把 fail_closed 误写成 failclosed 后悄悄放开闸门。
    """
    reg = _make_registry("fail_open")
    for m in ("fail_open", "fail_closed", "observe"):
        reg.set_dispatch_gate_mode(m)
        assert reg.dispatch_gate_mode == m
    try:
        reg.set_dispatch_gate_mode("failclosed")  # 拼写错误
        raise AssertionError("应拒绝非法 mode")
    except ValueError:
        pass
    assert reg.dispatch_gate_mode == "observe"  # 未被污染


def test_observe_equals_fail_open_in_dispatch_behavior():
    """observe 与 fail_open 在 dispatch 上的行为完全一致（同一条执行路径）。

    仅语义标签不同（observe 用于观测期告警调优）。反向验证：
    若误把 observe 接进 fail_closed 的阻断分支，本用例会失败。
    """
    reset_gate_stats()
    reg_ob = _make_registry("observe")
    reg_fo = _make_registry("fail_open")
    spec = PermissionSpec(
        reads_fs=False, writes_fs=False, network=True,
        exec_external=False, sandbox=SANDBOX_NONE,
        requires_explicit_consent=False,
    )
    for reg in (reg_ob, reg_fo):
        reg.register(name="t_net", toolset="ts", schema={"name": "t_net"},
                     handler=_marker_handler, permission_spec=spec)
    ob = json.loads(reg_ob.dispatch("t_net", {}))
    fo = json.loads(reg_fo.dispatch("t_net", {}))
    assert ob["ok"] is True and fo["ok"] is True  # 两者都执行

