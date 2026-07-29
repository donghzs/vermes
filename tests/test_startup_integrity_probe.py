"""G1 startup integrity sentinel — regression tests (c2).

Design: docs/design-startup-integrity-guards-final.md
Covers the §7 c2 assertions:
  * four-state verdict (ok / corrupt / missing_with_profile / fresh_install)
  * plan-a lockdown: SessionDB() RAISES and leaves ZERO bytes on disk
  * probe is home-sourced (audit correction B) and read-only (correction C)
  * custom db_path targets are NOT affected by lockdown
  * empty (0-byte) ledger never whitewashes an incident into "ok"
"""

import sqlite3
from pathlib import Path

import pytest

import vermes_state
from vermes_state import (
    IntegrityLockdownError,
    SessionDB,
    startup_integrity_probe,
)


@pytest.fixture(autouse=True)
def _clean_integrity_state():
    """Every test starts and ends with the sentinel disarmed."""
    vermes_state._reset_integrity_state()
    vermes_state._set_last_init_error(None)
    yield
    vermes_state._reset_integrity_state()
    vermes_state._set_last_init_error(None)


def _make_valid_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Four-state verdict
# ---------------------------------------------------------------------------

def test_fresh_install_on_pristine_home(tmp_path):
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "fresh_install"
    assert not vermes_state.is_integrity_lockdown()


def test_ok_on_valid_ledger(tmp_path):
    _make_valid_db(tmp_path / "state.db")
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "ok"
    assert not vermes_state.is_integrity_lockdown()


def test_corrupt_on_garbage_file(tmp_path):
    (tmp_path / "state.db").write_bytes(b"this is definitely not sqlite" * 64)
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "corrupt"
    assert vermes_state.is_integrity_lockdown()


@pytest.mark.parametrize(
    "trace_setup",
    [
        lambda home: (home / "state.db-wal").write_bytes(b"\x00" * 32),
        lambda home: (home / "config.yaml").write_text("model: x\n"),
        lambda home: (
            (home / "messages").mkdir(),
            (home / "messages" / "s1.json").write_text("{}"),
        ),
        lambda home: (
            (home / "sessions").mkdir(),
            (home / "sessions" / "old.txt").write_text("x"),
        ),
    ],
    ids=["wal-remnant", "config-yaml", "messages-json", "sessions-dir"],
)
def test_missing_with_profile_on_each_trace_signal(tmp_path, trace_setup):
    trace_setup(tmp_path)
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "missing_with_profile"
    assert status["traces"], "trace list must name the signals found"
    assert vermes_state.is_integrity_lockdown()


def test_zero_byte_ledger_with_traces_is_not_whitewashed(tmp_path):
    """A persistent 0-byte state.db must NOT be judged ok — this is exactly
    the plan-b whitewash scenario the lockdown exists to prevent."""
    (tmp_path / "state.db").write_bytes(b"")
    (tmp_path / "config.yaml").write_text("model: x\n")
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "missing_with_profile"
    assert vermes_state.is_integrity_lockdown()


# ---------------------------------------------------------------------------
# Plan-a lockdown behavior
# ---------------------------------------------------------------------------

def test_lockdown_sessiondb_raises_and_leaves_zero_bytes(tmp_path):
    (tmp_path / "messages").mkdir()
    (tmp_path / "messages" / "s.json").write_text("{}")
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "missing_with_profile"

    guarded = tmp_path / "state.db"
    with pytest.raises(IntegrityLockdownError):
        SessionDB(db_path=guarded)

    # THE core promise: "your data was not modified" — no empty DB created.
    assert not guarded.exists(), "lockdown must never create the ledger file"
    # Cause is surfaced for degradation paths (/resume-style messages).
    err = vermes_state.get_last_init_error()
    assert err and "lockdown" in err


def test_lockdown_does_not_affect_custom_db_paths(tmp_path):
    (tmp_path / "config.yaml").write_text("x: 1\n")
    startup_integrity_probe(home=tmp_path)
    assert vermes_state.is_integrity_lockdown()

    other = tmp_path / "elsewhere" / "other.db"
    db = SessionDB(db_path=other)  # must NOT raise
    assert other.exists()
    db._conn.close()


def test_no_lockdown_on_fresh_install_sessiondb_creates_normally(tmp_path):
    startup_integrity_probe(home=tmp_path)
    assert not vermes_state.is_integrity_lockdown()
    db = SessionDB(db_path=tmp_path / "state.db")
    assert (tmp_path / "state.db").exists()
    db._conn.close()


# ---------------------------------------------------------------------------
# Read-only + home-sourcing invariants (audit corrections B & C)
# ---------------------------------------------------------------------------

def test_probe_is_read_only_never_creates_files(tmp_path):
    # Repo-level conftest fixtures may pre-seed tmp_path (gateway_locks etc.);
    # the invariant is the probe adds NOTHING, so compare before/after snapshots.
    before = sorted(p.name for p in tmp_path.iterdir())
    startup_integrity_probe(home=tmp_path)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert after == before, "probe must not create any files/dirs"


def test_probe_does_not_modify_existing_ledger(tmp_path):
    db = tmp_path / "state.db"
    _make_valid_db(db)
    before = db.read_bytes()
    startup_integrity_probe(home=tmp_path)
    assert db.read_bytes() == before, "probe must not write a single byte to the ledger"


def test_probe_default_home_is_get_vermes_home_sourced(tmp_path, monkeypatch):
    """Audit correction B: with no explicit home, the probe must resolve
    get_vermes_home() AT CALL TIME (env override honored), never a stale
    import-time constant."""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    status = startup_integrity_probe()
    assert status["home"] == str(tmp_path)
    assert status["state_db"] == "fresh_install"


def test_large_ledger_uses_header_check_only(tmp_path, monkeypatch):
    """Above the quick_check threshold the probe must still return ok for a
    valid ledger (header/schema_version path)."""
    db = tmp_path / "state.db"
    _make_valid_db(db)
    monkeypatch.setattr(vermes_state, "_INTEGRITY_QUICK_CHECK_MAX_BYTES", 0)
    status = startup_integrity_probe(home=tmp_path)
    assert status["state_db"] == "ok"
