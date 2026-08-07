"""Slice 3 反向验证（R5 解药）：证明 golden-set 不是「真空通过」。

纪律背景（见 MEMORY.md 工作纪律红线）：**写完回归测试必做反向验证**——
把测试拷到「有 bug」的状态下跑，它必须失败，否则测试本身没测到东西。

本文件回答三件事：
1. 正常跑 run_manifest 必须全绿（sanity，证明 harness 能驱动真实 handler）。
2. 把 project_context.save_section 打桩成 no-op（模拟 P0「写回不落库」），
   write_abstract_persists 必须失败 —— 证伪「查表谓词是摆设、只要调了工具就过」。
3. 门禁 compare() 必须在这个 no-op 下把 write_abstract_persists 标为 regression ——
   证明门禁的「退步检测」确实会响，而不是永远绿灯。

注意：harness / eval_gate 都不依赖 pytest；本文件只是用 pytest 把它们串起来做反向验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 防御性：保证 repo root 在 sys.path（pytest prepend 模式通常已插入，这里兜底）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.golden.harness import (  # noqa: E402
    DEFAULT_MANIFEST,
    GoldenHarness,
    load_manifest,
    run_manifest,
)
from tests.eval.golden.predicates import get_predicate  # noqa: E402
from scripts.eval_gate import build_baseline, compare  # noqa: E402


MANIFEST = load_manifest(DEFAULT_MANIFEST)


def _case(cid: str) -> dict:
    for c in MANIFEST["cases"]:
        if c["id"] == cid:
            return c
    raise KeyError(f"case not in manifest: {cid}")


# ──────────────────────────────────────────────────────────────
# 1) sanity：正常全绿
# ──────────────────────────────────────────────────────────────

def test_normal_run_all_pass() -> None:
    """harness 能离线驱动真实 handler，4 条回归护栏当前全过。"""
    report = run_manifest()
    # 契约锁死：至少存在这 4 条对应历史回归的护栏，且全部通过。
    assert report.total >= 4, f"golden-set 用例数异常：{report.total}"
    assert report.passed == report.total, {
        c.id: c.failures for c in report.cases if not c.passed
    }
    assert report.task_success_rate == 1.0


def test_manifest_predicates_all_resolve() -> None:
    """manifest 里引用的每一个 predicate 名字都必须在注册表里存在。

    抓「manifest 写错谓词名却因 get_predicate 抛错被静默」这类问题——
    get_predicate 对未知名字会抛 KeyError（见 predicates.py），这里确认不抛。
    """
    for case in MANIFEST["cases"]:
        for exp in case.get("expect", []):
            name = exp.get("predicate", "")
            try:
                get_predicate(name)
            except KeyError as e:
                pytest.fail(f"case {case['id']} 引用了未注册谓词 {name!r}：{e}")


def test_write_case_passes_normally() -> None:
    """对照组：不开 no-op 时，write_abstract_persists 必须通过。"""
    with GoldenHarness() as h:
        res = h.run_case(_case("write_abstract_persists"))
    assert res.passed is True, res.failures


# ──────────────────────────────────────────────────────────────
# 2) R5 解药：打桩 no-op 必须让落库护栏失败
# ──────────────────────────────────────────────────────────────

def test_write_case_fails_when_save_is_noop(monkeypatch) -> None:
    """把 project_context.save_section 打成 no-op（模拟 P0 写回不落库），
    落库谓词必须抓到失败——否则这个 case 就是真空通过。"""
    import vermes_cli.scholarforge.project_context as pc

    def _noop_save(project_id, section_key, content):  # noqa: ANN001
        return False  # 写回静默失败，不落库

    monkeypatch.setattr(pc, "save_section", _noop_save)

    with GoldenHarness() as h:
        res = h.run_case(_case("write_abstract_persists"))

    assert res.passed is False, (
        "❌ 反向验证失败：save_section 已被打成 no-op，但 case 仍判通过。"
        "说明落库谓词没有真正查 section_contents 表，是真空通过。"
        f" checks={res.checks}"
    )
    # 失败的谓词里至少要包含「查表」类证据（section_persisted 或 返回串报错）。
    failed = {c.predicate for c in res.checks if not c.ok}
    assert ("section_persisted" in failed) or ("result_not_error" in failed), (
        f"失败原因不含落库证据，疑似误判：{res.checks}"
    )


def test_gate_flags_regression(monkeypatch) -> None:
    """门禁 compare() 必须在 save no-op 下把 write_abstract_persists 标为 regression。"""
    import vermes_cli.scholarforge.project_context as pc

    # 1) 基线 = 正常全绿
    baseline_report = run_manifest()
    baseline = build_baseline(baseline_report)
    assert baseline["task_success_rate"] == 1.0

    # 2) 退化运行：save_section no-op
    def _noop_save(project_id, section_key, content):  # noqa: ANN001
        return False

    monkeypatch.setattr(pc, "save_section", _noop_save)

    regressed_report = run_manifest()
    diff = compare(regressed_report, baseline)

    assert diff["regressions"], f"门禁未检测到任何回归：{diff}"
    assert "write_abstract_persists" in diff["regressions"], (
        f"门禁漏报了 write_abstract_persists 的回归：{diff}"
    )
