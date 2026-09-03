"""① A2A v1.0 协议 — 类型 / 传输 registry / AgentRegistry 单测。

锁定的核心不变量（对照 §5 ① 规格 7 步）：
  - AgentHandle URI/display/normalize 归一；
  - 消息信封 kind 原生承载三股流动（skill/tool/knowledge）；
  - transport registry 自动发现 local/subprocess/mcp/http 四类 adapter；
  - AgentRegistry 注册/寻址/探活/路由/能力匹配，离线不投递（避免静默丢消息）；
  - 持久化 round-trip（upsert / remove / by_name / list / heartbeat）。
"""

import json
import time

import pytest

from agent.a2a import (
    AgentHandle,
    A2AEnvelope,
    MessageKind,
    normalize_handle,
    parse_mention_handles,
)
from agent.a2a.registry import AgentRegistry
from agent.a2a.transports import get_a2a_transport, list_a2a_transports


# ── 类型层 ──────────────────────────────────────────────────

def test_handle_uri_and_display():
    h = AgentHandle(profile_id="codex-1", name="Codex", provider="openai", model="gpt-4")
    assert h.uri() == "vermes://codex-1"
    assert h.display() == "Codex"
    assert h.to_dict()["capabilities"] == []


def test_normalize_handle():
    assert normalize_handle("vermes://codex-1") == "vermes://codex-1"
    assert normalize_handle("@Codex") == "@Codex"
    assert normalize_handle("Codex") == "@Codex"
    assert normalize_handle("") == ""


def test_parse_mention_handles():
    text = "帮我 @Codex 写代码，再让 @扣子 搜资料"
    assert parse_mention_handles(text) == ["Codex", "扣子"]


def test_message_kind_three_flows():
    # 三股流动：skill / tool / knowledge 是一等语义
    assert MessageKind.SKILL.value == "skill"
    assert MessageKind.TOOL.value == "tool"
    assert MessageKind.KNOWLEDGE.value == "knowledge"
    # 常规三类
    assert MessageKind.MESSAGE.value == "message"
    assert MessageKind.TASK.value == "task"
    assert MessageKind.RESULT.value == "result"


def test_envelope_roundtrip():
    env = A2AEnvelope(
        from_handle="@alice",
        to_handle="@bob",
        kind=MessageKind.KNOWLEDGE.value,
        payload={"decision": "use Postgres"},
        room_id="room-1",
    )
    d = env.to_dict()
    assert d["kind"] == "knowledge"
    assert d["payload"]["decision"] == "use Postgres"
    assert d["room_id"] == "room-1"


# ── 传输 registry ───────────────────────────────────────────

def test_transport_discovery():
    names = list_a2a_transports()
    assert "local" in names
    assert "subprocess" in names
    assert "mcp" in names
    assert "http" in names


def test_transport_instances():
    for name in ("local", "subprocess", "mcp", "http"):
        t = get_a2a_transport(name)
        assert t is not None, f"{name} transport should resolve"
        assert t.name == name


def test_transport_capabilities():
    # 能力标签：写代码→subprocess(code)，搜资料→mcp(tool)
    assert "code" in get_a2a_transport("subprocess").capabilities
    assert "tool" in get_a2a_transport("mcp").capabilities


# ── AgentRegistry（注入内存 db，不碰 ~/.vermes）──────────────────

@pytest.fixture()
def registry(tmp_path):
    """用临时 SQLite 建 AgentRegistry（经 SessionDB）。"""
    from vermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "a2a-test.db")
    return AgentRegistry(db=db)


def _mk(profile_id, name, caps=(), transport="local"):
    return AgentHandle(
        profile_id=profile_id,
        name=name,
        provider="openai",
        model="gpt-4",
        transport=transport,
        capabilities=caps,
    )


def test_register_and_resolve_by_name(registry):
    registry.register(_mk("codex-1", "Codex", caps=("code",)))
    h = registry.resolve("@Codex")
    assert h is not None
    assert h.profile_id == "codex-1"
    assert "code" in h.capabilities


def test_resolve_by_uri(registry):
    registry.register(_mk("codex-1", "Codex", caps=("code",)))
    h = registry.resolve("vermes://codex-1")
    assert h is not None
    assert h.name == "Codex"


def test_resolve_unknown_returns_none(registry):
    assert registry.resolve("@Ghost") is None


def test_heartbeat_and_offline(registry):
    registry.register(_mk("codex-1", "Codex"))
    assert registry.is_alive("codex-1") is True
    # 手动把心跳推旧，模拟已销毁 agent
    db = registry._get_db()
    db._execute_write(lambda c: c.execute(
        "UPDATE a2a_agents SET last_heartbeat = ? WHERE profile_id = ?",
        (time.time() - 99999, "codex-1"),
    ))
    assert registry.is_alive("codex-1") is False
    # 离线 agent 不投递，避免静默丢消息
    assert registry.resolve("@Codex") is None


def test_heartbeat_missing_returns_false(registry):
    assert registry.heartbeat("nope") is False


def test_unregister(registry):
    registry.register(_mk("codex-1", "Codex"))
    registry.unregister("codex-1")
    assert registry.resolve("@Codex") is None


def test_list_and_match_capability(registry):
    registry.register(_mk("codex-1", "Codex", caps=("code",)))
    registry.register(_mk("kouzi-1", "扣子", caps=("search",)))
    agents = registry.list_agents()
    assert len(agents) == 2
    # 能力匹配派活
    code = registry.match("code")
    assert len(code) == 1
    assert code[0].profile_id == "codex-1"
    search = registry.match("search")
    assert len(search) == 1
    assert search[0].name == "扣子"


def test_persistence_roundtrip(registry):
    registry.register(_mk("codex-1", "Codex", caps=("code", "cli"), transport="subprocess"))
    # 新 registry 实例读同一 db（模拟跨进程）
    from agent.a2a.registry import AgentRegistry
    registry2 = AgentRegistry(db=registry._get_db())
    h = registry2.resolve("@Codex")
    assert h is not None
    assert h.transport == "subprocess"
    assert set(h.capabilities) == {"code", "cli"}


def test_upsert_does_not_duplicate(registry):
    registry.register(_mk("codex-1", "Codex", caps=("code",)))
    registry.register(_mk("codex-1", "Codex v2", caps=("code", "cli")))
    agents = registry.list_agents()
    assert len(agents) == 1
    assert agents[0].name == "Codex v2"
