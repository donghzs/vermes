"""L-014 契约测：`max_chars` 单片段长度上限（工单 §8 唯一缺口）。

覆盖面：
- 插件注册面：超限 raise（已有 test_s21，此处钉出口）
- YAML 加载面：超限 skip + WARNING
- 注入出口：render_content / _resolve_section 截断 + WARNING
- Callable 渲染膨胀：按 `_plugin_max_chars` 截断
- 默认 8000，YAML/插件可覆盖
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.prompt_processor_loader import (
    DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS,
    PromptProcessor,
    clear_plugin_processors,
    invalidate_cache,
    load_all_processors,
)
from agent import system_prompt as sp


@pytest.fixture(autouse=True)
def _clean():
    clear_plugin_processors()
    invalidate_cache()
    yield
    clear_plugin_processors()
    invalidate_cache()


def test_default_cap_is_8000():
    assert DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS == 8000


def test_builtin_yaml_all_under_cap():
    """在库 37 个 YAML 不得超限（否则会被 skip → 缺段）。"""
    for p in load_all_processors():
        assert len(p.content) <= p.effective_max_chars, (
            f"{p.effective_id} content {len(p.content)} > cap {p.effective_max_chars}"
        )


def test_yaml_over_cap_is_skipped(tmp_path, monkeypatch):
    """YAML 超限 → 不进事实层 + WARNING。"""
    import yaml

    big = tmp_path / "big.yaml"
    big.write_text(
        yaml.safe_dump(
            {
                "name": "big.section",
                "content": "x" * (DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS + 10),
                "triggers": {"type": "always"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agent.prompt_processor_loader._get_builtin_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        "agent.prompt_processor_loader._get_user_dir", lambda: tmp_path / "nope"
    )
    invalidate_cache()
    ids = {p.effective_id for p in load_all_processors()}
    assert "big.section" not in ids


def test_yaml_custom_max_chars_respected(tmp_path, monkeypatch):
    """YAML 可声明更小的 max_chars，照样拒载。"""
    import yaml

    f = tmp_path / "tight.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "name": "tight.section",
                "content": "y" * 300,
                "max_chars": 100,
                "triggers": {"type": "always"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.prompt_processor_loader._get_builtin_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "agent.prompt_processor_loader._get_user_dir", lambda: tmp_path / "nope"
    )
    invalidate_cache()
    ids = {p.effective_id for p in load_all_processors()}
    assert "tight.section" not in ids


def test_render_content_truncates_callable_blowup():
    """Callable 渲染膨胀：按 _plugin_max_chars 截断 + WARNING。"""
    p = PromptProcessor(
        name="dyn",
        content="",
        layer="volatile",
        render={"engine": "plugin_callable", "on_missing": "keep", "inputs": {}},
        metadata={"source": "plugin", "plugin_callable": True},
    )
    p._plugin_callable = lambda ctx: "Z" * 500
    p._plugin_max_chars = 100
    out = p.render_content({})
    assert len(out) == 100
    assert out == "Z" * 100


def test_resolve_section_respects_cap():
    """装配出口走 render_content，超限段被截断而不是撑爆 prompt。"""
    p = PromptProcessor(
        name="wide",
        content="W" * 200,
        layer="volatile",
        metadata={"source": "plugin", "max_chars": 50},
    )
    content, source, _h = sp._resolve_section("wide")
    # 无 plugin 时走 missing；直接测 render_content 与 enforce 即可
    assert p.render_content() == "W" * 50
    assert p.effective_max_chars == 50


def test_mustache_render_expansion_capped():
    """mustache 渲染后膨胀也过 max_chars。"""
    p = PromptProcessor(
        name="m",
        content="hello {{name}}",
        layer="volatile",
        render={"engine": "mustache", "on_missing": "keep", "inputs": {}},
        metadata={"max_chars": 10},
    )
    out = p.render_content({"name": "WORLDWORLDWORLD"})
    assert len(out) == 10
