"""
tests/agent/test_capability_framework.py — 能力注册表+涌现决策器+技能提取+图谱同步测试

验证四件事：
  1. 能力注册表：check/install/activate 流程
  2. 涌现决策器：从信号涌现出决策
  3. 技能提取：从簇提取技能 + 用户确认
  4. 图谱同步：导出 → 导入 → 冲突解决
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent.capability_registry import (
    Capability, CapabilityStatus, CapabilityType,
    check_all_capabilities, install_capability, activate_capability,
    get_capability, get_capabilities, get_capability_report_prompt,
)
from agent.capability_evolver import (
    evaluate_capability_emergence, run_emergence_cycle,
    EmergenceSignal, EvolutionDecision,
)
from agent.skill_extractor import (
    SkillExtractor, ExtractedSkill, ensure_skill_tables,
    extract_skills, get_active_skills_prompt, get_pending_skills_prompt,
)
from agent.graph_sync import (
    export_graph, import_graph, export_graph_to_file, import_graph_from_file,
    ensure_graph_tables, GraphExport, ImportResult,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary self-model.db with schema."""
    db_path = tmp_path / "evolution" / "self-model.db"
    db_path.parent.mkdir(parents=True)

    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_preview TEXT,
            result_preview TEXT,
            success INTEGER DEFAULT 1,
            duration REAL DEFAULT 0,
            session_id TEXT DEFAULT '',
            turn_number INTEGER DEFAULT 0,
            cluster_id INTEGER,
            embedding_id INTEGER,
            protected INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT 'unnamed',
            tool_names TEXT DEFAULT '',
            event_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            lifecycle_stage TEXT DEFAULT 'emerging',
            feature_signature TEXT DEFAULT '',
            first_seen TEXT DEFAULT '',
            last_active TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS insights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            cluster_id INTEGER,
            cluster_name TEXT,
            description TEXT,
            severity REAL DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_text TEXT,
            rationale TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()

    return db_path


# ── Test Capability Registry ────────────────────────────────────────────────

class TestCapabilityRegistry:
    """Test capability check/install/activate lifecycle."""

    def test_check_all_capabilities(self):
        """check_all_capabilities returns a report with all registered caps."""
        report = check_all_capabilities()
        assert len(report.capabilities) >= 3
        # skill_extraction and graph_sync are built_in
        skill_cap = report.get_by_name("skill_extraction")
        assert skill_cap is not None
        assert skill_cap.built_in is True

    def test_get_capability(self):
        """get_capability returns by name."""
        cap = get_capability("vector_retrieval")
        assert cap is not None
        assert cap.type == CapabilityType.RETRIEVAL

        assert get_capability("nonexistent") is None

    def test_capability_report_prompt(self):
        """get_capability_report_prompt returns a string block."""
        prompt = get_capability_report_prompt()
        assert "<capability_status>" in prompt
        assert "vector_retrieval" in prompt
        assert "skill_extraction" in prompt

    def test_install_builtin_capability(self):
        """Installing a built-in capability returns success."""
        ok, detail = install_capability("skill_extraction")
        assert ok is True
        assert "built-in" in detail.lower()

    def test_activate_skill_extraction(self, tmp_path):
        """Activating skill_extraction creates tables."""
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            ok, detail = activate_capability("skill_extraction")
            assert ok is True

    def test_activate_graph_sync(self, tmp_path):
        """Activating graph_sync creates tables."""
        with patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}):
            ok, detail = activate_capability("graph_sync")
            assert ok is True


# ── Test Capability Evolver ─────────────────────────────────────────────────

