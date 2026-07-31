"""P1-3 Outbound Intent Ledger — unit tests.

Covers the SQLite-backed delivery ledger that closes the “desktop relay
stuck in pending forever” blind spot (the QQ 代发 Failed to fetch 之后前端
只能干等超时的根因之一).

Design contract (borrowed from OpenSquilla's outbox, adapted to single-node):
- A ledger row is written *before* the gateway picks the relay up (state=pending).
- The gateway watcher advances it: pending → claimed → sent | failed | expired.
- ``failed`` / ``expired`` are terminal and are **NOT** auto-retried, so a
  flaky channel cannot produce duplicate sends.
"""

import hashlib

import pytest

from vermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _record(db, delivery_id="d1", session_id="s1", status="pending"):
    return db.record_outbound_intent(
        delivery_id=delivery_id,
        session_id=session_id,
        target="telegram",
        content_hash=hashlib.sha256(b"hello").hexdigest(),
        intent="desktop_relay",
        status=status,
    )


# ── record ─────────────────────────────────────────────────────

def test_record_returns_true(db):
    assert _record(db) is True


def test_record_persists_all_fields(db):
    ch = hashlib.sha256(b"payload").hexdigest()
    db.record_outbound_intent(
        delivery_id="d-x", session_id="s-x", target="qq",
        content_hash=ch, intent="desktop_relay", status="pending",
    )
    rows = db.list_outbound_intents("s-x")
    assert len(rows) == 1
    r = rows[0]
    assert r["delivery_id"] == "d-x"
    assert r["session_id"] == "s-x"
    assert r["target"] == "qq"
    assert r["content_hash"] == ch
    assert r["intent"] == "desktop_relay"
    assert r["status"] == "pending"
    assert r["provider_msg_id"] is None
    assert r["error"] is None
    assert r["created_at"] is not None
    assert r["updated_at"] is not None


# ── state machine ──────────────────────────────────────────────

def test_update_advances_pending_to_sent(db):
    _record(db)
    assert db.update_outbound_intent("d1", status="claimed") is True
    assert db.update_outbound_intent("d1", status="sent", provider_msg_id="ext-123") is True
    r = db.list_outbound_intents("s1")[0]
    assert r["status"] == "sent"
    assert r["provider_msg_id"] == "ext-123"


def test_update_claimed_then_failed_keeps_row(db):
    """Terminal failure must keep the row (so it is auditable) — NOT delete it
    and NOT be re-driven by any retry loop."""
    _record(db)
    db.update_outbound_intent("d1", status="claimed")
    assert db.update_outbound_intent("d1", status="failed", error="adapter 500") is True
    rows = db.list_outbound_intents("s1")
    assert len(rows) == 1  # not deleted
    assert rows[0]["status"] == "failed"
    assert rows[0]["error"] == "adapter 500"
    # A second update must be a no-op re-statement, never a silent re-send.
    assert db.update_outbound_intent("d1", status="sent") is True  # allowed but...
    assert db.list_outbound_intents("s1")[0]["status"] == "sent"   # ...explicit only


def test_expired_state_keeps_row(db):
    _record(db)
    assert db.update_outbound_intent("d1", status="expired", error="ttl passed") is True
    rows = db.list_outbound_intents("s1")
    assert len(rows) == 1
    assert rows[0]["status"] == "expired"
    assert rows[0]["error"] == "ttl passed"


def test_update_unknown_delivery_id_returns_false(db):
    assert db.update_outbound_intent("nope", status="sent") is False
    assert db.list_outbound_intents("s1") == []


# ── list / ordering ────────────────────────────────────────────

def test_list_returns_most_recent_first_and_limits(db):
    for i in range(5):
        db.record_outbound_intent(
            delivery_id=f"d{i}", session_id="s1", target="qq",
            content_hash="x", intent="desktop_relay",
        )
    rows = db.list_outbound_intents("s1", limit=3)
    assert len(rows) == 3
    # created_at DESC → delivery ids in reverse insertion order
    assert [r["delivery_id"] for r in rows] == ["d4", "d3", "d2"]


def test_list_scoped_by_session(db):
    db.record_outbound_intent("da", "sA", "qq", "x", "desktop_relay")
    db.record_outbound_intent("db", "sB", "qq", "x", "desktop_relay")
    assert [r["delivery_id"] for r in db.list_outbound_intents("sA")] == ["da"]
    assert [r["delivery_id"] for r in db.list_outbound_intents("sB")] == ["db"]


# ── integration with request_desktop_relay ─────────────────────

def test_request_desktop_relay_persists_delivery_id(db):
    """The relay signal and the ledger share one delivery_id, so the gateway
    watcher can correlate the in-flight session row to its ledger entry.

    (In production the ledger row is written by the API layer *before* calling
    request_desktop_relay; here we simulate that ordering.)
    """
    db.create_session("s-relay", source="telegram")
    db.record_outbound_intent(
        delivery_id="led-1", session_id="s-relay", target="telegram",
        content_hash=hashlib.sha256(b"hi").hexdigest(), intent="desktop_relay",
        status="pending",
    )
    # handoff_state starts NULL → relay accepted, delivery_id attached to session
    assert db.request_desktop_relay(
        "s-relay", "hi from desktop", "tok", ttl=300.0, delivery_id="led-1"
    ) is True
    pending = db.list_pending_handoffs()
    assert len(pending) == 1
    assert pending[0]["relay_delivery_id"] == "led-1"
    # ledger side still has the pending row, same id (correlatable)
    led = db.list_outbound_intents("s-relay")
    assert len(led) == 1
    assert led[0]["delivery_id"] == "led-1"
    assert led[0]["status"] == "pending"
    assert led[0]["target"] == "telegram"


def test_relay_rejected_when_in_flight_keeps_clean_ledger(db):
    db.create_session("s-busy", source="qq")
    db.record_outbound_intent(
        delivery_id="d-busy", session_id="s-busy", target="qq",
        content_hash="x", intent="desktop_relay",
    )
    assert db.request_desktop_relay("s-busy", "m1", "tok", delivery_id="d-busy") is True
    # second relay while first is pending (handoff_state='pending') is rejected
    assert db.request_desktop_relay("s-busy", "m2", "tok", delivery_id="d-busy2") is False
    # only one ledger row for the session (the rejected relay must not have written one)
    assert [r["delivery_id"] for r in db.list_outbound_intents("s-busy")] == ["d-busy"]
