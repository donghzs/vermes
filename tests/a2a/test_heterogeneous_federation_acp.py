"""T5：异构 ACP 联邦端到端（3 个不同 agent 经同一 transport 基类接入）。

验证「神魔堂」的核心主张：**异构 agent 用同一套协议登堂**，差异只在
recipe 的 entry_point / args（spawn 谁），协议层完全一致。

三条真实 recipe：
- codex-acp        → npx @agentclientprotocol/codex-acp@1.8.0        （Zed 适配器）
- claude-agent-acp → npx @agentclientprotocol/claude-agent-acp@0.73.0 （Zed 适配器）
- copilot          → copilot --acp --stdio                            （原生 ACP）

断言三件事：
1. 各自 spawn 自己的命令（三条 argv 两两不同，互不干扰）
2. 握手协议一致（initialize → session/new → session/prompt，与 agent 无关）
3. session/update 分块各自正确回填到自己那次调用

全部用 mock subprocess.Popen 模拟 stdio JSON-RPC，不真 spawn npx。
"""

from __future__ import annotations

import json
from unittest import mock

from agent.copilot_acp_client import AcpAgentTransportBase, CopilotACPClient

from vermes_cli.a2a.recipes.loader import RECIPES_DIR, load_all_recipes
from vermes_cli.a2a.transport import build_acp_transport

RECIPE_NAMES = ("codex-acp", "claude-agent-acp", "copilot")

# 各 agent 期望的 spawn argv（取自真实 recipes/*.yaml，非记忆）
EXPECTED_SPAWN = {
    "codex-acp": ["npx", "@agentclientprotocol/codex-acp@1.8.0"],
    "claude-agent-acp": ["npx", "@agentclientprotocol/claude-agent-acp@0.73.0"],
    "copilot": ["copilot", "--acp", "--stdio"],
}


def _fake_proc(stdout_lines, captured_writes):
    proc = mock.MagicMock()
    proc.stdin = mock.MagicMock()
    proc.stdin.write.side_effect = lambda s: captured_writes.append(s)
    proc.stdout = stdout_lines
    proc.stderr = []
    proc.poll.return_value = None
    return proc


def _handshake_lines(reply_text: str, session_id: str = "sess-x") -> list[str]:
    """initialize → session/new → (session/update ×1) → session/prompt 的响应流。"""
    return [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": session_id}}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"text": reply_text},
                    }
                },
            }
        ),
        json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"stopReason": "end_turn"}}),
    ]


def _run_one(recipe, reply_text: str):
    """对一条 recipe 跑一次完整握手，返回 (argv, 写入的请求列表, 回填文本)。"""
    transport = build_acp_transport(recipe)
    writes: list[str] = []
    proc = _fake_proc(_handshake_lines(reply_text), writes)
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ) as popen:
        text, reasoning = transport._run_prompt("hello", timeout_seconds=10)
    argv = popen.call_args.args[0]
    requests = [json.loads(w) for w in writes if w.strip().startswith("{")]
    return argv, requests, text


def _recipes_by_name():
    return {r.name: r for r in load_all_recipes(RECIPES_DIR)}


def test_three_agents_spawn_distinct_commands():
    """(1) 异构联邦：各自 spawn 自己的 CLI，三条 argv 两两不同。"""
    by_name = _recipes_by_name()
    spawns = {}
    for name in RECIPE_NAMES:
        recipe = by_name[name]
        argv, _requests, text = _run_one(recipe, f"reply::{name}")
        spawns[name] = argv
        # (3) 回填：每个 agent 的回复只回到自己那次调用
        assert text == f"reply::{name}", (name, text)

    assert spawns == EXPECTED_SPAWN, spawns
    assert len({tuple(v) for v in spawns.values()}) == 3, "三条 spawn 命令必须互不相同"

    # 细节 2：Codex/Claude 走 Zed npx 适配器，不是裸 codex / claude CLI
    assert spawns["codex-acp"][0] == "npx"
    assert "codex-acp" in spawns["codex-acp"][1]
    assert spawns["claude-agent-acp"][0] == "npx"
    assert "claude-agent-acp" in spawns["claude-agent-acp"][1]
    # Copilot 原生支持 ACP，走裸 CLI
    assert spawns["copilot"][0] == "copilot"


def test_all_agents_share_same_handshake_protocol():
    """(2) 协议一致：三个 agent 的握手方法序列与 clientInfo 完全相同。"""
    by_name = _recipes_by_name()
    seen_methods = []
    seen_versions = []
    seen_client_info = []

    for name in RECIPE_NAMES:
        _argv, requests, _text = _run_one(by_name[name], "ok")
        seen_methods.append([r["method"] for r in requests])
        seen_versions.append(requests[0]["params"]["protocolVersion"])
        seen_client_info.append(requests[0]["params"]["clientInfo"])

    assert all(m == ["initialize", "session/new", "session/prompt"] for m in seen_methods)
    # 协议版本与客户端身份对所有 agent 一致（差异只在 spawn 谁）
    assert len(set(seen_versions)) == 1
    assert all(ci["name"] == "Vermes-agent" for ci in seen_client_info)


def test_copilot_keeps_subclass_others_use_generic_base():
    """类型差异：Copilot 保留弃用守卫子类，Codex/Claude 走泛型基类。"""
    by_name = _recipes_by_name()
    assert isinstance(build_acp_transport(by_name["copilot"]), CopilotACPClient)
    assert type(build_acp_transport(by_name["codex-acp"])) is AcpAgentTransportBase
    assert type(build_acp_transport(by_name["claude-agent-acp"])) is AcpAgentTransportBase
    # 但三者都是同一基类的实例 → 走同一套协议逻辑
    for name in RECIPE_NAMES:
        assert isinstance(build_acp_transport(by_name[name]), AcpAgentTransportBase)