class TestCapabilityEvolver:
    """Test emergence signal evaluation."""

    def test_no_data_returns_empty(self, temp_db):
        """Empty DB → no decisions."""
        decisions = evaluate_capability_emergence(str(temp_db))
        assert decisions == []

    def test_retrieval_bottleneck_emerges(self, temp_db):
        """Enough bottleneck signals → vector_retrieval decision."""
        conn = sqlite3.connect(str(temp_db))

        # Insert 15 self_assessment events: 8 bottleneck, 7 capacity_ok
        # Recent (last 24h) has high bottleneck ratio
        from datetime import datetime, timedelta
        now = datetime.now()

        # Historical baseline (lower bottleneck ratio)
        for i in range(10):
            ts = (now - timedelta(days=5)).isoformat()
            signal = "capacity_ok" if i < 8 else "bottleneck"
            conn.execute(
                "INSERT INTO raw_events (timestamp, tool_name, args_preview, success) VALUES (?, ?, ?, 1)",
                (ts, "__self_assessment__", f"signal={signal}, hit=2/4, clusters=3")
            )

        # Recent (high bottleneck ratio)
        for i in range(5):
            ts = (now - timedelta(hours=2)).isoformat()
            signal = "bottleneck" if i < 4 else "capacity_ok"
            conn.execute(
                "INSERT INTO raw_events (timestamp, tool_name, args_preview, success) VALUES (?, ?, ?, 1)",
                (ts, "__self_assessment__", f"signal={signal}, hit=0/4, clusters=8")
            )

        conn.commit()
        conn.close()

        decisions = evaluate_capability_emergence(str(temp_db))
        # Should have a vector_retrieval decision
        vr_decisions = [d for d in decisions if d.capability_name == "vector_retrieval"]
        assert len(vr_decisions) >= 1
        assert vr_decisions[0].action in ("install", "activate", "monitor")

    def test_skill_repetition_emerges(self, temp_db):
        """Repetitive clusters → skill_extraction decision."""
        conn = sqlite3.connect(str(temp_db))

        # Insert 5 stable clusters, 3 with low tool diversity + high count
        clusters_data = [
            ("git_workflow", "terminal|read_file", 25, "stable"),
            ("python_test", "terminal|read_file", 20, "stable"),
            ("deploy", "terminal|write_file", 18, "stable"),
            ("explore", "terminal|read_file|web_search|write_file|edit", 15, "stable"),
            ("debug", "terminal|read_file|web_search|edit|write_file", 12, "stable"),
        ]
        for name, tools, count, stage in clusters_data:
            conn.execute(
                "INSERT INTO clusters (name, tool_names, event_count, lifecycle_stage, is_active) VALUES (?, ?, ?, ?, 1)",
                (name, tools, count, stage)
            )

        conn.commit()
        conn.close()

        decisions = evaluate_capability_emergence(str(temp_db))
        skill_decisions = [d for d in decisions if d.capability_name == "skill_extraction"]
        assert len(skill_decisions) >= 1

    def test_run_emergence_cycle_updates_signals(self, temp_db):
        """run_emergence_cycle updates emergence_signals in registry."""
        # This test just verifies it runs without error
        decisions = run_emergence_cycle(str(temp_db))
        assert isinstance(decisions, list)


# ── Test Skill Extractor ────────────────────────────────────────────────────

class TestSkillExtractor:
    """Test skill extraction from clusters."""

    def test_extract_from_repetitive_cluster(self, temp_db):
        """Skill is extracted from a cluster with repetitive tool usage."""
        conn = sqlite3.connect(str(temp_db))

        # Create a stable cluster with many events
        conn.execute(
            "INSERT INTO clusters (name, tool_names, event_count, success_count, lifecycle_stage, is_active) "
            "VALUES ('git_workflow', 'terminal|read_file', 20, 18, 'stable', 1)"
        )
        cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Add raw_events for this cluster
        for i in range(20):
            conn.execute(
                "INSERT INTO raw_events (timestamp, tool_name, args_preview, success, cluster_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"2026-07-14T10:{i:02d}:00", "terminal" if i % 2 == 0 else "read_file",
                 f'{{"command": "git commit"}}', 1 if i < 18 else 0, cluster_id)
            )

        conn.commit()
        conn.close()

        # Extract
        extractor = SkillExtractor(str(temp_db))
        skills = extractor.extract()

        assert len(skills) >= 1
        assert skills[0].name == "git_workflow"
        assert skills[0].usage_count == 20
        assert skills[0].status == "pending"

    def test_confirm_skill(self, temp_db):
        """User can confirm a pending skill."""
        conn = sqlite3.connect(str(temp_db))
        ensure_skill_tables(conn)
        conn.execute(
            "INSERT INTO extracted_skills (cluster_id, name, description, status) VALUES (1, 'test_skill', 'desc', 'pending')"
        )
        skill_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        extractor = SkillExtractor(str(temp_db))
        assert extractor.confirm_skill(skill_id) is True

        skills = extractor.list_skills(status="active")
        assert len(skills) == 1
        assert skills[0].name == "test_skill"

    def test_reject_skill(self, temp_db):
        """User can reject a pending skill."""
        conn = sqlite3.connect(str(temp_db))
        ensure_skill_tables(conn)
        conn.execute(
            "INSERT INTO extracted_skills (cluster_id, name, description, status) VALUES (1, 'test_skill', 'desc', 'pending')"
        )
        skill_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        extractor = SkillExtractor(str(temp_db))
        assert extractor.reject_skill(skill_id) is True

        skills = extractor.list_skills(status="rejected")
        assert len(skills) == 1

    def test_active_skills_prompt(self, temp_db):
        """Active skills generate a prompt block."""
        conn = sqlite3.connect(str(temp_db))
        ensure_skill_tables(conn)
        conn.execute(
            "INSERT INTO extracted_skills (cluster_id, name, description, tool_sequence, usage_count, success_rate, status, confirmed_at) "
            "VALUES (1, 'git_flow', 'Git workflow', '[\"terminal\", \"read_file\"]', 15, 0.9, 'active', '2026-07-14')"
        )
        conn.commit()
        conn.close()

        prompt = get_active_skills_prompt(str(temp_db))
        assert "<extracted_skills>" in prompt
        assert "git_flow" in prompt

    def test_pending_skills_prompt(self, temp_db):
        """Pending skills generate a prompt block for user confirmation."""
        conn = sqlite3.connect(str(temp_db))
        ensure_skill_tables(conn)
        conn.execute(
            "INSERT INTO extracted_skills (cluster_id, name, description, usage_count, status) "
            "VALUES (1, 'candidate', 'A candidate skill', 10, 'pending')"
        )
        conn.commit()
        conn.close()

        prompt = get_pending_skills_prompt(str(temp_db))
        assert "<pending_skills>" in prompt
        assert "candidate" in prompt


