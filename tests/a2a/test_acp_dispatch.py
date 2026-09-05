"""⑭ ACP dispatch 接线测试（AcpTransport.send）。

背景：① A2A 落地了 AgentRegistry（寻址/路由/探活）+ transport adapter 体系，
但 adapter 的 ``send()`` 全是**透传桩**——不真 spawn，「登堂」后仍无法对话。
本轮接线 ``transports_acp.py``，让 send() 真调
``transport.chat.completions.create()``。

测试纪律：mock 掉 resolve / build_acp_transport / create 三处外部依赖，
**绝不真 spawn 外部命令**（同 test_acp_handshake.py）。

覆盖：
- adapter 注册（get_a2a_transport("acp") 命中，与 register 端点写的
  transport="acp" 对齐——命名不一致会导致 dispatch 静默拿不到 adapter）
- 成功链路：envelope → resolve → recipe → spawn → create → 文本
- 失败链路全部返回 {"error": ...} 而**不抛**（协议层不阻断）
- **无 recipe 必须报错而非静默透传**（假成功比失败更危险）
- payload 文本提取约定（goal → text → message，与 LocalTransport 对齐）
"""

from __future__ import annotations

from unittest import mock

import pytest

from agent.a2a.registry import AgentRegistry
from agent.a2a.transports import get_a2a_transport, list_a2a_transports
from agent.a2a.types import A2AEnvelope, AgentHandle, MessageKind


def _envelope(payload, kind: str = MessageKind.MESSAGE.value, to: str = "@codex-acp"):
    return A2AEnvelope(
        from_handle="vermes://me", to_handle=to, kind=kind, payload=payload
    )


def _fake_transport(reply_text: str = "hello from agent"):
    """假 ACP transport：记录 create 调用，返回 OpenAI 风格响应。"""
    t = mock.MagicMock()
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock(message=mock.MagicMock(content=reply_text))]
    t.chat.completions.create.return_value = resp
    return t


@pytest.fixture
def acp():
    t = get_a2a_transport("acp")
    assert t is not None, "acp adapter 必须已注册（_discover_transports 含 'acp'）"
    return t


# ── adapter 注册 ───────────────────────────────────────────

def test_acp_transport_registered():
    assert "acp" in list_a2a_transports()
    t = get_a2a_transport("acp")
    assert t.name == "acp"
    assert "acp" in t.capabilities


def test_acp_name_matches_register_endpoint_value():
    """register 端点写 transport="acp"，adapter 名必须一致。

    命名不一致会让 dispatch 时 get_a2a_transport 返回 None → 静默透传
    （看起来成功其实没 spawn），属高危假成功。
    """
    import vermes_cli.blueprints.chat as chat_bp  # noqa: F401 - 仅确认可导入
    assert get_a2a_transport("acp") is not None


# ── 成功链路 ──────────────────────────────────────────────

def test_send_dispatches_to_acp_agent(acp):
    """成功：resolve → recipe → build transport → create → 返回文本。"""
    fake = _fake_transport("def hello(): pass")
    handle = AgentHandle(
        profile_id="a2a:acp-codex",
        name="codex-acp",
        transport="acp",
        recipe="codex-acp",
        model="acp-codex",
    )
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle), mock.patch(
        "vermes_cli.a2a.transport.build_acp_transport", return_value=fake
    ) as build:
        out = acp.send(_envelope({"text": "写个 hello world"}))

    assert out == {"transport": "acp", "result": "def hello(): pass"}
    # 关键：真调了 create（这是「dispatch 接线」的本质）
    assert fake.chat.completions.create.called
    assert fake.chat.completions.create.call_args.kwargs["messages"] == [
        {"role": "user", "content": "写个 hello world"}
    ]
    # recipe 被正确传给 build_acp_transport
    assert build.call_args.args[0].name == "codex-acp"


def test_send_task_kind_also_dispatches(acp):
    """TASK 信封同样可 dispatch（不只是 MESSAGE）。"""
    fake = _fake_transport("done")
    handle = AgentHandle(profile_id="p", recipe="codex-acp", transport="acp")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle), mock.patch(
        "vermes_cli.a2a.transport.build_acp_transport", return_value=fake
    ):
        out = acp.send(_envelope({"goal": "重构认证模块"}, kind=MessageKind.TASK.value))
    assert out["result"] == "done"


