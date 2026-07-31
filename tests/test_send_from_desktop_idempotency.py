"""P3 exactly-once 幂等：send-from-desktop 桥对同一 delivery_id 只 relay 一次。

根治「响应丢失后前端退避重试 → 后端真执行两次 relay → 渠道重复回复」。
前端（A.4.4）重试时复用同一 delivery_id；本测试验证后端据此去重。
（项目未启用 pytest-asyncio，故用 asyncio.run 驱动协程。）
"""

import asyncio
import hashlib

import pytest

from vermes_state import SessionDB


class _FakeRequest:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._body


@pytest.fixture
def relay_counter(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("chan-s1", "telegram")
    db.close()
    # 每个 endpoint 调用内部会 new SessionDB()，统一指向同一个测试库文件，
    # 使 ledger 行在多次调用间持久化（幂等查重依赖此）。
    monkeypatch.setattr(
        "vermes_state.SessionDB",
        lambda *a, **k: SessionDB(tmp_path / "state.db"),
    )
    # request_desktop_relay 在单元层面只计数，不真正连 gateway
    calls = {"n": 0}

    def _fake_relay(self, sid, text, token, ttl=300.0, delivery_id=None):
        calls["n"] += 1
        return True

    monkeypatch.setattr(SessionDB, "request_desktop_relay", _fake_relay)
    return calls  # 返回计数器，测试里断言 relay 次数


def test_same_delivery_id_relays_exactly_once(relay_counter):
    from vermes_cli.blueprints.session import send_from_desktop

    res1 = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-abc"})))
    assert res1["ok"] is True
    assert res1["delivery_id"] == "dlv-abc"
    assert "idempotent" not in res1  # 首次不是幂等返回

    # 前端重试：同一 delivery_id 再次 POST（响应丢失后重试）
    res2 = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-abc"})))
    assert res2["ok"] is True
    assert res2["idempotent"] is True  # 命中既有 ledger，直接 ack
    # 首次 relay 成功后 ledger 仍为 pending（单元层无 watcher 推进）→ 非终态标记
    assert res2["terminal"] is False
    assert res2["state"] == "pending"

    # 关键：只 relay 了一次
    assert relay_counter["n"] == 1


def test_idempotent_terminal_flag_on_failed(tmp_path, monkeypatch):
    from vermes_cli.blueprints.session import send_from_desktop

    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("chan-s1", "telegram")
    db.close()
    monkeypatch.setattr(
        "vermes_state.SessionDB",
        lambda *a, **k: SessionDB(tmp_path / "state.db"),
    )
    calls = {"n": 0}

    def _fake_relay(self, sid, text, token, ttl=300.0, delivery_id=None):
        calls["n"] += 1
        return True

    monkeypatch.setattr(SessionDB, "request_desktop_relay", _fake_relay)

    # 首次 relay 成功 → ledger pending（首次返回无 terminal 键）
    res1 = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-t"})))
    assert res1["ok"] is True
    assert "terminal" not in res1

    # 模拟 watcher 将 ledger 置为 failed（含 error）
    db2 = SessionDB(tmp_path / "state.db")
    db2.update_outbound_intent("dlv-t", status="failed", error="gateway down")
    db2.close()

    # 前端重试：命中 failed ledger → 返回 terminal=True + error，且不二次 relay
    res2 = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-t"})))
    assert res2["ok"] is True
    assert res2["idempotent"] is True
    assert res2["terminal"] is True
    assert res2["state"] == "failed"
    assert res2["error"] == "gateway down"
    assert calls["n"] == 1  # 仍 exactly-once


def test_idempotent_terminal_flag_on_sent(tmp_path, monkeypatch):
    from vermes_cli.blueprints.session import send_from_desktop

    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("chan-s1", "telegram")
    db.close()
    monkeypatch.setattr(
        "vermes_state.SessionDB",
        lambda *a, **k: SessionDB(tmp_path / "state.db"),
    )
    calls = {"n": 0}

    def _fake_relay(self, sid, text, token, ttl=300.0, delivery_id=None):
        calls["n"] += 1
        return True

    monkeypatch.setattr(SessionDB, "request_desktop_relay", _fake_relay)

    asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-s"})))
    db2 = SessionDB(tmp_path / "state.db")
    db2.update_outbound_intent("dlv-s", status="sent", error=None)
    db2.close()

    res2 = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-s"})))
    assert res2["ok"] is True
    assert res2["idempotent"] is True
    assert res2["terminal"] is True
    assert res2["state"] == "sent"
    assert calls["n"] == 1


def test_different_delivery_id_relays_twice(relay_counter):
    from vermes_cli.blueprints.session import send_from_desktop

    asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-1"})))
    asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi", "delivery_id": "dlv-2"})))
    assert relay_counter["n"] == 2  # 不同 ID 各自 relay


def test_missing_delivery_id_server_generates(relay_counter):
    from vermes_cli.blueprints.session import send_from_desktop

    res = asyncio.run(send_from_desktop("chan-s1", _FakeRequest({"text": "hi"})))
    assert res["ok"] is True
    assert isinstance(res["delivery_id"], str) and len(res["delivery_id"]) > 0
    assert relay_counter["n"] == 1


def test_get_outbound_intent_roundtrip(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.record_outbound_intent(
        delivery_id="d-x", session_id="s-x", target="qq",
        content_hash=hashlib.sha256(b"x").hexdigest(),
        intent="desktop_relay", status="pending",
    )
    row = db.get_outbound_intent("d-x")
    assert row is not None
    assert row["delivery_id"] == "d-x"
    assert row["status"] == "pending"
    assert db.get_outbound_intent("nonexistent") is None
    db.close()
