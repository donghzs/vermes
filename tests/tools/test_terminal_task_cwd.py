"""Regression tests for task/session cwd propagation in terminal_tool."""

import json
import os

import tools.terminal_tool as terminal_tool


def test_cd_persists_across_foreground_calls(monkeypatch):
    """Without an explicit workdir or ACP task-cwd override, a `cd` issued in
    one foreground call must persist into the next (Hermes parity).

    Regresses the bug where terminal_tool passed the config-default cwd on
    every call, which reset the working directory each time and made `cd`
    silently no-op across calls.
    """
    task_id = "cd-persist-test"
    monkeypatch.setattr(terminal_tool, "_active_environments", {})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {})
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type: {"approved": True},
    )

    try:
        out1 = json.loads(
            terminal_tool.terminal_tool(
                command="cd /tmp && pwd", task_id=task_id, timeout=20
            )
        )
        assert out1["exit_code"] == 0
        out2 = json.loads(
            terminal_tool.terminal_tool(command="pwd", task_id=task_id, timeout=20)
        )
        # macOS resolves /tmp -> /private/tmp; either form proves persistence.
        assert out2["output"].strip().endswith("/tmp")
    finally:
        env = terminal_tool._active_environments.get(task_id)
        if env is not None:
            try:
                env.cleanup()
            except Exception:
                pass


def _minimal_terminal_config(cwd="/default"):
    return {
        "env_type": "local",
        "cwd": cwd,
        "timeout": 60,
    }


def test_foreground_command_uses_registered_task_cwd_for_existing_environment(monkeypatch):
    """ACP can update task cwd after the local env exists; foreground must honor it."""
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type: {"approved": True},
    )

    result = json.loads(terminal_tool.terminal_tool(command="pwd", task_id=task_id))

    assert result["exit_code"] == 0
    assert calls == [("pwd", {"timeout": 60, "cwd": "/workspace/acp"})]


def test_explicit_workdir_still_wins_over_registered_task_cwd(monkeypatch):
    calls = []

    class FakeEnv:
        env = {}

        def execute(self, command, **kwargs):
            calls.append(kwargs)
            return {"output": "ok", "returncode": 0}

    task_id = "acp-session-1"
    monkeypatch.setattr(terminal_tool, "_active_environments", {task_id: FakeEnv()})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(terminal_tool, "_task_env_overrides", {task_id: {"cwd": "/workspace/acp"}})
    monkeypatch.setattr(terminal_tool, "_get_env_config", lambda: _minimal_terminal_config())
    monkeypatch.setattr(
        terminal_tool,
        "_check_all_guards",
        lambda command, env_type: {"approved": True},
    )

    result = json.loads(
        terminal_tool.terminal_tool(
            command="pwd",
            task_id=task_id,
            workdir="/explicit/workdir",
        )
    )

    assert result["exit_code"] == 0
    assert calls == [{"timeout": 60, "cwd": "/explicit/workdir"}]
