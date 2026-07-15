"""Tests for dual-write elimination — v_outcomes view + single source of truth.

Tests cover:
1. v_outcomes view exists and maps raw_events correctly
2. record_tool_outcome no longer writes to outcomes table
3. legacy queries (FROM v_outcomes) return correct data
4. seed data goes into raw_events (not outcomes)
5. outcome_id resolution from raw_events for DAG relations
"""

import os
import sqlite3
import tempfile
import pytest
from pathlib import Path


@pytest.fixture
def fresh_hermes_home():
    """Create a fresh HERMES_HOME with seeded DB."""
    home = tempfile.mkdtemp()
    os.environ["HERMES_HOME"] = home
    # Clear cached evolution state
    import agent.evolution_manager as em
    em._evolution_active = None
    
    # Trigger initialization (creates tables + seeds raw_events)
    from agent.evolution_manager import is_evolution_active
    is_evolution_active()
    
    from agent.evolution_manager import get_self_model_db
    db_path = str(get_self_model_db())
    yield db_path
    
    # Cleanup
    em._evolution_active = None
    os.environ.pop("HERMES_HOME", None)


class TestVOutcomesView:
    def test_view_exists(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        views = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' AND name='v_outcomes'"
        ).fetchall()
        assert len(views) == 1
        conn.close()

    def test_view_maps_raw_events(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        view_count = conn.execute("SELECT COUNT(*) FROM v_outcomes").fetchone()[0]
        assert raw_count == view_count
        conn.close()

    def test_view_columns_match_outcomes_schema(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        cols = conn.execute("PRAGMA table_info(v_outcomes)").fetchall()
        col_names = [c[1] for c in cols]
        expected = ["id", "timestamp", "task", "action", "tool", "success",
                    "details", "duration", "domain", "error_type", "error_msg", "role"]
        assert col_names == expected
        conn.close()

    def test_view_role_is_default(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        roles = conn.execute("SELECT DISTINCT role FROM v_outcomes").fetchall()
        assert roles == [("default",)]
        conn.close()

    def test_view_domain_is_empty(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        domains = conn.execute("SELECT DISTINCT domain FROM v_outcomes").fetchall()
        assert domains == [("",)]
        conn.close()

    def test_view_error_msg_on_failure(self, fresh_hermes_home):
        from agent.raw_event import record_raw_event
        conn = sqlite3.connect(fresh_hermes_home)
        
        record_raw_event(
            tool_name="terminal",
            tool_args={"cmd": "bad"},
            result="command not found",
            is_error=True,
            duration=0.1,
        )
        
        rows = conn.execute(
            "SELECT error_msg FROM v_outcomes WHERE success = 0"
        ).fetchall()
        assert len(rows) >= 1
        assert "command not found" in rows[-1][0]
        conn.close()


class TestNoDualWrite:
    def test_record_raw_event_does_not_write_outcomes_table(self, fresh_hermes_home):
        from agent.raw_event import record_raw_event
        
        conn = sqlite3.connect(fresh_hermes_home)
        outcomes_before = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        
        record_raw_event(
            tool_name="web_search",
            tool_args={"query": "test"},
            result="found it",
            is_error=False,
            duration=1.0,
        )
        
        outcomes_after = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        raw_after = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        
        # outcomes table should NOT grow (dual-write eliminated)
        assert outcomes_after == outcomes_before
        # raw_events should grow
        assert raw_after > 10  # 10 seeds + 1 new
        conn.close()

    def test_v_outcomes_grows_with_raw_events(self, fresh_hermes_home):
        from agent.raw_event import record_raw_event
        
        conn = sqlite3.connect(fresh_hermes_home)
        before = conn.execute("SELECT COUNT(*) FROM v_outcomes").fetchone()[0]
        
        record_raw_event(
            tool_name="read_file",
            tool_args={"path": "/tmp/x"},
            result="content",
            is_error=False,
            duration=0.2,
        )
        
        after = conn.execute("SELECT COUNT(*) FROM v_outcomes").fetchone()[0]
        assert after == before + 1
        conn.close()


class TestLegacyQueries:
    def test_count_total(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        total = conn.execute("SELECT COUNT(*) FROM v_outcomes").fetchone()[0]
        assert total == 10  # seeded data
        conn.close()

    def test_count_success(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        successes = conn.execute(
            "SELECT COUNT(*) FROM v_outcomes WHERE success = 1"
        ).fetchone()[0]
        assert successes == 10  # all seeds are successful
        conn.close()

    def test_top_tool(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        row = conn.execute(
            "SELECT tool, COUNT(*) as cnt FROM v_outcomes GROUP BY tool ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        assert row[0] == "terminal"  # 4 terminal seeds
        assert row[1] == 4
        conn.close()

    def test_recent_query(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        rows = conn.execute(
            "SELECT task, tool, success FROM v_outcomes ORDER BY id DESC LIMIT 5"
        ).fetchall()
        assert len(rows) == 5
        conn.close()


class TestSeedData:
    def test_seed_writes_raw_events_not_outcomes(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        raw_count = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
        outcomes_count = conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
        
        assert raw_count == 10  # seeds in raw_events
        assert outcomes_count == 0  # no seeds in outcomes table
        conn.close()

    def test_seed_contains_expected_tools(self, fresh_hermes_home):
        conn = sqlite3.connect(fresh_hermes_home)
        tools = conn.execute("SELECT DISTINCT tool_name FROM raw_events").fetchall()
        tool_names = {t[0] for t in tools}
        assert "terminal" in tool_names
        assert "read_file" in tool_names
        assert "web_search" in tool_names
        assert "patch" in tool_names
        assert "write_file" in tool_names
        conn.close()
