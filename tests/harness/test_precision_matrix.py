"""Tests for H4.2 tool precision matrix (read-only derivation + routing feed-back).

These tests verify:
  * a low-precision tool yields a routing-guidance warning,
  * a high-precision tool yields none,
  * insufficient samples stay silent,
  * get_precision_matrix returns tools sorted by reliability (worst first),
  * the ledger is NOT consulted for reliable tools (perf short-circuit),
  * every failure path is fail-open (returns None / neutral, no exception).
"""

import sqlite3

import pytest

from harness.precision_matrix import (
    ToolPrecision,
    get_precision_matrix,
    get_tool_precision,
    precision_guidance,
)


def _make_db(path, low_tool="flaky", good_tool="solid"):
    """Create a self-model.db-like file with raw_events + v_outcomes view."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE raw_events (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            tool_name TEXT,
            args_preview TEXT,
            result_preview TEXT,
            success INTEGER,
            duration REAL,
            session_id TEXT,
            turn_number INTEGER,
            cluster_id TEXT,
            embedding_id TEXT,
            protected INTEGER
        )"""
    )
    conn.execute(
        """CREATE VIEW v_outcomes AS
            SELECT id, timestamp, tool_name AS task, args_preview AS action,
                   tool_name AS tool, success, result_preview AS details,
                   duration, '' AS domain, '' AS error_type,
                   CASE WHEN success = 0 THEN result_preview ELSE '' END AS error_msg,
                   'default' AS role
            FROM raw_events"""
    )
    rows = []
    # Low-precision tool: 10 calls, only 2 succeed -> 20%.
    for i in range(10):
        rows.append(("t", low_tool, "", "ok" if i < 2 else "err",
                     1 if i < 2 else 0, 1.0, "", 0, "", "", 0))
    # High-precision tool: 10/10 succeed -> 100%.
    for i in range(10):
        rows.append(("t", good_tool, "", "ok", 1, 1.0, "", 0, "", "", 0))
    conn.executemany(
        "INSERT INTO raw_events "
        "(timestamp, tool_name, args_preview, result_preview, success, "
        " duration, session_id, turn_number, cluster_id, embedding_id, protected) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "self-model.db"
    _make_db(path)
    return path


def test_low_precision_yields_guidance(db, monkeypatch):
    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: db
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types",
        lambda tool: {"timeout": 6, "auth_error": 2} if tool == "flaky" else {},
    )

    tp = get_tool_precision("flaky")
    assert tp.total_calls == 10
    assert tp.success_count == 2
    assert tp.success_rate == 0.2
    assert tp.low_precision is True

    guidance = precision_guidance("flaky")
    assert guidance is not None
    assert "flaky" in guidance
    assert "20%" in guidance
    assert "timeout" in guidance


def test_high_precision_no_guidance(db, monkeypatch):
    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: db
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types", lambda tool: {}
    )

    tp = get_tool_precision("solid")
    assert tp.success_rate == 1.0
    assert tp.low_precision is False
    assert precision_guidance("solid") is None


def test_insufficient_samples_stay_silent(tmp_path, monkeypatch):
    path = tmp_path / "self-model.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE raw_events (id INTEGER PRIMARY KEY, timestamp TEXT, "
        "tool_name TEXT, args_preview TEXT, result_preview TEXT, success INTEGER, "
        "duration REAL, session_id TEXT, turn_number INTEGER, cluster_id TEXT, "
        "embedding_id TEXT, protected INTEGER)"
    )
    conn.execute(
        "CREATE VIEW v_outcomes AS SELECT id, timestamp, tool_name AS task, "
        "args_preview AS action, tool_name AS tool, success, result_preview AS "
        "details, duration, '' AS domain, '' AS error_type, '' AS error_msg, "
        "'default' AS role FROM raw_events"
    )
    conn.execute(
        "INSERT INTO raw_events (timestamp, tool_name, args_preview, "
        "result_preview, success, duration, session_id, turn_number, cluster_id, "
        "embedding_id, protected) VALUES ('t','newbie','','err',0,1.0,'',0,'','',0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: path
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types", lambda tool: {}
    )

    assert precision_guidance("newbie") is None
    assert get_tool_precision("newbie").low_precision is False


def test_precision_matrix_sorted_worst_first(db, monkeypatch):
    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: db
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types", lambda tool: {}
    )

    matrix = get_precision_matrix(min_samples=1)
    assert len(matrix) == 2
    assert matrix[0].tool == "flaky"
    assert matrix[0].success_rate == 0.2
    assert matrix[1].tool == "solid"
    assert matrix[1].success_rate == 1.0


def test_no_ledger_io_when_reliable(db, monkeypatch):
    """Reliable tools must short-circuit before touching the FailureLedger."""
    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: db
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types",
        lambda tool: (_ for _ in ()).throw(
            RuntimeError("ledger must not be queried for reliable tools")
        ),
    )
    assert precision_guidance("solid") is None


def test_fail_open_on_db_error(monkeypatch):
    monkeypatch.setattr(
        "harness.precision_matrix._query_outcome_stats",
        lambda tool: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    # Neither entry should raise; both degrade to neutral.
    assert precision_guidance("anything") is None
    assert get_tool_precision("anything").total_calls == 0


def test_missing_db_yields_no_guidance(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.db"
    monkeypatch.setattr(
        "harness.precision_matrix._self_model_db_path", lambda: missing
    )
    monkeypatch.setattr(
        "harness.precision_matrix._query_failure_types", lambda tool: {}
    )
    assert precision_guidance("any-tool") is None
    assert get_precision_matrix() == []
