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
# 3. 账本解析：DIVERSION_LEDGER 格式可被脚本正确解析
# ---------------------------------------------------------------------------

def test_parse_diversion_ledger_extracts_t3_paths():
    """DIVERSION_LEDGER 里的 T3 登记（tools/）必须能被解析成 tools/ 前缀。"""
    prefixes = uw.parse_diversion_ledger()
    assert "tools/" in prefixes, f"DIVERSION_LEDGER 应解析出 tools/，实际: {prefixes}"


def test_registered_diversion_matches_t3_files():
    """已登记的 T3 三文件（follow 区）不得再被算税。"""
    prefixes = uw.parse_diversion_ledger()
    for path in (
        "tools/env_passthrough.py",
        "tools/environments/docker.py",
        "tools/environments/local.py",
    ):
        assert uw.is_registered_diversion(path, prefixes), f"{path} 应已登记为有意偏离"


def test_unregistered_path_still_taxed():
    """未登记的 follow 区路径仍算税（防「一登记全免」的误伤）。"""
    prefixes = uw.parse_diversion_ledger()
    assert not uw.is_registered_diversion("plugins/some_upstream_plugin/x.py", prefixes)
    assert not uw.is_registered_diversion("harness/stability.py", prefixes)


def test_classify_docs_vermes_is_own():
    """docs/vermes/ 应分类为 own（外置后不税），docs/ 其他仍 follow。"""
    assert uw.classify("docs/vermes/TASK_BOARD.md") == "own"
    assert uw.classify("docs/OTHER.md") == "follow"
    assert uw.classify("scripts/vermes/x.py") == "own"
    assert uw.classify("scripts/other.py") == "follow"
