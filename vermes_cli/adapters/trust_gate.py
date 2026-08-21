"""L2b 信任闸门（UNIVERSAL_OPERATION_LAYER_DESIGN.md §15.3 / §14.5）。

设计纪律（守「薄」）：
- 只做权限声明 + 执行前闸门，不写垂直逻辑。
- 闸门插在 SoftwareAdapter.invoke() 入口（§15.4），默认 deny-unless-declared。
- cli_native（纯 CLI 透传）= 低权信任：exec_external=true 但 network=false、
  requires_explicit_consent=false → 默认 ALLOW（不阻断现有 273 工具）。
- sdk_bridge（加载外部 SDK / 长驻进程）= 默认 requires_explicit_consent=true → ASK_USER。
"""

from __future__ import annotations

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
        """
        if spec is None:
            return GateResult(DENY, "未声明 PermissionSpec（deny-unless-declared）")

        if spec.requires_explicit_consent:
            return GateResult(ASK_USER, "requires_explicit_consent=true，需用户显式授权", spec)

        # 网络访问必须落在沙箱内，否则拒绝（防止静默外联）
        if spec.network and spec.sandbox == SANDBOX_NONE:
            return GateResult(DENY, "network=true 但 sandbox=none，拒绝未沙箱化的外联", spec)

        return GateResult(ALLOW, permission=spec)
