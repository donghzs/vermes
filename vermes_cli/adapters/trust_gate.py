"""L2b 信任闸门（UNIVERSAL_OPERATION_LAYER_DESIGN.md §15.3 / §14.5）。

设计纪律（守「薄」）：
- 只做权限声明 + 执行前闸门，不写垂直逻辑。
- 闸门插在 SoftwareAdapter.invoke() 入口（§15.4），默认 deny-unless-declared。
- cli_native（纯 CLI 透传）= 低权信任：exec_external=true 但 network=false、
  requires_explicit_consent=false → 默认 ALLOW（不阻断现有 273 工具）。
- sdk_bridge（加载外部 SDK / 长驻进程）= 默认 requires_explicit_consent=true → ASK_USER。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

# 闸门决策
ALLOW = "allow"
DENY = "deny"
ASK_USER = "ask_user"

# sandbox 取值
SANDBOX_NONE = "none"
SANDBOX_CONTAINER = "container"
SANDBOX_SECCOMP = "seccomp"


@dataclass
class PermissionSpec:
    """工具的权限声明。发现时附带；未声明 = deny-unless-declared。"""

    reads_fs: bool = False
    writes_fs: bool = False
    network: bool = False
    exec_external: bool = False
    sandbox: str = SANDBOX_NONE  # none | container | seccomp
    requires_explicit_consent: bool = False


@dataclass
class GateResult:
    """闸门判定结果。"""

    decision: str  # ALLOW | DENY | ASK_USER
    reason: str = ""
    permission: Optional[PermissionSpec] = None
    rule: str = ""  # 命中规则名（undeclared_deny / consent_required / network_no_sandbox / default_allow）


# ---------------------------------------------------------------------------
# 闸门命中率计数（fail-open 观测期数据，驱动 fail-closed 切换决策）
# ---------------------------------------------------------------------------
_gate_stats_lock = threading.Lock()
_gate_stats: dict = {
    "total": 0,
    "decisions": {"allow": 0, "deny": 0, "ask_user": 0},
    "rules": {},  # rule -> count
}


def record_gate_hit(decision: str, rule: str) -> None:
    """线程安全的闸门命中计数。decision ∈ {allow,deny,ask_user}；rule 为命中规则名。"""
    with _gate_stats_lock:
        _gate_stats["total"] += 1
        _gate_stats["decisions"][decision] = _gate_stats["decisions"].get(decision, 0) + 1
        _gate_stats["rules"][rule] = _gate_stats["rules"].get(rule, 0) + 1


def get_gate_stats() -> dict:
    """返回观测期累计命中率。fail-open 结束后据此决定是否切 fail-closed。"""
    with _gate_stats_lock:
        return {
            "total": _gate_stats["total"],
            "decisions": dict(_gate_stats["decisions"]),
            "rules": dict(_gate_stats["rules"]),
        }


def reset_gate_stats() -> None:
    """清空计数（测试隔离 / 新观测期起点）。"""
    with _gate_stats_lock:
        _gate_stats["total"] = 0
        _gate_stats["decisions"] = {"allow": 0, "deny": 0, "ask_user": 0}
        _gate_stats["rules"] = {}


class TrustGate:
    """执行前权限闸门。"""

    @staticmethod
    def default_for_mechanism(mechanism: str) -> PermissionSpec:
        """按 operation_mechanism 给默认信任分级（§15.3）。"""
        if mechanism == "cli_native":
            # 低权信任：可透传执行 CLI、可在工作目录读写文件，但无网络、无需显式授权
            return PermissionSpec(
                reads_fs=True,
                writes_fs=True,
                network=False,
                exec_external=True,
                sandbox=SANDBOX_NONE,
                requires_explicit_consent=False,
            )
        # sdk_bridge / gui_automation / official_api 等：默认需显式授权
        return PermissionSpec(
            reads_fs=False,
            writes_fs=False,
            network=False,
            exec_external=False,
            sandbox=SANDBOX_NONE,
            requires_explicit_consent=True,
        )

    @staticmethod
    def check(spec: Optional[PermissionSpec], ctx: Optional[dict] = None) -> GateResult:
        """执行前判定：ALLOW / DENY / ASK_USER。

        默认 deny-unless-declared：spec 为 None（未声明）直接 DENY。
        每次判定都经 record_gate_hit 累计命中率（fail-open 观测数据）。
        """
        if spec is None:
            res = GateResult(DENY, "未声明 PermissionSpec（deny-unless-declared）")
            res.rule = "undeclared_deny"
            record_gate_hit(DENY, "undeclared_deny")
            return res

        if spec.requires_explicit_consent:
            res = GateResult(ASK_USER, "requires_explicit_consent=true，需用户显式授权", spec)
            res.rule = "consent_required"
            record_gate_hit(ASK_USER, "consent_required")
            return res

        # 网络访问必须落在沙箱内，否则拒绝（防止静默外联）
        if spec.network and spec.sandbox == SANDBOX_NONE:
            res = GateResult(DENY, "network=true 但 sandbox=none，拒绝未沙箱化的外联", spec)
            res.rule = "network_no_sandbox"
            record_gate_hit(DENY, "network_no_sandbox")
            return res

        res = GateResult(ALLOW, permission=spec)
        res.rule = "default_allow"
        record_gate_hit(ALLOW, "default_allow")
        return res
