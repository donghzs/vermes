"""core 区登记（Hermes 2026-09-23）契约测。

G1 绿色只覆盖 follow 区；core 分叉必须在 §7d CORE_DIVERGE_LEDGER 登记。
本文件钉住：账本可解析、自冻结锚起 7 个 core 文件全覆盖、S2 两键有「为何不能走插件」理由。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs" / "DISTRIBUTION_MANIFEST.md"

# 自冻结锚 888bf8a344 起 core 区已改文件（Hermes 点名 7 个 + acp_adapter/server.py = 8）
FREEZE_REF = "888bf8a344"
EXPECTED_CORE_FILES = {
    "agent/agent_init.py",
    "agent/conversation_compression.py",
    "agent/file_safety.py",
    "agent/prompt_processor_loader.py",
    "agent/system_prompt.py",
    "gateway/run.py",
    "gateway/session_context.py",
    "acp_adapter/server.py",
}


def _load_watch():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "upstream_watch", ROOT / "scripts" / "upstream_watch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_core_ledger_parses_and_covers_all_core_files():
    """自冻结锚起 core 改动必须 100% 在 §7d 登记（契约测名单与 git 实测对齐）。"""
    mod = _load_watch()
    exact, dirs = mod.parse_core_diverge_ledger()
    registered = set(exact)
    for p in EXPECTED_CORE_FILES:
        hit = p in registered or any(p.startswith(d) for d in dirs)
        assert hit, f"core 文件未登记：{p}（须进 §7d CORE_DIVERGE_LEDGER）"


def test_s2_core_entries_explain_why_not_plugin():
    """C-001/C-002 必须写明「为什么改 core」与「为何不能走插件形态」。"""
    text = MANIFEST.read_text(encoding="utf-8")
    assert "CORE_DIVERGE_LEDGER" in text
    for cid, path in (("C-001", "system_prompt.py"), ("C-002", "prompt_processor_loader.py")):
        assert cid in text, f"缺 {cid}"
        # 同一行/条目须含路径与「不能走插件」
        rows = [ln for ln in text.splitlines() if cid in ln and path in ln]
        assert rows, f"{cid} 行未同时含 {path}"
        assert any("不能走插件" in ln for ln in rows), f"{cid} 缺「为何不能走插件形态」"


def test_green_boundary_wording_present():
    """G1 注记必须写明 PASS 只覆盖 follow 区（Hermes 盲区补丁）。"""
    text = MANIFEST.read_text(encoding="utf-8")
    assert "只覆盖 follow" in text or "只覆盖 follow 区" in text
    assert "core 登记率" in text


def test_disable_sections_wording_not_overclaimed():
    """VERMES_DISABLE_PROMPT_SECTIONS 不得写成「已具备/已生效」。"""
    doctor = (ROOT / "vermes_cli" / "doctor.py").read_text(encoding="utf-8")
    assert "尚未实现" in doctor
    assert "计划于 S2.2 生效" not in doctor
