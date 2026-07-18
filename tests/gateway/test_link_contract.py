"""Phase 3.1 — 链路契约测试 (link contract).

锁定 GatewayRunner 的 mixin 链路 + harness 可达性 + ScholarForge 关键 handler。
目的：在 Phase 3（run.py 拆分 / 去全局态）过程中提供"等价性安全网"——
任何意外改动（删 mixin / 漏打包 harness / 改名关键 handler）都会让本测试红，
与 tests/gateway 5,694 基线 gate 共同构成回归护栏。

运行：pytest tests/gateway/test_link_contract.py -q
"""
import pytest

# ── harness 可达性（P0 漏打包教训：仅 hiddenimports 未进包 → 运行崩溃）──
from harness.constraints import ConstraintReport, run_constraints
from harness.recoverable import RecoverableFeedback, recoverable_tool
from harness.stability import StabilityReport, probe_stability


def test_harness_classes_importable():
    for obj in (RecoverableFeedback, StabilityReport, ConstraintReport,
                recoverable_tool, probe_stability, run_constraints):
        assert obj is not None
    assert callable(recoverable_tool)
    assert callable(probe_stability)
    assert callable(run_constraints)


# ── GatewayRunner 链路契约 ──
import gateway.run as _gw  # noqa: E402  (置于 harness 校验之后)

EXPECTED_DIRECT_BASES = {
    "TelegramTopicsMixin", "VoiceMixin", "GoalMixin", "KanbanMixin",
    "SlashCommandsMixin", "SessionMixin", "AuthMixin", "ConfigLoaderMixin", "WatcherMixin",
}
EXPECTED_MRO_MIXINS = EXPECTED_DIRECT_BASES | {
    "SessionCommandsMixin", "ConfigCommandsMixin", "SystemCommandsMixin", "CapabilityCommandsMixin",
}


def test_gatewayrunner_direct_bases_contract():
    """GatewayRunner 的直接基类集合是链路契约：增删任一 mixin 都须显式评审。"""
    bases = {b.__name__ for b in _gw.GatewayRunner.__bases__}
    assert bases == EXPECTED_DIRECT_BASES, (
        f"GatewayRunner 直接基类契约被破坏（是否误增/误删 mixin？）\n"
        f"  差异: {bases ^ EXPECTED_DIRECT_BASES}"
    )


def test_gatewayrunner_mro_mixins_contract():
    """完整 MRO 须包含全部 13 个关键 mixin（4 个 command mixin 经 SlashCommandsMixin 继承）。"""
    mro = {c.__name__ for c in _gw.GatewayRunner.__mro__}
    missing = EXPECTED_MRO_MIXINS - mro
    assert not missing, f"GatewayRunner MRO 缺失关键 mixin: {missing}"


# ── ScholarForge（首个热插拔模块，契约锁 15 工具）──
def test_scholarforge_registered_in_global_registry():
    import hermes_cli.scholarforge  # 副作用：注册 15 个工具到全局 registry
    from tools.registry import registry

    names = set(registry.get_all_tool_names())
    # 3 个核心 Agent 工具必须存在（Agent 对话内直接可用）
    for critical in ("scholarforge_search", "scholarforge_write", "scholarforge_review"):
        assert critical in names, f"ScholarForge 核心工具缺失: {critical}"
    # 整体健康度：不得因重构丢失大量工具（基线 15）
    sf_count = sum(1 for n in names if n.startswith("scholarforge_"))
    assert sf_count >= 13, f"ScholarForge 注册工具数异常偏低: {sf_count}"
