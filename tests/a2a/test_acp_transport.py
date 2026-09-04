"""T2 tests: build_acp_transport + Codex-acp 端到端握手（mock subprocess.Popen）。

不真正 spawn npx codex-acp；用 MagicMock 模拟 stdio JSON-RPC：
- 注入 initialize / session/new / session/update* / session/prompt 的响应行
- 断言：spawn argv == recipe.spawn_command（Zed 适配器 npx 包）
- 断言：握手方法顺序 initialize → session/new → session/prompt
- 断言：session/update 的 agent_message_chunk 被回收到 prompt 文本
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

from agent.copilot_acp_client import AcpAgentTransportBase, CopilotACPClient

from vermes_cli.a2a.recipes.loader import RECIPES_DIR, find_recipe
from vermes_cli.a2a.transport import build_acp_transport


def _fake_proc(stdout_lines, captured_writes):
    """A MagicMock stand-in for subprocess.Popen with a real iterable stdout."""
    proc = mock.MagicMock()
    proc.stdin = mock.MagicMock()
    proc.stdin.write.side_effect = lambda s: captured_writes.append(s)
    proc.stdout = stdout_lines  # `for line in proc.stdout` iterates a list
    proc.stderr = []
    proc.poll.return_value = None  # 进程在整个握手期间保持存活
    return proc


def _codex_handshake_lines() -> list[str]:
    return [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "sess-codex-1"}}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": "Hello "},
                    }
                },
            }
        ),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": "Codex"},
                    }
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}),
    ]


def test_build_codex_acp_uses_zed_adapter_and_handshake():
    recipe = find_recipe("codex-acp", RECIPES_DIR)
    assert recipe is not None
    assert recipe.entry_point == "npx"
    assert recipe.args == ["@agentclientprotocol/codex-acp@1.8.0"]

    transport = build_acp_transport(recipe)
    assert isinstance(transport, AcpAgentTransportBase)
    assert not isinstance(transport, CopilotACPClient)  # 新 agent 走泛型基类

    captured_writes: list[str] = []
    fake_proc = _fake_proc(_codex_handshake_lines(), captured_writes)

    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=fake_proc
    ) as popen:
        text, reasoning = transport._run_prompt("hi codex", timeout_seconds=10)

    # (1) spawn argv == recipe.spawn_command（Zed 适配器 npx 包，非裸 codex CLI）
    popen.assert_called_once()
    argv = popen.call_args.args[0]
    assert argv == recipe.spawn_command == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]

    # (2) 握手方法顺序：initialize → session/new → session/prompt
    written = [json.loads(w) for w in captured_writes if w.strip().startswith("{")]
    assert [m["method"] for m in written] == ["initialize", "session/new", "session/prompt"]

    # session/new 携带 cwd + 空 mcpServers；session/prompt 携带 prompt 文本
    assert written[1]["params"]["cwd"]
    assert written[2]["params"]["prompt"][0]["type"] == "text"
    assert written[2]["params"]["prompt"][0]["text"] == "hi codex"

    # (3) session/update 的 agent_message_chunk 被回收到 prompt 文本
    assert text == "Hello Codex"
    assert reasoning == ""


def test_build_acp_transport_copilot_recipe_returns_copilot_subclass():
    recipe = find_recipe("copilot", RECIPES_DIR)
    assert recipe is not None
    transport = build_acp_transport(recipe)
    # provider == copilot-acp → 保留弃用守卫的 CopilotACPClient
    assert isinstance(transport, CopilotACPClient)
    assert transport._acp_command == "copilot"
    assert transport._acp_args == ["--acp", "--stdio"]


def test_build_acp_transport_rejects_non_acp():
    import pytest

    # 真实 ACP recipe 应能正常构建
    good = find_recipe("codex-acp", RECIPES_DIR)
    assert good is not None
    build_acp_transport(good)

    # 伪造一个非 ACP transport 的 recipe → 应被拒绝
    bad = SimpleNamespace(
        name="x",
        transport="mcp",
        entry_point="x",
        args=[],
        is_acp=False,
        provider="acp-x",
    )
    with pytest.raises(ValueError):
        build_acp_transport(bad)  # type: ignore[arg-type]