@pytest.mark.parametrize("key", ["goal", "text", "message"])
def test_payload_text_extraction_convention(acp, key):
    """payload 文本提取顺序 goal → text → message（与 LocalTransport 一致）。"""
    fake = _fake_transport()
    handle = AgentHandle(profile_id="p", recipe="codex-acp", transport="acp")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle), mock.patch(
        "vermes_cli.a2a.transport.build_acp_transport", return_value=fake
    ):
        out = acp.send(_envelope({key: "hi"}))
    assert fake.chat.completions.create.call_args.kwargs["messages"][0]["content"] == "hi"


# ── 失败链路（都返回 error，不抛）───────────────────────────

def test_send_unresolvable_agent(acp):
    """离线 / 未知 agent → 明确报错，不静默丢消息。"""
    with mock.patch.object(AgentRegistry, "resolve", return_value=None):
        out = acp.send(_envelope({"text": "hi"}))
    assert "error" in out
    assert "not resolvable" in out["error"]


def test_send_without_recipe_errors_loudly(acp):
    """🔴 无 recipe（历史行）必须报错，不能静默透传。

    静默透传 = 「以为 dispatch 了其实没 spawn」的假成功，比报错危险得多。
    """
    handle = AgentHandle(profile_id="a2a:old", name="legacy", transport="acp", recipe="")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle):
        out = acp.send(_envelope({"text": "hi"}))
    assert "error" in out
    assert "no recipe" in out["error"]
    # 绝不能看起来像成功
    assert "result" not in out


def test_send_missing_recipe_file(acp):
    """recipe 名有记录但文件已删 → 报错。"""
    handle = AgentHandle(profile_id="p", recipe="nonexistent-recipe", transport="acp")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle):
        out = acp.send(_envelope({"text": "hi"}))
    assert "error" in out
    assert "recipe not found" in out["error"]


def test_send_empty_text(acp):
    handle = AgentHandle(profile_id="p", recipe="codex-acp", transport="acp")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle):
        out = acp.send(_envelope({}))
    assert "error" in out
    assert "empty message text" in out["error"]


def test_send_acp_call_failure_does_not_raise(acp):
    """spawn / 握手 / 对话任一失败 → 返回 error，不抛（协议层不阻断）。"""
    handle = AgentHandle(profile_id="p", recipe="codex-acp", transport="acp")
    failing = mock.MagicMock()
    failing.chat.completions.create.side_effect = RuntimeError("spawn failed: npx died")
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle), mock.patch(
        "vermes_cli.a2a.transport.build_acp_transport", return_value=failing
    ):
        out = acp.send(_envelope({"text": "hi"}))
    assert "error" in out
    assert "acp call failed" in out["error"]
    assert "npx died" in out["error"]


def test_send_malformed_response(acp):
    """响应结构异常 → 报错，不崩。"""
    handle = AgentHandle(profile_id="p", recipe="codex-acp", transport="acp")
    bad = mock.MagicMock()
    bad.chat.completions.create.return_value = mock.MagicMock(choices=[])
    with mock.patch.object(AgentRegistry, "resolve", return_value=handle), mock.patch(
        "vermes_cli.a2a.transport.build_acp_transport", return_value=bad
    ):
        out = acp.send(_envelope({"text": "hi"}))
    assert "error" in out
    assert "malformed" in out["error"]


# ── 非 dispatchable kind → 透传 ────────────────────────────

@pytest.mark.parametrize(
    "kind", [MessageKind.RESULT.value, MessageKind.SKILL.value, MessageKind.TOOL.value]
)
def test_non_dispatchable_kinds_pass_through(acp, kind):
    """RESULT/SKILL/TOOL 等信封 ACP 通路无对应语义 → 透传（与 LocalTransport 一致）。"""
    payload = {"foo": "bar"}
    out = acp.send(_envelope(payload, kind=kind))
    assert out["kind"] == kind
    assert out["payload"] == payload
    assert "error" not in out