# ── Test Graph Sync ─────────────────────────────────────────────────────────

class TestGraphSync:
    """Test graph export/import."""

    def test_export_empty_db(self, temp_db):
        """Export from empty DB returns empty but valid GraphJSON."""
        export = export_graph(str(temp_db), source="test@device")
        assert export.version == 1
        assert export.source == "test@device"
        assert export.clusters == []
        assert export.insights == []

    def test_export_with_data(self, temp_db):
        """Export includes clusters and insights."""
        conn = sqlite3.connect(str(temp_db))

        conn.execute(
            "INSERT INTO clusters (name, tool_names, event_count, success_count, lifecycle_stage, feature_signature, is_active) "
            "VALUES ('git', 'terminal', 20, 18, 'stable', 'sig123', 1)"
        )
        conn.execute(
            "INSERT INTO insights (kind, cluster_id, cluster_name, description, severity, is_active) "
            "VALUES ('anti_pattern', 1, 'git', 'dont force push', 0.8, 1)"
        )
        conn.commit()
        conn.close()

        export = export_graph(str(temp_db), source="test@device")
        assert len(export.clusters) == 1
        assert export.clusters[0]["name"] == "git"
        assert len(export.insights) == 1

    def test_export_to_file(self, temp_db, tmp_path):
        """Export to file works."""
        file_path = tmp_path / "graph.json"
        assert export_graph_to_file(str(temp_db), str(file_path), "test") is True
        assert file_path.exists()

        data = json.loads(file_path.read_text())
        assert data["version"] == 1
        assert data["source"] == "test"

    def test_import_and_merge(self, temp_db):
        """Import merges data without duplicates."""
        graph_data = {
            "version": 1,
            "exported_at": "2026-07-14T19:00:00",
            "source": "other@device",
            "clusters": [
                {"name": "imported_cluster", "tool_names": "terminal",
                 "event_count": 10, "success_count": 8,
                 "lifecycle_stage": "stable", "feature_signature": "unique_sig_456",
                 "first_seen": "", "last_active": ""},
            ],
            "insights": [
                {"kind": "strategy", "cluster_id": 1, "cluster_name": "imported",
                 "description": "works well", "severity": 0.7, "created_at": ""},
            ],
            "skills": [
                {"name": "imported_skill", "description": "from other device",
                 "tool_sequence": "[]", "usage_count": 5, "success_rate": 0.9,
                 "confirmed_at": ""},
            ],
            "decisions": [
                {"decision_text": "use python 3.11", "rationale": "best version",
                 "created_at": ""},
            ],
        }

        result = import_graph(str(temp_db), graph_data)
        assert result.clusters_imported == 1
        assert result.insights_imported == 1
        assert result.skills_imported == 1
        assert result.decisions_imported == 1

        # Second import → conflicts resolved (no duplicates)
        result2 = import_graph(str(temp_db), graph_data)
        assert result2.clusters_imported == 0
        assert result2.conflicts_resolved >= 1

    def test_import_from_file(self, temp_db, tmp_path):
        """Import from file works."""
        file_path = tmp_path / "import.json"
        graph_data = {
            "version": 1,
            "source": "file@device",
            "clusters": [],
            "insights": [],
            "skills": [],
            "decisions": [{"decision_text": "test decision", "rationale": "because", "created_at": ""}],
        }
        file_path.write_text(json.dumps(graph_data))
        result = import_graph_from_file(str(temp_db), str(file_path))
        assert result.decisions_imported == 1
