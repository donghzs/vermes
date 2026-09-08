"""⑭ login_probe 单元测试：本机登录态探测（登堂降门槛核心）。

覆盖：家族识别 + 探测函数在 monkeypatch 控制下的各分支。
探测函数依赖文件/CLI 真实环境，测试用 monkeypatch 打桩 _file_exists /
_cli_status / _keychain_has_claude_credential 控制输入。
"""

import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import vermes_cli.a2a.login_probe as lp


def test_match_family():
    assert lp._match_family("claude-agent-acp", "acp-claude-agent") == "claude"
    assert lp._match_family("codex-acp", "acp-codex") == "codex"
    assert lp._match_family("gemini", "acp-gemini") == "gemini"
    assert lp._match_family("copilot", "copilot-acp") == "copilot"
    assert lp._match_family("qwen-code", "acp-qwen-code") is None


def test_probe_login_unknown_family():
    p = lp.probe_login("qwen-code", "acp-qwen-code")
    assert p.logged_in is None
    assert "无登录态探测策略" in p.detail


def test_probe_claude_credential_file(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: path == ".claude/.credentials.json")
    monkeypatch.setattr(lp, "_keychain_has_claude_credential", lambda: False)
    monkeypatch.setattr(lp, "_cli_status", lambda *a, **k: None)
    p = lp.probe_login("claude-agent-acp", "acp-claude-agent")
    assert p.logged_in is True
    assert p.method == "credential file"


def test_probe_claude_keychain(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: False)
    monkeypatch.setattr(lp, "_keychain_has_claude_credential", lambda: True)
    p = lp.probe_login("claude-agent-acp", "acp-claude-agent")
    assert p.logged_in is True
    assert p.method == "keychain"


def test_probe_claude_not_logged_in(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: False)
    monkeypatch.setattr(lp, "_keychain_has_claude_credential", lambda: False)
    monkeypatch.setattr(lp, "_cli_status", lambda *a, **k: False)
    p = lp.probe_login("claude-agent-acp", "acp-claude-agent")
    assert p.logged_in is False
    assert p.method == "cli status"


def test_probe_claude_logged_in_via_cli(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: False)
    monkeypatch.setattr(lp, "_keychain_has_claude_credential", lambda: False)
    monkeypatch.setattr(lp, "_cli_status", lambda *a, **k: True)
    p = lp.probe_login("claude-agent-acp", "acp-claude-agent")
    assert p.logged_in is True


def test_probe_codex_credential_file(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: path == ".codex/auth.json")
    p = lp.probe_login("codex-acp", "acp-codex")
    assert p.logged_in is True
    assert p.method == "credential file"


def test_probe_copilot_gh_hosts(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: path == ".config/gh/hosts.yml")
    p = lp.probe_login("copilot", "copilot-acp")
    assert p.logged_in is True


def test_probe_gemini_oauth(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: path == ".gemini/oauth_creds.json")
    p = lp.probe_login("gemini", "acp-gemini")
    assert p.logged_in is True


def test_probe_gemini_unknown(monkeypatch):
    monkeypatch.setattr(lp, "_file_exists", lambda path: False)
    p = lp.probe_login("gemini", "acp-gemini")
    assert p.logged_in is None


def test_login_command_for():
    assert lp.login_command_for("claude-agent-acp", "acp-claude-agent") == "claude"
    assert lp.login_command_for("codex-acp", "acp-codex") == "codex login"
    assert lp.login_command_for("gemini", "acp-gemini") == "gemini login"
    assert lp.login_command_for("copilot", "copilot-acp") == "gh auth login"
    assert lp.login_command_for("qwen-code", "acp-qwen-code") == ""
