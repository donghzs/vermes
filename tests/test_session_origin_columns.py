import sqlite3
import tempfile
from pathlib import Path

from vermes_state import SessionDB


def test_lazy_origin_columns_created_on_channel_write():
    """§3.5: opening a legacy DB must NOT add channel columns, but writing a
    channel session must lazily ALTER them in and persist the values."""
    d = Path(tempfile.mktemp(suffix=".db"))
    con = sqlite3.connect(d)
    con.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (11);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            user_id TEXT,
            model TEXT,
            model_config TEXT,
            system_prompt TEXT,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            tool_call_count INTEGER DEFAULT 0,
            title TEXT
        );
        """
    )
    con.close()

    db = SessionDB(db_path=d)
    # After open, legacy DB must remain un-mutated (rollback-safety).
    cur = db._conn.execute('PRAGMA table_info("sessions")')
    cols = {r[1] for r in cur.fetchall()}
    assert "chat_id" not in cols and "origin_json" not in cols

    sid = "s1"
    db.create_session(
        sid, "telegram", chat_id="123", chat_type="dm",
        display_name="Bob", session_key="k",
        origin_json='{"platform":"telegram","chat_id":"123"}',
    )
    row = db.get_session(sid)
    assert row["chat_id"] == "123", row
    assert row["origin_json"] == '{"platform":"telegram","chat_id":"123"}', row

    # relay path also lazily ensures its columns
    assert db.request_desktop_relay(sid, "hi", "tok", 300.0) is True
    st = db.get_desktop_relay_state(sid)
    assert st["state"] == "pending", st
    db.clear_desktop_relay(sid)
    assert db.get_desktop_relay_state(sid)["state"] == "pending"
