# -*- coding: utf-8 -*-
"""P4-4 T3 benchmark harness CI 断言（非阻塞 visibility，与 P4-3 VALIDATED_TOOLS 同一纪律）。

验证项：
1. 任务集 TASKS 动态覆盖全部已注册 scholarforge 工具（不漏接、不漂移）。
2. dry-run 模式端到端跑通：产出合法报告（total == 任务数、wiring_rate 计算、无崩溃）。
3. 报告支持 LLM-tier 分层（跨切原则：弱/中/强各报一次，不混绝对分）。
"""
import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from tools.registry import registry
from vermes_cli.scholarforge import tools as sf_tools
from vermes_cli.scholarforge.benchmark import (
    TASKS,
    LLM_TIERS,
    run_benchmark,
    verify_task_wiring,
)
from vermes_cli.scholarforge.validation_coverage import (
    VALIDATED_TOOLS,
    short_name,
)


def _registered_scholarforge_tools():
    if not any(n.startswith("scholarforge_") for n in registry.get_all_tool_names()):
        sf_tools.register_tools()
    return {
        short_name(n)
        for n in registry.get_all_tool_names()
        if n.startswith("scholarforge_")
    }


def test_task_set_covers_all_registered_tools():
    registered = _registered_scholarforge_tools()
    assert registered, "全局 registry 中没有任何 scholarforge 工具被注册"

    covered = set()
    for task in TASKS:
        covered.update(task.tools)

    missing = sorted(registered - covered)
    assert not missing, (
        f"{len(missing)} 个已注册 scholarforge 工具未出现在 benchmark 任务集：{missing}"
    )
    # 反向：任务集引用的工具都真实注册 + 在 VALIDATED_TOOLS 中
    orphans = sorted(covered - registered)
    assert not orphans, f"任务集引用了未注册工具：{orphans}"


def test_dry_run_produces_valid_report():
    report = run_benchmark(mode="dry", llm_tier="strong")
    summary = report["summary"]
    assert summary["mode"] == "dry"
    assert summary["total"] == len(TASKS), "报告 total 应与任务数一致"
    # dry 模式：所有任务接线合法 → wiring_rate = 100
    assert summary["wiring_rate"] == 100.0, f"dry 模式 wiring_rate 应为 100，实际 {summary['wiring_rate']}"
    # 每任务都应有评分条目
    assert len(report["results"]) == summary["total"]
    for r in report["results"]:
        assert r["wired"] is True
    # category 分层存在
    assert set(summary["categories"].keys()) >= {"tool_probe", "long_chain"}


def test_report_supports_llm_tier_split():
    assert set(LLM_TIERS) == {"weak", "mid", "strong"}, "LLM 须分三档"
    # 每档都能独立产报告（不混绝对分）
    for tier in LLM_TIERS:
        report = run_benchmark(mode="dry", llm_tier=tier)
        assert report["llm_tier"] == tier
        assert report["summary"]["llm_tier"] == tier


def test_verify_task_wiring_detects_unregistered_tool():
    from dataclasses import replace
    bad = replace(TASKS[0], tools=["nonexistent_tool"])
    ok, detail = verify_task_wiring(bad)
    assert ok is False
    assert "nonexistent_tool" in detail
