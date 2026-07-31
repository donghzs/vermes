"""Watcher-layer integration test for the P1-3 Outbound Intent Ledger.

These tests drive the *real* handoff-watcher row logic
(``WatcherMixin._process_handoff_row``) against a real ``SessionDB``, with a
stub gateway that supplies just enough surface (``_session_db`` + a faked
``_process_desktop_relay``) to exercise the four ledger transitions:

    pending → claimed → sent        (happy path)
    pending → claimed → failed      (adapter error, NO retry)
    pending → expired               (TTL passed before gateway pickup)
    skipped                          (already claimed by another gateway)

The glue we assert on is exactly the code added in P1-3:
``_handoff_watcher`` now delegates each row to ``_process_handoff_row`` which
calls ``update_outbound_intent`` at the claimed/sent/failed/expired points.
"""

import hashlib
import time

import pytest

from gateway.watcher_mixin import WatcherMixin
from vermes_state import SessionDB


class _StubWatcher(WatcherMixin):
    """Minimal gateway stand-in: real SessionDB, faked relay delivery."""

    def __init__(self, db, *, fail_relay: bool = False):
        self._session_db = db
        self._running = True
        self.adapters: dict = {}
        self._fail_relay = fail_relay

    async def _process_desktop_relay(self, row):
        # The real method builds a MessageEvent + _handle_message; for the
        # watcher-state-machine we only care whether delivery succeeds or raises.
        if self._fail_relay:
            raise RuntimeError("simulated adapter failure")

    async def _process_handoff(self, row):
        # Legacy CLI→channel handoff path; not exercised by relay tests.
        return


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _seed_desktop_relay(db, delivery_id="d1", source="telegram", expire_in=None):
    """Create a session + ledger row + pending desktop relay signal."""
    db.create_session("s1", source=source)
    db.record_outbound_intent(
        delivery_id=delivery_id, session_id="s1", target=source,
        content_hash=hashlib.sha256(b"hi").hexdigest(), intent="desktop_relay",
        status="pending",
    )
    assert db.request_desktop_relay(
        "s1", "hi from desktop", "tok", ttl=300.0, delivery_id=delivery_id
    ) is True
    if expire_in is not None:
        # backdate the TTL so the watcher treats it as already expired
        db._conn.execute(
            "UPDATE sessions SET relay_expire_at = ? WHERE id = 's1'",
            (time.time() + expire_in,),
        )
        db._conn.commit()
    return db.list_pending_handoffs()[0]


@pytest.mark.asyncio
async def test_watcher_marks_ledger_sent_on_success(db):
    row = _seed_desktop_relay(db)
    w = _StubWatcher(db)
    await w._process_handoff_row(row)

    led = db.list_outbound_intents("s1")[0]
    assert led["status"] == "sent"
    # session handoff reached terminal + relay payload cleared after delivery
    assert db.get_session("s1")["handoff_state"] == "completed"
    assert db._conn.execute(
        "SELECT relay_text FROM sessions WHERE id = 's1'"
    ).fetchone()[0] is None


@pytest.mark.asyncio
async def test_watcher_marks_ledger_failed_on_adapter_error(db):
    row = _seed_desktop_relay(db)
    w = _StubWatcher(db, fail_relay=True)
    await w._process_handoff_row(row)

    led = db.list_outbound_intents("s1")[0]
    # failed = adapter claimed but errored; row is KEPT (auditable) and
    # deliberately NOT auto-retried (OpenSquilla outbox unknown semantics).
    assert led["status"] == "failed"
    assert "simulated adapter failure" in (led["error"] or "")
    assert db.get_session("s1")["handoff_state"] == "failed"


@pytest.mark.asyncio
async def test_watcher_marks_ledger_expired_on_ttl_timeout(db):
    # relay never picked up; TTL already in the past → expired, not consumed
    row = _seed_desktop_relay(db, expire_in=-10.0)
    w = _StubWatcher(db)
    await w._process_handoff_row(row)

    led = db.list_outbound_intents("s1")[0]
    assert led["status"] == "expired"
    assert "expired" in (led["error"] or "")
    assert db.get_session("s1")["handoff_state"] == "failed"
    # relay payload cleared so it can't be retried by a later tick
    assert db._conn.execute(
        "SELECT relay_text FROM sessions WHERE id = 's1'"
    ).fetchone()[0] is None


@pytest.mark.asyncio
async def test_watcher_skips_already_claimed_row(db):
    """If another gateway already moved the session out of 'pending',
    claim_handoff fails → the row is skipped and the ledger stays untouched
    (no double-delivery)."""
    db.create_session("done", source="telegram")
    db.record_outbound_intent(
        "ddone", "done", "telegram", hashlib.sha256(b"x").hexdigest(),
        "desktop_relay", status="pending",
    )
    # simulate another gateway having already claimed/completed it
    db._conn.execute("UPDATE sessions SET handoff_state = 'completed' WHERE id = 'done'")
    db._conn.commit()
    row = {
        "id": "done", "relay_source": "desktop",
        "relay_delivery_id": "ddone", "relay_expire_at": None,
    }
    w = _StubWatcher(db)
    await w._process_handoff_row(row)

    # ledger must remain exactly as it was — pending, never advanced
    assert db.list_outbound_intents("done")[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_watcher_legacy_handoff_does_not_touch_ledger(db):
    """A non-desktop (legacy CLI→channel) handoff has no delivery_id, so the
    P1-3 ledger calls must be safe no-ops — the watcher still completes it."""
    db.create_session("leg", source="cli")
    db._conn.execute(
        "UPDATE sessions SET handoff_state = 'pending', relay_source = 'cli' "
        "WHERE id = 'leg'"
    )
    db._conn.commit()
    row = db.list_pending_handoffs()[0]
    assert row["relay_source"] == "cli"

    w = _StubWatcher(db)
    await w._process_handoff_row(row)

    assert db.get_session("leg")["handoff_state"] == "completed"
    # no ledger row was created or touched for the legacy path
    assert db.list_outbound_intents("leg") == []
