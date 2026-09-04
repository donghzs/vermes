"""Recipe loader / schema tests (请神收尾 T1)."""

from __future__ import annotations

import pytest
from pathlib import Path

from vermes_cli.a2a.recipes.loader import (
    load_recipe,
    load_all_recipes,
    find_recipe,
    RECIPES_DIR,
)


def test_load_copilot_recipe_ok() -> None:
    rc = load_recipe(RECIPES_DIR / "copilot.yaml")
    assert rc.name == "copilot"
    assert rc.transport == "acp"
    assert rc.provider == "copilot-acp"
    assert rc.spawn_command == ["copilot", "--acp", "--stdio"]
    assert "code" in rc.capabilities
    assert rc.auth.env_var == "COPILOT_API_KEY"


def test_codex_acp_entry_point_uses_zed_adapter() -> None:
    # 用户硬约束：Codex 经 Zed npx 适配器，不是裸 codex --acp
    rc = load_recipe(RECIPES_DIR / "codex-acp.yaml")
    assert rc.spawn_command == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]
    assert rc.provider == "acp-codex"
    assert rc.auth.env_var == "OPENAI_API_KEY"


def test_claude_agent_acp_entry_point_uses_zed_adapter() -> None:
    # 用户硬约束：Claude Code 经 Zed npx 适配器，不是裸 claude
    rc = load_recipe(RECIPES_DIR / "claude-agent-acp.yaml")
    assert rc.spawn_command == ["npx", "@agentclientprotocol/claude-agent-acp@0.73.0"]
    assert rc.provider == "acp-claude-agent"
    assert rc.auth.env_var == "ANTHROPIC_API_KEY"


def test_missing_required_field(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nversion: 1.0.0\n")  # 缺 transport/entry_point/description
    with pytest.raises(ValueError, match="missing required field"):
        load_recipe(bad)


def test_args_wrong_type(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\nversion: 1.0.0\ndescription: d\n"
        "transport: acp\nentry_point: e\nargs: not-a-list\n"
    )
    with pytest.raises(ValueError, match="args"):
        load_recipe(bad)


def test_yaml_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nversion: [unclosed\n")
    with pytest.raises(ValueError, match="invalid YAML"):
        load_recipe(bad)


def test_unsafe_tag_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\nversion: 1.0.0\ndescription: d\n"
        "transport: acp\nentry_point: e\n"
        "auth: !!python/object:os.system ['echo hi']\n"
    )
    with pytest.raises(ValueError, match="unsafe"):
        load_recipe(bad)


def test_load_all_recipes_finds_three() -> None:
    recipes = load_all_recipes(RECIPES_DIR)
    names = {r.name for r in recipes}
    assert {"copilot", "codex-acp", "claude-agent-acp"} <= names


def test_find_recipe_by_name() -> None:
    rc = find_recipe("copilot", RECIPES_DIR)
    assert rc is not None and rc.name == "copilot"
