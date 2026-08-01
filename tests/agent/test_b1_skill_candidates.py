"""B1 涌现式重构：clusters 表无 tool_names 列，原 SQL 查死列→恒报错被吞→自学习出口断。

修复后改用 clusters 表自身已填充的涌现字段（event_count + success_rate +
lifecycle_stage）做技能候选门槛，移除 tool_diversity 硬编码启发式。

测试用临时 self-model.db（真实 17 列 schema），验证涌现门槛行为正确。
"""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import skill_extractor  # noqa: E402
from agent import capability_evolver  # noqa: E402

_CLUSTERS_DDL = """
CREATE TABLE IF NOT EXISTS clusters (
    id INTEGER PRIMARY KEY,
    name TEXT,
    feature_signature TEXT,
    event_count INTEGER,
    success_count INTEGER,
    error_count INTEGER,
    total_duration REAL,
    first_seen TEXT,
    last_seen TEXT,
    last_active_at TEXT,
    success_rate REAL,
    avg_duration REAL,
    is_active INTEGER,
    lifecycle_stage TEXT,
    parent_cluster_id INTEGER,
    evolved_from TEXT,
    created_at TEXT
)
"""


def _make_db(rows):
    """rows: list of dict with cluster fields. 返回 (db_path, conn)。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_CLUSTERS_DDL)
    for r in rows:
        conn.execute(
            """INSERT INTO clusters
               (name, feature_signature, event_count, success_count, error_count,
                total_duration, first_seen, last_seen, last_active_at, success_rate,
                avg_duration, is_active, lifecycle_stage, parent_cluster_id, evolved_from, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r.get("name", "c"),
                r.get("feature_signature", "sig"),
                r.get("event_count", 0),
                r.get("success_count", 0),
                r.get("error_count", 0),
                0.0,
                "t",
                "t",
                "t",
                r.get("success_rate", 0.0),
                0.0,
                r.get("is_active", 1),
                r.get("lifecycle_stage", "stable"),
                r.get("parent_cluster_id"),
                r.get("evolved_from"),
                "t",
            ),
        )
    conn.commit()
    return path, conn


def _stable(event_count, success_rate, name="c"):
    return {"name": name, "event_count": event_count, "success_rate": success_rate,
            "lifecycle_stage": "stable"}


def test_skill_candidates_emergent_gate():
    path, conn = _make_db([
        _stable(20, 0.95, "good_stable"),     # 涌现门槛命中
        _stable(20, 0.50, "low_success"),      # success_rate 低于涌现下限
        {"name": "dead", "event_count": 20, "success_rate": 0.95,
         "lifecycle_stage": "dead"},           # 非 stable
        _stable(3, 0.99, "too_few"),           # event_count < 5
        _stable(10, 0.99, "__self_assessment__"),  # 系统自噬簇应被排除
    ])
    try:
        inst = skill_extractor.SkillExtractor(path)
        cands = inst._find_skill_candidates(conn)
        names = {c["name"] for c in cands}
        assert names == {"good_stable"}, f"仅 good_stable 应通过涌现门槛，实际: {names}"
        assert "__self_assessment__" not in names, "系统自噬簇不应进入技能候选"
    finally:
        conn.close()
        Path(path).unlink()


def test_skill_candidates_no_tool_names_column_reference():
    # 重构后不应再引用不存在的 tool_names 列（否则 OperationalError 被吞→空）
    path, conn = _make_db([_stable(20, 0.9, "x")])
    try:
        inst = skill_extractor.SkillExtractor(path)
        # 若仍查 tool_names，会因 OperationalError 被 except 吞掉返回 []；
        # 断言确实返回候选，说明没查死列
        cands = inst._find_skill_candidates(conn)
        assert any(c["name"] == "x" for c in cands)
    finally:
        conn.close()
        Path(path).unlink()


def test_capability_evolver_emergent_signal():
    # 3+ 个 stable 且 event_count>=15 且 success_rate>=0.8 → 应产生 skill_extraction 信号
    path, conn = _make_db([
        _stable(20, 0.9, "a"),
        _stable(18, 0.85, "b"),
        _stable(16, 0.95, "c"),
    ])
    try:
        signals = capability_evolver._check_pattern_repetition(conn, datetime.now())
        names = {s.capability_name for s in signals}
        assert "skill_extraction" in names, f"应涌现 skill_extraction 信号，实际: {names}"
    finally:
        conn.close()
        Path(path).unlink()


def test_capability_evolver_no_signal_when_low_success():
    # 全 stable 但 success_rate 低于涌现下限 → 不应产生信号
    path, conn = _make_db([
        _stable(20, 0.5, "a"),
        _stable(18, 0.4, "b"),
        _stable(16, 0.3, "c"),
    ])
    try:
        signals = capability_evolver._check_pattern_repetition(conn, datetime.now())
        assert not any(s.capability_name == "skill_extraction" for s in signals)
    finally:
        conn.close()
        Path(path).unlink()
