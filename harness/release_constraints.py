"""Harness: pre-release constraints.

把 A0 复验门（2026-07-18 的"5 bug 分类 STALE/REAL"流程）沉淀为
``harness/constraints.py`` 约束集，发布前 ``run_constraints`` 一键审。

Usage:
    python3 -c "
    import asyncio
    from harness.release_constraints import build_release_constraints
    from harness.constraints import run_constraints
    report = asyncio.run(run_constraints(build_release_constraints()))
    print('passed:', report.passed)
    for e in report.errors:
        print('  ERROR:', e.name, '-', e.detail)
    for w in report.warnings:
        print('  WARN:', w.name, '-', w.detail)
    "

或通过 CLI:
    python3 -m harness.release_constraints
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from harness.constraints import Constraint, ConstraintResult, run_constraints

logger = logging.getLogger("harness.release_constraints")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ToolRegistrationConstraint(Constraint):
    """验证关键工具已注册到 registry。"""

    name = "tool_registration"
    severity = "error"

    REQUIRED_TOOLS = [
        "vision_analyze",
        "video_analyze",
        "web_search",
        "web_extract",
        "text_to_speech",
        "image_generate",
        "terminal",
        "browser_back",
        "browser_navigate",
        "browser_snapshot",
    ]

    async def check(self, ctx: Any = None) -> ConstraintResult:
        try:
            from tools.registry import registry
            import model_tools  # noqa: F401 — triggers all registry.register calls
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"无法导入 model_tools 或 registry: {exc}",
                suggestion="检查 import 链是否完整",
            )

        missing = []
        for tool_name in self.REQUIRED_TOOLS:
            if tool_name not in registry._tools:
                missing.append(tool_name)

        if missing:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"未注册的工具: {', '.join(missing)}",
                suggestion="检查对应工具文件的 registry.register() 调用",
            )
        return ConstraintResult(
            name=self.name,
            passed=True,
            detail=f"全部 {len(self.REQUIRED_TOOLS)} 个关键工具已注册",
        )


class ScholarForgeToolsConstraint(Constraint):
    """验证 ScholarForge 工具集可用。"""

    name = "scholarforge_tools"
    severity = "error"

    async def check(self, ctx: Any = None) -> ConstraintResult:
        try:
            from hermes_cli.scholarforge import tools as sf_tools
            # Count SCHOLARFORGE_*_SCHEMA constants
            schemas = [
                a for a in dir(sf_tools)
                if a.startswith("SCHOLARFORGE_") and a.endswith("_SCHEMA")
            ]
            if len(schemas) < 10:
                return ConstraintResult(
                    name=self.name,
                    passed=False,
                    detail=f"只有 {len(schemas)} 个 SCHOLARFORGE_*_SCHEMA 常量，期望 >=10",
                    suggestion="检查 hermes_cli/scholarforge/tools.py 的 schema 定义",
                )
            # Verify _PROVIDER_FALLBACK_MODELS exists
            if not hasattr(sf_tools, "_PROVIDER_FALLBACK_MODELS"):
                return ConstraintResult(
                    name=self.name,
                    passed=False,
                    detail="_PROVIDER_FALLBACK_MODELS 不存在",
                    suggestion="检查 provider fallback 配置",
                )
            return ConstraintResult(
                name=self.name,
                passed=True,
                detail=f"ScholarForge {len(schemas)} 个 schema + provider fallback 就位",
            )
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"ScholarForge 导入失败: {exc}",
                suggestion="检查 hermes_cli/scholarforge/ 模块完整性",
            )


class GatewayMixinsConstraint(Constraint):
    """验证 GatewayRunner MRO 包含所有 mixin（P2-1 拆分后不可丢失）。"""

    name = "gateway_mixins"
    severity = "error"

    EXPECTED_MIXINS = [
        "TelegramTopicsMixin",
        "VoiceMixin",
        "GoalMixin",
        "KanbanMixin",
        "SessionMixin",
        "AuthMixin",
        "WatcherMixin",
        "ConfigLoaderMixin",
        "SlashCommandsMixin",
    ]

    async def check(self, ctx: Any = None) -> ConstraintResult:
        try:
            from gateway.run import GatewayRunner
            mixin_names = [c.__name__ for c in GatewayRunner.__mro__]
            missing = [m for m in self.EXPECTED_MIXINS if m not in mixin_names]
            if missing:
                return ConstraintResult(
                    name=self.name,
                    passed=False,
                    detail=f"MRO 缺失 mixin: {', '.join(missing)}",
                    suggestion="检查 gateway/*_mixin.py 的 import 和基类声明",
                )
            return ConstraintResult(
                name=self.name,
                passed=True,
                detail=f"MRO 包含全部 {len(self.EXPECTED_MIXINS)} 个 mixin",
            )
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"GatewayRunner 导入失败: {exc}",
                suggestion="检查 gateway/run.py 的 import 链",
            )


class HarnessIntegrityConstraint(Constraint):
    """验证 harness 三件套（recoverable/stability/constraints）可导入。"""

    name = "harness_integrity"
    severity = "error"

    async def check(self, ctx: Any = None) -> ConstraintResult:
        modules = []
        try:
            from harness.recoverable import recoverable_tool, classify_failure
            modules.append("recoverable")
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"recoverable 导入失败: {exc}",
            )
        try:
            from harness.stability import probe_stability
            modules.append("stability")
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"stability 导入失败: {exc}",
            )
        try:
            from harness.constraints import Constraint, run_constraints
            modules.append("constraints")
        except Exception as exc:
            return ConstraintResult(
                name=self.name,
                passed=False,
                detail=f"constraints 导入失败: {exc}",
            )
        return ConstraintResult(
            name=self.name,
            passed=True,
            detail=f"harness 三件套就位: {', '.join(modules)}",
        )


class RecoverableToolCoverageConstraint(Constraint):
    """验证 @recoverable_tool 至少覆盖了关键工具入口。"""

    name = "recoverable_coverage"
    severity = "warning"

    EXPECTED_DECORATED = [
        "browser_back",
        "vision_analyze",
        "video_analyze",
        "web_search",
        "web_extract",
        "text_to_speech",
        "image_generate",
        "terminal",
    ]

    async def check(self, ctx: Any = None) -> ConstraintResult:
        decorated = []
        missing = []

        tool_map = {
            "browser_back": ("tools.browser_tool", "browser_back"),
            "vision_analyze": ("tools.vision_tools", "_handle_vision_analyze"),
            "video_analyze": ("tools.vision_tools", "_handle_video_analyze"),
            "web_search": ("tools.web_tools", "web_search_tool"),
            "web_extract": ("tools.web_tools", "web_extract_tool"),
            "text_to_speech": ("tools.tts_tool", "text_to_speech_tool"),
            "image_generate": ("tools.image_generation_tool", "_handle_image_generate"),
            "terminal": ("tools.terminal_tool", "_handle_terminal"),
        }

        for tool_name, (mod_path, func_name) in tool_map.items():
            try:
                mod = importlib.import_module(mod_path)
                fn = getattr(mod, func_name, None)
                if fn and hasattr(fn, "__wrapped__"):
                    decorated.append(tool_name)
                else:
                    missing.append(tool_name)
            except Exception:
                missing.append(tool_name)

        if missing:
            return ConstraintResult(
                name=self.name,
                passed=False,
                severity="warning",
                detail=f"未装饰的工具: {', '.join(missing)}",
                suggestion="加 @recoverable_tool 装饰器",
            )
        return ConstraintResult(
            name=self.name,
            passed=True,
            detail=f"{len(decorated)}/{len(self.EXPECTED_DECORATED)} 关键工具已接入 @recoverable_tool",
        )


def build_release_constraints() -> list[Constraint]:
    """构建发布前约束集。"""
    return [
        HarnessIntegrityConstraint(),
        ToolRegistrationConstraint(),
        ScholarForgeToolsConstraint(),
        GatewayMixinsConstraint(),
        RecoverableToolCoverageConstraint(),
    ]


async def main():
    """CLI 入口：python3 -m harness.release_constraints"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    constraints = build_release_constraints()
    report = await run_constraints(constraints)
    print(f"\n{'='*60}")
    print("Release Constraints Report")
    print(f"{'='*60}")
    status = "PASS" if report.passed else "FAIL"
    print(f"Overall: {status}")
    print(f"{'='*60}")
    for r in report.results:
        icon = "OK" if r.passed else ("WARN" if r.severity == "warning" else "FAIL")
        print(f"  [{icon}] {r.name}: {r.detail}")
        if r.suggestion:
            print(f"         -> {r.suggestion}")
    print(f"{'='*60}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))