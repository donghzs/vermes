"""Sprint A · 秘书代劳接入服务层 onboard_agent 单元测试。

覆盖决策树 4 分支 + need_auth + 鉴权持久化 + fail-open。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vermes_cli.a2a.login_probe import LoginProbe
from vermes_cli.a2a.onboarding import _cli_type_of, onboard_agent


@pytest.fixture
def db():
    """轻量 fake SessionDB（只覆 onboarding 用到的 upsert 方法）。"""
    d = MagicMock()
    d.upsert_agent_profile = MagicMock()
    d.upsert_a2a_agent = MagicMock()
    return d


# ---------------------------------------------------------------------------
# _cli_type_of 归一化
# ---------------------------------------------------------------------------
def test_cli_type_of_full_path():
    assert _cli_type_of("/usr/local/bin/aider") == "aider"


def test_cli_type_of_bare_bin():
    assert _cli_type_of("codex") == "codex"


def test_cli_type_of_empty():
    assert _cli_type_of("") == ""


# ---------------------------------------------------------------------------
# 分支 1：recipe 名精确匹配 → ACP 登堂（成功）
# ---------------------------------------------------------------------------
def test_onboard_by_recipe_name_success(db):
    recipe = SimpleNamespace(
        name="codex-acp",
        provider="acp-codex",
        description="OpenAI Codex (ACP)",
        is_acp=True,
        auth=SimpleNamespace(env_var=None),
        spawn_command=["npx", "@agentclientprotocol/codex-acp"],
        capabilities=["coding"],
        capability_source="inferred",
    )
    transport = MagicMock()
    transport._acp_command = "codex"
    transport._handshake = MagicMock(return_value=(True, "protocolVersion=1"))
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=recipe
    ), patch(
        "vermes_cli.a2a.onboarding.build_acp_transport", return_value=transport
    ), patch(
        # _health_check 第一级是真实 shutil.which 查 PATH（onboarding.py:184）。
        # 不隔离 → 本测试依赖「机器是否真装了 codex」：装了才绿，换机即红。
        "shutil.which", return_value="/usr/bin/codex"
    ):
        r = onboard_agent("codex-acp", db=db)
    assert r["status"] == "success"
    assert r["transport"] == "acp"
    assert r["via"] == "acp"
    assert r["profile_id"] == "a2a:acp-codex"
    db.upsert_agent_profile.assert_called_once()
    db.upsert_a2a_agent.assert_called_once()


# ---------------------------------------------------------------------------
# 分支 A：命中 recipe 但缺 key → need_auth（不建 profile）
# ---------------------------------------------------------------------------
def test_onboard_recipe_need_auth(db):
    recipe = SimpleNamespace(
        name="claude-agent-acp",
        provider="acp-claude-agent",
        description="Claude Agent (ACP)",
        is_acp=True,
        auth=SimpleNamespace(env_var="ANTHROPIC_API_KEY"),
        spawn_command=["npx", "@agentclientprotocol/claude-agent-acp"],
        capabilities=["coding"],
        capability_source="inferred",
    )
    lp = LoginProbe(logged_in=False, method="test", detail="no keychain")
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=recipe
    ), patch(
        "vermes_cli.a2a.onboarding.probe_login", return_value=lp
    ), patch.dict("os.environ", {}, clear=True):
        r = onboard_agent("claude-agent-acp", db=db)
    assert r["status"] == "need_auth"
    assert r["auth_env"] == "ANTHROPIC_API_KEY"
    assert "login_command" in r
    # need_auth 不落库
    db.upsert_agent_profile.assert_not_called()


# ---------------------------------------------------------------------------
# 分支 2b：本机发现、无 recipe、CLI 白名单内 → cli 直连
# ---------------------------------------------------------------------------
def test_onboard_local_cli_direct(db):
    disc = SimpleNamespace(
        id="agent:aider",
        name="Aider",
        kind="cli",
        entry_point="/opt/homebrew/bin/aider",
        description="",
    )
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.find_recipe_for_discovery", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.LocalAgentScanner"
    ) as scanner_cls, patch(
        "vermes_cli.blueprints.chat._CLI_PRINT_ARGS",
        {"aider": (["--message", "{prompt}"], "Aider 不可用")},
    ):
        scanner_cls.return_value.scan.return_value = [disc]
        r = onboard_agent("Aider", db=db)
    assert r["status"] == "success"
    assert r["transport"] == "cli"
    assert r["via"] == "cli"
    assert r["profile_id"] == "local:aider"
    db.upsert_agent_profile.assert_called_once()


# ---------------------------------------------------------------------------
# 分支 2b'：本机发现 CLI 不在白名单 → error
# ---------------------------------------------------------------------------
def test_onboard_local_cli_not_in_whitelist(db):
    disc = SimpleNamespace(
        id="agent:unknowncli",
        name="Unknown CLI",
        kind="cli",
        entry_point="/usr/local/bin/unknowncli",
        description="",
    )
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.find_recipe_for_discovery", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.LocalAgentScanner"
    ) as scanner_cls, patch(
        "vermes_cli.blueprints.chat._CLI_PRINT_ARGS", {}
    ):
        scanner_cls.return_value.scan.return_value = [disc]
        r = onboard_agent("Unknown CLI", db=db)
    assert r["status"] == "error"
    assert "映射表缺" in r["error"]


# ---------------------------------------------------------------------------
# 分支 2c：本机发现但非 cli 类（config dir）→ error
# ---------------------------------------------------------------------------
def test_onboard_config_dir_no_pathway(db):
    disc = SimpleNamespace(
        id="agent:cfg_.qclaw",
        name="QClaw",
        kind="config",
        entry_point="/Users/u/.qclaw",
        description="",
    )
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.find_recipe_for_discovery", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.LocalAgentScanner"
    ) as scanner_cls:
        scanner_cls.return_value.scan.return_value = [disc]
        r = onboard_agent("QClaw", db=db)
    assert r["status"] == "error"
    assert "无 ACP recipe" in r["error"]


# ---------------------------------------------------------------------------
# 分支 C：完全找不到 → not_found
# ---------------------------------------------------------------------------
def test_onboard_not_found(db):
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=None
    ), patch(
        "vermes_cli.a2a.onboarding.LocalAgentScanner"
    ) as scanner_cls:
        scanner_cls.return_value.scan.return_value = []
        r = onboard_agent("不存在的Agent", db=db)
    assert r["status"] == "not_found"
    db.upsert_agent_profile.assert_not_called()


# ---------------------------------------------------------------------------
# 鉴权持久化：给了 key → os.environ 注入 + save_credential 落库
# ---------------------------------------------------------------------------
def test_onboard_auth_value_persisted(db):
    recipe = SimpleNamespace(
        name="codex-acp",
        provider="acp-codex",
        description="OpenAI Codex (ACP)",
        is_acp=True,
        auth=SimpleNamespace(env_var="OPENAI_API_KEY"),
        spawn_command=["npx", "@agentclientprotocol/codex-acp"],
        capabilities=["coding"],
        capability_source="inferred",
    )
    transport = MagicMock()
    transport._acp_command = "codex"
    transport._handshake = MagicMock(return_value=(True, "ok"))
    with patch(
        "vermes_cli.a2a.onboarding.find_recipe", return_value=recipe
    ), patch(
        "vermes_cli.a2a.onboarding.build_acp_transport", return_value=transport
    ), patch(
        "vermes_cli.a2a.onboarding._health_check", return_value=(True, "ok")
    ), patch(
        "vermes_cli.a2a.onboarding.save_credential"
    ) as save_mock, patch.dict("os.environ", {}, clear=True):
        r = onboard_agent("codex-acp", auth_value="sk-test123", db=db)
    assert r["status"] == "success"
    assert r["auth"]["persisted"] is True
    save_mock.assert_called_once_with("codex-acp", "sk-test123")
