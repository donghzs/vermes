"""S2.2 identity walking skeleton 契约测。

工单 §5：统一注入入口 `_resolve_section` + 只迁 `identity` 一块。
硬门槛：gold 逐字相同由 `test_s2_gold` / `scripts/s2_snapshot.py --check` 把关；
本文件钉住入口三元组、字节等价、canonical hash、S2.4 map 退役后的 missing 路径、
computer_use 惰性哨兵，以及真注入路径。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.prompt_processor_loader import (
    clear_plugin_processors,
    compute_manifest_hash,
    load_all_processors,
)
from agent import system_prompt as sp

ROOT = Path(__file__).resolve().parents[2]
IDENTITY_YAML = ROOT / "vermes_cli" / "processors" / "identity.yaml"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SHA64 = re.compile(r"^sha256:[0-9a-f]{64}$")


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_plugin_processors()
    yield
    clear_plugin_processors()


def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hide_processors(monkeypatch):
    """让 processor 查找恒空 —— 走 fallback / fallback-lazy / missing 路径。"""
    monkeypatch.setattr(sp, "load_all_processors", lambda: [])


def test_resolve_section_identity_triple_shape():
    """入口契约：三元组形状 + hash 为 64hex 或 sha256:64hex（canonical）。"""
    content, source, content_hash = sp._resolve_section("identity")
    assert isinstance(content, str) and content.strip()
    assert source in {"user", "builtin", "plugin", "fallback", "fallback-lazy", "missing", "processor"}
    assert content_hash, "content_hash 不得为空"
    assert _SHA64.match(content_hash) or _HEX64.match(content_hash), (
        f"hash 不是 64hex / sha256:64hex：{content_hash!r}"
    )


def test_identity_byte_equivalent_to_proc_or_default():
    """S2.2 只换路径、不换字：identity 与旧入口逐字相同。"""
    content, _source, _h = sp._resolve_section("identity")
    assert content == sp._proc_or_default("identity")


def test_identity_source_builtin_and_canonical_hash():
    """source=builtin；hash == compute_manifest_hash(identity.yaml)。"""
    import yaml

    data = yaml.safe_load(IDENTITY_YAML.read_text(encoding="utf-8"))
    expected = compute_manifest_hash(data)

    content, source, content_hash = sp._resolve_section("identity")
    assert source == "builtin", f"identity 应走 builtin YAML，实测 source={source}"
    assert content_hash == expected, (
        "content_hash 必须是 compute_manifest_hash 的 canonical 值且可解释"
    )
    assert content_hash.startswith("sha256:")
    # 与 processor 实例同源（不瞎 sha）
    proc = next(p for p in load_all_processors() if p.effective_id == "identity")
    assert content_hash == proc.content_hash
    assert content == proc.content


def test_missing_path_after_fallback_retired(monkeypatch, caplog):
    """S2.4：hide YAML 后非 computer_use 键走 missing（不再有 map 常量兜底）。"""
    from agent.prompt_builder import DEFAULT_AGENT_IDENTITY, EDITING_GUARDRAILS_GUIDANCE

    _hide_processors(monkeypatch)
    with caplog.at_level("WARNING", logger="agent.system_prompt"):
        content, source, content_hash = sp._resolve_section("editing_guardrails")
    assert source == "missing"
    assert content != EDITING_GUARDRAILS_GUIDANCE
    assert "[prompt-section missing: editing_guardrails]" in content
    assert content_hash == _sha256_of(content)

    with caplog.at_level("WARNING", logger="agent.system_prompt"):
        content2, source2, hash2 = sp._resolve_section("identity")
    assert source2 == "missing"
    assert content2 != DEFAULT_AGENT_IDENTITY
    assert "[prompt-section missing: identity]" in content2
    assert hash2 == _sha256_of(content2)


def test_missing_key_returns_visible_placeholder_and_warns(monkeypatch, caplog):
    """缺失键 → 可见占位文本（非空串）+ source=missing + warning，不静默。"""
    _hide_processors(monkeypatch)
    with caplog.at_level("WARNING", logger="agent.system_prompt"):
        content, source, content_hash = sp._resolve_section("no_such_section_xyz")
    assert source == "missing"
    assert content, "missing 不得返回空串（Hermes：异常必须可见）"
    assert "[prompt-section missing: no_such_section_xyz]" in content
    assert content_hash == _sha256_of(content)
    assert any("no_such_section_xyz" in r.message for r in caplog.records) or any(
        "no_such_section_xyz" in (r.getMessage() or "") for r in caplog.records
    )


def test_computer_use_lazy_sentinel(monkeypatch):
    """§9b.1 哨兵坑：map 已退役，但 computer_use 显式惰性分支必须保留。"""
    from agent.prompt_builder import COMPUTER_USE_GUIDANCE

    assert not hasattr(sp, "_PROCESSOR_FALLBACK"), "S2.4 应已删除 _PROCESSOR_FALLBACK"

    # 无 processor 时的惰性路径：hash == sha256(content)
    _hide_processors(monkeypatch)
    content, source, content_hash = sp._resolve_section("computer_use")
    assert source == "fallback-lazy"
    assert content == COMPUTER_USE_GUIDANCE
    assert content_hash == _sha256_of(content)
    assert _HEX64.match(content_hash)


def test_build_system_prompt_parts_stable_contains_identity(monkeypatch):
    """真注入路径：build_system_prompt_parts 的 stable 含 identity 内容。"""

    class _StubRA:
        def load_soul_md(self):
            return None

        def build_nous_subscription_prompt(self, *a, **k):
            return ""

        def build_environment_hints(self):
            return ""

        def build_context_files_prompt(self, *a, **k):
            return ""

        def build_workspace_block(self, *a, **k):
            return ""

        def get_toolset_for_tool(self, name):
            return None

        def build_skills_system_prompt(self, **k):
            return ""

        def resolve_compact_skill_categories(self, **k):
            return "off"

    monkeypatch.setattr(sp, "_ra", lambda: _StubRA())
    agent = SimpleNamespace(
        load_soul_identity=False,  # 不走 SOUL，逼 identity 走 _resolve_section
        skip_context_files=True,
        valid_tool_names=["read_file"],
        provider="openai",
        model="gpt-4o-mini",
        platform="cli",
        session_id="s22-test",
        pass_session_id=False,
        _memory_store=None,
        _memory_manager=None,
        _memory_enabled=False,
        _user_profile_enabled=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _task_completion_guidance=True,
        _kanban_worker_guidance=None,
    )
    parts = sp.build_system_prompt_parts(agent)
    expected_content, _src, _h = sp._resolve_section("identity")
    assert expected_content in parts["stable"], "stable 必须真注入 identity 内容"


def test_all_keys_proc_or_default_matches_resolve_section():
    """13+ 调用点零回归：全部 15 键 `_proc_or_default` == `_resolve_section` content。"""
    from tests.tools.test_s23_migrate_sections import CONSTANTS

    keys = list(CONSTANTS.keys())
    assert len(keys) == 15, f"预期 15 键，实测 {len(keys)}：{keys}"
    for name in keys:
        assert sp._proc_or_default(name) == sp._resolve_section(name)[0], f"键 {name} 字节不等价"
