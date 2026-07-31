"""Tests for stale relay cleanup — gateway crash/restart recovery.

Tests that:
1. request_desktop_relay force-overrides stale 'running' rows (expired)
2. request_desktop_relay still 409s on fresh 'running' rows (not expired)
3. list_pending_handoffs returns stale 'running' rows for watcher cleanup
4. _process_handoff_row fails stale running rows instead of re-processing
"""
import pytest
import time
import sqlite3
from unittest.mock import AsyncMock, MagicMock


def _make_db(tmp_path):
    """Create a real SessionDB with a test database."""
    import sys
    from pathlib import Path
    sys.path.insert(0, ".")
    from vermes_state import SessionDB
    db = SessionDB(Path(tmp_path / "test_state.db"))
    # Ensure origin columns
    db._ensure_session_origin_columns(db._conn)
    # Insert a test session
    db._conn.execute(
        "INSERT INTO sessions (id, started_at, source) "
        "VALUES (?, ?, ?)",
        ("test-sess-1", int(time.time()), "feishu"),
    )
    db._conn.commit()
    return db


class TestStaleRelayCleanup:
    """Stale running relay rows should be cleaned up, not block forever."""

    def test_request_relay_overrides_stale_running(self, tmp_path):
        """A running relay past its expire_at should be overridden by a new request."""
        db = _make_db(tmp_path)
        # Simulate a stuck 'running' relay (expired 60s ago)
        db._conn.execute(
            "UPDATE sessions SET handoff_state='running', relay_source='desktop', "
            "relay_expire_at=? WHERE id=?",
            (time.time() - 60, "test-sess-1"),
        )
        db._conn.commit()
        # New relay request should succeed (override)
        ok = db.request_desktop_relay("test-sess-1", "hello", "tok", ttl=300)
        assert ok, "stale running relay should be overridden"
        # Verify state is now pending
        row = db._conn.execute(
            "SELECT handoff_state FROM sessions WHERE id=?", ("test-sess-1",)
        ).fetchone()
        assert row["handoff_state"] == "pending"

    def test_request_relay_rejects_fresh_running(self, tmp_path):
        """A running relay not yet expired should still 409."""
        db = _make_db(tmp_path)
        # Simulate an active 'running' relay (expires in 300s)
        db._conn.execute(
            "UPDATE sessions SET handoff_state='running', relay_source='desktop', "
            "relay_expire_at=? WHERE id=?",
            (time.time() + 300, "test-sess-1"),
        )
        db._conn.commit()
        ok = db.request_desktop_relay("test-sess-1", "hello", "tok", ttl=300)
        assert not ok, "fresh running relay should NOT be overridden"

    def test_list_pending_handoffs_includes_stale_running(self, tmp_path):
        """list_pending_handoffs should return stale running rows for watcher cleanup."""
        db = _make_db(tmp_path)
        db._conn.execute(
            "UPDATE sessions SET handoff_state='running', relay_source='desktop', "
            "relay_expire_at=? WHERE id=?",
            (time.time() - 60, "test-sess-1"),
        )
        db._conn.commit()
        pending = db.list_pending_handoffs()
        assert len(pending) == 1
        assert pending[0]["id"] == "test-sess-1"
        assert pending[0]["handoff_state"] == "running"

    def test_list_pending_handoffs_excludes_fresh_running(self, tmp_path):
        """Fresh running rows should NOT be in list_pending_handoffs."""
        db = _make_db(tmp_path)
        db._conn.execute(
            "UPDATE sessions SET handoff_state='running', relay_source='desktop', "
            "relay_expire_at=? WHERE id=?",
            (time.time() + 300, "test-sess-1"),
        )
        db._conn.commit()
        pending = db.list_pending_handoffs()
        assert len(pending) == 0

    def test_list_pending_handoffs_includes_pending(self, tmp_path):
        """Normal pending rows should still be listed."""
        db = _make_db(tmp_path)
        db._conn.execute(
            "UPDATE sessions SET handoff_state='pending', relay_source='desktop', "
            "relay_expire_at=? WHERE id=?",
            (time.time() + 300, "test-sess-1"),
        )
        db._conn.commit()
        pending = db.list_pending_handoffs()
        assert len(pending) == 1
        assert pending[0]["handoff_state"] == "pending"


class TestProcessHandoffRowStaleRunning:
    """_process_handoff_row should fail stale running rows, not re-process them."""

    @pytest.mark.asyncio
    async def test_stale_running_is_failed_not_claimed(self):
        """A stale running row should be marked failed, not re-claimed."""
        from gateway.watcher_mixin import WatcherMixin
        runner = MagicMock(spec=WatcherMixin)
        runner._session_db = MagicMock()
        runner._session_db.fail_handoff = MagicMock()
        runner._session_db.clear_desktop_relay = MagicMock()
        runner._session_db.update_outbound_intent = MagicMock()
        runner._session_db.claim_handoff = MagicMock(return_value=True)
        runner._process_desktop_relay = AsyncMock()
        # A stale running row
        row = {
            "id": "sess-1",
            "handoff_state": "running",
            "relay_source": "desktop",
            "relay_expire_at": time.time() - 60,
            "relay_delivery_id": "del-1",
            "relay_text": "hello",
        }
        await WatcherMixin._process_handoff_row(runner, row)
        # Should have called fail_handoff, not claim_handoff or _process_desktop_relay
        runner._session_db.fail_handoff.assert_called_once_with(
            "sess-1", "stale running relay (gateway restart?)"
        )
        runner._session_db.clear_desktop_relay.assert_called_once_with("sess-1")
        runner._session_db.claim_handoff.assert_not_called()
        runner._process_desktop_relay.assert_not_called()
