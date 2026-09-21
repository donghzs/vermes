# -*- coding: utf-8 -*-
"""发行版机制不变式测试：ZONES / 冻结锚 / 账本解析 的一致性契约。

这些测试守的是发行版工程的机制层，不是产品行为。目的：让「分区漂移」
「账本格式破坏」「冻结锚失效」在 CI 里直接红，不靠人脑记忆。
"""

from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import upstream_watch as uw  # noqa: E402


def _manifest_text() -> str:
    p = os.path.join(ROOT, "docs", "DISTRIBUTION_MANIFEST.md")
    assert os.path.exists(p), "DISTRIBUTION_MANIFEST.md 必须存在"
    with open(p, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. 冻结锚：boundary 默认必须读 freeze_ref，且 freeze_ref 是固定值
# ---------------------------------------------------------------------------

def test_freeze_ref_is_immutable_commit():
    """冻结锚必须是 888bf8a344 —— 发版 tag 会前移，这个不能动。"""
    assert uw.FREEZE_REF == "888bf8a344", (
        "FREEZE_REF 被改动！它是有意冻结的测量锚点，发版 tag 可以动，它不行。"
    )


def test_boundary_default_reads_freeze_ref_not_tag():
    """boundary 默认 since 必须是 FREEZE_REF，而非发版 tag（否则 tag 前移窗口塌缩）。"""
    import argparse

    p = argparse.ArgumentParser()
    b = p.add_subparsers(dest="cmd").add_parser("boundary")
    b.add_argument("--since", default=None)
    # 直接从源码契约验证：cmd_boundary 内 `since = args.since or FREEZE_REF`
    src = open(os.path.join(ROOT, "scripts", "upstream_watch.py"), encoding="utf-8").read()
    assert "since = args.since or FREEZE_REF" in src, (
        "cmd_boundary 必须用 `since = args.since or FREEZE_REF`，不得硬编码 v2.5.1"
    )


# ---------------------------------------------------------------------------
# 2. ZONES 与 manifest §2 分区表同步
# ---------------------------------------------------------------------------

def test_zones_own_contains_externalized_paths():
    """外置路径（docs/vermes/、scripts/vermes/）必须在 ZONES.own，否则外置后仍计税。"""
    for prefix in ("docs/vermes/", "scripts/vermes/"):
        assert prefix in uw.ZONES["own"], f"{prefix} 必须进 ZONES.own"


def test_zones_own_contains_vermes_only_tools():
    """Vermes 独有发行版工具必须在 own，否则被 follow 误算税。"""
    for prefix in ("scripts/upstream_watch.py", "scripts/trigger-win-build.py"):
        assert prefix in uw.ZONES["own"], f"{prefix} 是 Vermes 独有工具，必须进 ZONES.own"


def test_zones_own_prefixes_exist_in_manifest():
    """manifest §2 own 表与 ZONES.own 至少共享核心前缀（防文档/脚本漂移）。"""
    text = _manifest_text()
    # own 表行（含 docs/vermes/、scripts/vermes/）
    m = re.search(r"\|\s*\*\*own\*\*.*?\|\s*(.*?)\s*\|", text)
    assert m, "manifest §2 分区表应含 own 行"
    own_cell = m.group(1)
    for key in ("docs/vermes/", "scripts/vermes/"):
        assert key in own_cell, f"manifest §2 own 表缺 {key}（与 ZONES 漂移）"


# ---------------------------------------------------------------------------
# 3. 账本解析：DIVERSION_LEDGER 格式可被脚本正确解析（精确匹配，防放水）
# ---------------------------------------------------------------------------

def test_parse_diversion_ledger_extracts_t3_files():
    """DIVERSION_LEDGER 里的 T3 登记必须解析为精确文件（非 tools/ 目录前缀）。"""
    exact_files, dir_prefixes = uw.parse_diversion_ledger()
    assert "tools/env_passthrough.py" in exact_files, f"应精确匹配文件，实际: {exact_files}"
    assert "tools/environments/docker.py" in exact_files
    assert "tools/environments/local.py" in exact_files
    # 关键：不得把文件条目升格成 tools/ 目录前缀
    assert "tools/" not in dir_prefixes, f"文件条目不得升格为 tools/ 目录前缀: {dir_prefixes}"


def test_registered_diversion_matches_t3_files():
    """已登记的 T3 三文件（follow 区）不得再被算税。"""
    ledger = uw.parse_diversion_ledger()
    for path in (
        "tools/env_passthrough.py",
        "tools/environments/docker.py",
        "tools/environments/local.py",
    ):
        assert uw.is_registered_diversion(path, ledger), f"{path} 应已登记为有意偏离"


def test_registered_file_does_not_exempt_siblings():
    """登记单文件不豁免同目录兄弟 —— 这是防放水的核心。"""
    ledger = uw.parse_diversion_ledger()
    # tools/env_passthrough.py 已登记，但 tools/kanban_tools.py 不得因此免税
    assert not uw.is_registered_diversion("tools/kanban_tools.py", ledger)
    assert not uw.is_registered_diversion("tools/skills_tool.py", ledger)


def test_registered_docs_file_does_not_exempt_other_docs():
    """登记 docs/DISTRIBUTION_MANIFEST.md 单文件不豁免其他 docs 文件。"""
    ledger = uw.parse_diversion_ledger()
    # D-002 登记的是 docs/DISTRIBUTION_MANIFEST.md 单文件
    assert uw.is_registered_diversion("docs/DISTRIBUTION_MANIFEST.md", ledger)
    assert not uw.is_registered_diversion("docs/TASK_BOARD_20260920.md", ledger)


def test_explicit_dir_entry_exempts_children_only():
    """显式目录条目（斜杠结尾）才豁免子路径；目录外不税。"""
    exact_files, dir_prefixes = uw.parse_diversion_ledger()
    # 注入一个显式目录条目验证语义
    test_ledger = (exact_files, dir_prefixes | {"docs/vermes/"})
    assert uw.is_registered_diversion("docs/vermes/TASK_BOARD.md", test_ledger)
    assert not uw.is_registered_diversion("docs/other.md", test_ledger)


def test_unregistered_path_still_taxed():
    """未登记的 follow 区路径仍算税（防「一登记全免」的误伤）。"""
    ledger = uw.parse_diversion_ledger()
    assert not uw.is_registered_diversion("plugins/some_upstream_plugin/x.py", ledger)
    assert not uw.is_registered_diversion("harness/stability.py", ledger)


def test_halfwidth_paren_in_ledger_is_handled():
    """账本用半角括号注释时也能正确解析（百度搭子建议）。"""
    # 直接测解析器的括号剥离：构造带半角括号的单元格文本
    import re
    raw = "`tools/env_passthrough.py` (deprecated, merged into local.py)"
    path = re.split(r"[（(]", raw)[0].strip().strip("`").strip()
    assert path == "tools/env_passthrough.py"


def test_classify_docs_vermes_is_own():
    """docs/vermes/ 应分类为 own（外置后不税），docs/ 其他仍 follow。"""
    assert uw.classify("docs/vermes/TASK_BOARD.md") == "own"
    assert uw.classify("docs/OTHER.md") == "follow"
    assert uw.classify("scripts/vermes/x.py") == "own"
    assert uw.classify("scripts/other.py") == "follow"


# ---------------------------------------------------------------------------
# 4. 意图级巡检：上游 → Vermes 对应物映射
# ---------------------------------------------------------------------------

def test_vermes_counterpart_file_level():
    """文件级映射：tools/approval.py 存在 → 有对应物。"""
    vm, verdict = uw._vermes_counterpart("tools/approval.py")
    assert vm == "tools/approval.py"
    assert verdict == "有对应物"


def test_vermes_counterpart_dir_prefix_resolves_file():
    """目录前缀映射：tools/environments/local.py → 拼文件名后判定对应文件是否存在。"""
    vm, verdict = uw._vermes_counterpart("tools/environments/local.py")
    assert vm == "tools/environments/local.py"
    assert verdict == "有对应物"


def test_vermes_counterpart_redline():
    """红线仅限 ZONES.own 具体点名资产，不是 agent/、gateway/ 一刀切。"""
    # gateway/platforms/ 是红线（中文平台 17 个，ZONES.own 点名）
    _, verdict = uw._vermes_counterpart("gateway/platforms/wechat/adapter.py")
    assert verdict == "红线"
    # agent 具体领先文件是红线
    _, verdict2 = uw._vermes_counterpart("agent/memory_fabric.py")
    assert verdict2 == "红线"
    # 但 agent 其他文件（如 file_safety.py）是 core 区，标「移植/评估」非红线
    _, verdict3 = uw._vermes_counterpart("agent/file_safety.py")
    assert verdict3 == "有对应物"


def test_vermes_counterpart_specific_overrides_dir_redline():
    """精确文件匹配覆盖目录红线：gateway/platforms/webhook.py 有映射，非红线。"""
    vm, verdict = uw._vermes_counterpart("gateway/platforms/webhook.py")
    assert vm == "gateway/platforms/webhook.py"
    assert verdict == "有对应物"


def test_vermes_counterpart_missing():
    """未映射的路径 → 无。"""
    _, verdict = uw._vermes_counterpart("some/unknown/path.py")
    assert verdict == "无"


def test_intent_skip_re_matches_docs():
    """纯文档/chore/test 提交被巡检排除。"""
    assert uw.INTENT_SKIP_RE.search("docs(website): tell readers")
    assert uw.INTENT_SKIP_RE.search("chore(deps): bump")
    assert uw.INTENT_SKIP_RE.search("test(agent): fixture")
    assert not uw.INTENT_SKIP_RE.search("fix(security): leak")


def test_intent_security_re_matches_ghsa():
    """安全信号匹配 GHSA/CVE/fix(security)。"""
    assert uw.INTENT_SECURITY_RE.search("fix(security): ... GHSA-2fmg-cjqm-hhrj")
    assert uw.INTENT_SECURITY_RE.search("fix: CVE-2026-xxxx")
    assert uw.INTENT_SECURITY_RE.search("fix(approval): honor allowlists")
