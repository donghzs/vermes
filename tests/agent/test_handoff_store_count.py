"""handoff 表名回归测试。

背景（2026-09-27 实证事故）：
  agent/memory_recall.py 与 agent/capability_evolver.py 都查询了一张名为
  ``handoffs`` 的表。任何 schema 里都不存在这张表 —— 本模块创建的是
  ``session_handoffs``。于是：

    * memory_recall   ：抛 "no such table: handoffs"（DEBUG 级吞掉，4116 次），
                        handoff_count 恒为 0 → 跨会话连续性评分永久失灵；
    * capability_evolver：在 self-model 库里找 handoffs（跨库误查），同样恒 0
                        → "continuity gap" 信号无条件误触发。

  两者都是**静默**的：没有报错、没有降级提示，只是功能悄悄失效。

本文件锁定修复：单一真源 count_handoffs() 必须读到真表，且行为级证明
compute_richness() 的 handoff_count 不再恒为 0。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent import handoff_store
from agent.handoff_store import HANDOFF_TABLE, count_handoffs


@pytest.fixture
def handoff_db(tmp_path: Path) -> Path:
    """建一个只含真表 session_handoffs 的临时库（不建 handoffs）。"""
    db = tmp_path / "session_handoffs.db"
    conn = sqlite3.connect(str(db))
    handoff_store._init_db(conn)
    conn.close()
    return db


def test_canonical_table_name_is_session_handoffs():
    """真表名必须是 session_handoffs —— 历史 bug 就是写成了 handoffs。"""
    assert HANDOFF_TABLE == "session_handoffs"


def test_count_handoffs_reads_real_table(handoff_db: Path):
    """正探针：真表里有数据就必须数出来（修复前恒 0）。"""
    conn = sqlite3.connect(str(handoff_db))
    for i in range(3):
        conn.execute(
            "INSERT INTO session_handoffs(session_id, created_at, summary_text) "
            "VALUES (?, ?, ?)",
            (f"s{i}", float(i), f"summary {i}"),
        )
    conn.commit()
    conn.close()

    assert count_handoffs(handoff_db) == 3


def test_no_legacy_handoffs_table_exists(handoff_db: Path):
    """负探针：库里不该有 handoffs 表 —— 旧查询注定失败，这是 bug 的物证。"""
    conn = sqlite3.connect(str(handoff_db))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    conn.close()

    assert "session_handoffs" in names
    assert "handoffs" not in names


def test_count_handoffs_missing_file_returns_zero(tmp_path: Path):
    """Fail-open：文件不存在返回 0，不抛异常、不建库。"""
    missing = tmp_path / "nope.db"
    assert count_handoffs(missing) == 0
    assert not missing.exists(), "count_handoffs 不应有建库副作用"


def test_count_handoffs_table_absent_returns_zero(tmp_path: Path):
    """Fail-open：库存在但没有目标表 → 0（旧实现会抛 no such table）。"""
    db = tmp_path / "other.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE unrelated(x INTEGER)")
    conn.commit()
    conn.close()

    assert count_handoffs(db) == 0


def test_compute_richness_reports_real_handoff_count(handoff_db: Path, monkeypatch):
    """行为级回归：richness 的 handoff_count 必须反映真实数据，而不是恒 0。"""
    conn = sqlite3.connect(str(handoff_db))
    for i in range(2):
        conn.execute(
            "INSERT INTO session_handoffs(session_id, created_at, summary_text) "
            "VALUES (?, ?, ?)",
            (f"s{i}", float(i), f"summary {i}"),
        )
    conn.commit()
    conn.close()

    from agent import memory_recall

    # 隔离其它数据源，只让 handoff 分支生效
    monkeypatch.setattr(memory_recall, "_get_handoff_db", lambda: handoff_db)
    monkeypatch.setattr(memory_recall, "_get_self_model_db", lambda: None)

    score = memory_recall.compute_richness()
    assert score.handoff_count == 2, (
        "handoff_count 仍为 0 —— 说明又查回了不存在的 'handoffs' 表"
    )
