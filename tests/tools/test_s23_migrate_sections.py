"""S2.3/S2.4 契约测：15 键逐键字节等价 + 漂移钉扎 + map 退役。

工单 §5 S2.3 通过门：「每个键单独一次比对」。
S2.4：以 YAML 为准回写常量后退役 `_PROCESSOR_FALLBACK`；本文件对 15 键逐个断言
`_resolve_section` content == builtin YAML/常量（两者应字节等价），并钉住
`KNOWN_YAML_CONSTANT_DRIFT`（S2.4 后应为空集）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent.prompt_processor_loader import (
    clear_plugin_processors,
    invalidate_cache,
    load_all_processors,
)
from agent import system_prompt as sp
from agent.prompt_builder import (
    ACADEMIC_SEARCH_GUIDANCE,
    COMPUTER_USE_GUIDANCE,
    EDITING_GUARDRAILS_GUIDANCE,
    GOOGLE_MODEL_OPERATIONAL_GUIDANCE,
    IMAGE_GENERATE_GUIDANCE,
    KANBAN_GUIDANCE,
    MEMORY_GUIDANCE,
    OPENAI_MODEL_EXECUTION_GUIDANCE,
    SCHOLARFORGE_WORKFLOW_GUIDANCE,
    SESSION_SEARCH_GUIDANCE,
    SKILLS_GUIDANCE,
    TASK_COMPLETION_GUIDANCE,
    TOOL_USE_ENFORCEMENT_GUIDANCE,
    DEFAULT_AGENT_IDENTITY,
    VERMES_AGENT_HELP_GUIDANCE,
)

ROOT = Path(__file__).resolve().parents[2]
EG_YAML = ROOT / "vermes_cli" / "processors" / "editing_guardrails.yaml"

# 15 键 → 硬编码常量（fallback 侧 source-of-truth）
CONSTANTS = {
    "identity": DEFAULT_AGENT_IDENTITY,
    "help_guidance": VERMES_AGENT_HELP_GUIDANCE,
    "task_completion": TASK_COMPLETION_GUIDANCE,
    "editing_guardrails": EDITING_GUARDRAILS_GUIDANCE,
    "memory_guidance": MEMORY_GUIDANCE,
    "session_search": SESSION_SEARCH_GUIDANCE,
    "skills_guidance": SKILLS_GUIDANCE,
    "image_generate": IMAGE_GENERATE_GUIDANCE,
    "academic_search": ACADEMIC_SEARCH_GUIDANCE,
    "scholarforge_workflow": SCHOLARFORGE_WORKFLOW_GUIDANCE,
    "kanban": KANBAN_GUIDANCE,
    "computer_use": COMPUTER_USE_GUIDANCE,
    "tool_use_enforcement": TOOL_USE_ENFORCEMENT_GUIDANCE,
    "google_model": GOOGLE_MODEL_OPERATIONAL_GUIDANCE,
    "openai_model": OPENAI_MODEL_EXECUTION_GUIDANCE,
}

# S2.4：4 键常量已以 builtin YAML 为准回写，双源漂移清空。
# 此钉扎表保留为空集——若再漂移必须显式改此表（=重新引入双源）。
KNOWN_YAML_CONSTANT_DRIFT = frozenset()


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_plugin_processors()
    invalidate_cache()
    yield
    clear_plugin_processors()
    invalidate_cache()


def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_editing_guardrails_yaml_exists_and_byte_equivalent():
    """S2.3 补 YAML：content 必须与 EDITING_GUARDRAILS_GUIDANCE 字节等价。"""
    assert EG_YAML.exists(), "缺 editing_guardrails.yaml"
    import yaml

    data = yaml.safe_load(EG_YAML.read_text(encoding="utf-8"))
    assert data["name"] == "editing_guardrails"
    assert data["content"] == EDITING_GUARDRAILS_GUIDANCE, (
        "YAML content 与常量不一致 —— gold 会红或双源漂移"
    )


def test_editing_guardrails_now_resolves_via_builtin_yaml():
    """补 YAML 后 source 应为 builtin（不再是 fallback），content 仍等于常量。"""
    content, source, content_hash = sp._resolve_section("editing_guardrails")
    assert source == "builtin", f"实测 source={source}"
    assert content == EDITING_GUARDRAILS_GUIDANCE
    assert content_hash.startswith("sha256:")
    proc = next(p for p in load_all_processors() if p.effective_id == "editing_guardrails")
    assert content_hash == proc.content_hash


@pytest.mark.parametrize("name", sorted(CONSTANTS.keys()))
def test_each_key_content_matches_source_of_truth(name):
    """逐键一次比对（工单 §5 S2.3 通过门）：content 等于其生效源，且与薄包装字节等价。

    生效源 = processor 在场用 processor（gold 也走这条）。
    S2.4 退役 `_PROCESSOR_FALLBACK` 后：YAML 缺失 → missing；仅 computer_use 有 fallback-lazy。
    YAML 与常量的双源漂移单独钉住（KNOWN_YAML_CONSTANT_DRIFT）。
    """
    expected_const = CONSTANTS[name]
    content, source, content_hash = sp._resolve_section(name)

    # 统一入口与薄包装必须字节等价
    assert content == sp._proc_or_default(name)

    if source in ("builtin", "user", "plugin"):
        proc = next(
            p for p in load_all_processors() if p.effective_id == name or p.name == name
        )
        assert content == proc.content
        assert content_hash == proc.content_hash
        assert content == expected_const, f"{name}: YAML 与常量漂移（应改 KNOWN 表）"
    elif source == "fallback-lazy":
        assert name == "computer_use"
        assert content == COMPUTER_USE_GUIDANCE
        assert content_hash == _sha256_of(content)
    else:
        raise AssertionError(f"{name}: 不期望的 source={source}")


def test_yaml_constant_drift_catalog_pinned():
    """钉住 YAML vs 常量漂移集合（S2.4 后应为空集；变化必须显式改此表）。"""
    drifted = set()
    for name, const in CONSTANTS.items():
        content, source, _h = sp._resolve_section(name)
        if source in ("builtin", "user", "plugin") and content != const:
            drifted.add(name)
    assert drifted == set(KNOWN_YAML_CONSTANT_DRIFT), (
        f"漂移集合变化：实际 {sorted(drifted)} vs 期望 {sorted(KNOWN_YAML_CONSTANT_DRIFT)}"
    )


def test_all_15_keys_have_processor():
    """15 键全部经 processor 可解析且非空（missing 不允许；S2.4 起无 map 兜底）。"""
    assert not hasattr(sp, "_PROCESSOR_FALLBACK"), "S2.4 应已删除 _PROCESSOR_FALLBACK"
    for name in CONSTANTS:
        content, source, _h = sp._resolve_section(name)
        assert content, f"{name} content 为空"
        assert source != "missing", f"{name} 解析为 missing"
        assert source != "fallback", f"{name} 不应再走 map fallback"


def test_all_call_sites_use_resolve_section():
    """S2.3：注入点不得再直接调 `_proc_or_default`（薄包装仅供测试/兼容）。"""
    import re

    src = (ROOT / "agent" / "system_prompt.py").read_text(encoding="utf-8")
    body = src.split("def build_system_prompt_parts", 1)[1]
    body = body.split("\ndef ", 1)[0]
    # 真调用点：标识符后紧跟 (；注释/文档里的 `_proc_or_default(` 不算
    calls = re.findall(r"(?<![`\w])_proc_or_default\(", body)
    assert not calls, f"build_system_prompt_parts 仍有 {len(calls)} 处 _proc_or_default 调用"
    assert body.count("_resolve_section(") >= 15, "15 键应全部走 _resolve_section"
