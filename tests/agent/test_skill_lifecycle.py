"""H4.3 测试：技能自进化评测闭环（升/降/淘汰/复活）。

直接驱动 SkillExtractor.evaluate_lifecycle，用临时 db + 受控数据验证四条规则。
anti_pattern 洞察通过 monkeypatch EmergentInsightExtractor.extract 注入。
"""

import json
import sqlite3

import pytest

from agent.skill_extractor import (
    SkillExtractor,
    ensure_skill_tables,
    evaluate_skill_lifecycle,
)


def _reset_evolution_state():
    import agent.evolution_manager as em

    em._evolution_active = None
    import agent.raw_event as re

    re._LAST_EMERGENCE_OK = None


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    """把 evolution DB（含 extracted_skills 与 raw_events）指向临时目录。"""
    from agent.evolution_manager import get_evolution_dir

    d = tmp_path / "hermes"
    d.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(d))
    _reset_evolution_state()
    yield d


def _make_db(tmp_path):
    from agent.evolution_manager import get_evolution_dir, get_self_model_db

    # 技能表与 raw_events 共用同一 evolution DB（HERMES_HOME 已重定向）
    db = get_self_model_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    ensure_skill_tables(conn)
    conn.close()
    return str(db)


def _insert(conn, **kw):
    conn.execute(
        """INSERT INTO extracted_skills
           (cluster_id, name, description, tool_sequence, usage_count,
            success_rate, status, extracted_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            kw.get("cluster_id", 1),
            kw["name"],
            kw.get("description", ""),
            json.dumps(kw.get("tool_sequence", [])),
            kw.get("usage_count", 0),
            kw.get("success_rate", 0.0),
            kw["status"],
            kw.get("extracted_at", ""),
            json.dumps(kw.get("metadata", {})),
        ),
    )
    conn.commit()


def _status(conn, name):
    return conn.execute(
        "SELECT status, metadata FROM extracted_skills WHERE name = ?", (name,)
    ).fetchone()


def test_low_success_active_skill_is_demoted(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _insert(conn, name="flaky", cluster_id=10, status="active",
            usage_count=20, success_rate=0.3)
    conn.close()

    summary = evaluate_skill_lifecycle(db)
    assert summary["demoted"] == 1
    conn = sqlite3.connect(db)
    row = _status(conn, "flaky")
    conn.close()
    assert row[0] == "stale"
    assert json.loads(row[1])["demote_reason"] == "low_success"


def test_high_success_active_skill_is_promoted(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _insert(conn, name="solid", cluster_id=11, status="active",
            usage_count=50, success_rate=0.95)
    conn.close()

    summary = evaluate_skill_lifecycle(db)
    assert summary["promoted"] == 1
    assert summary["demoted"] == 0
    conn = sqlite3.connect(db)
    row = _status(conn, "solid")
    conn.close()
    assert row[0] == "active"  # 晋升不改 status，仅打 proven 标记
    assert json.loads(row[1]).get("grade") == "proven"


def test_anti_pattern_insight_demotes_matching_skill(tmp_path, monkeypatch):
    from agent.emergent_insight import EmergentInsightExtractor, InsightReport, Insight

    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _insert(conn, name="badpat", cluster_id=99, status="active",
            usage_count=30, success_rate=0.9)
    conn.close()

    _report = InsightReport()
    _report.anti_patterns = [
        Insight(kind="anti_pattern", cluster_id=99, cluster_name="x",
                description="fragile pattern", severity=0.9)
    ]
    monkeypatch.setattr(EmergentInsightExtractor, "extract", lambda self: _report)

    summary = evaluate_skill_lifecycle(db)
    assert summary["demoted"] == 1
    conn = sqlite3.connect(db)
    row = _status(conn, "badpat")
    conn.close()
    assert row[0] == "stale"
    assert json.loads(row[1])["demote_reason"] == "anti_pattern"


def test_recovered_stale_skill_is_reactivated(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _insert(conn, name="recovered", cluster_id=12, status="stale",
            usage_count=40, success_rate=0.92)
    conn.close()

    summary = evaluate_skill_lifecycle(db)
    assert summary["reactivated"] == 1
    conn = sqlite3.connect(db)
    row = _status(conn, "recovered")
    conn.close()
    assert row[0] == "active"


def test_lifecycle_records_raw_event(tmp_path):
    """降级决策应写入 raw_events（skill_lifecycle），供后续学习。"""
    import sqlite3 as _sq
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    _insert(conn, name="flaky2", cluster_id=13, status="active",
            usage_count=15, success_rate=0.2)
    conn.close()

    evaluate_skill_lifecycle(db)

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT tool_name, result_preview FROM raw_events WHERE tool_name = 'skill_lifecycle'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "skill_lifecycle"
    assert "demote" in rows[0][1]
