"""阶段B 恢复面层测试：restore_flag 三路语义 + get_resolved_flags + 前端联动。

核心断言：
  - demote 恢复：flag→open + lifecycle_tag→reference
  - merge 恢复：flag→open，lifecycle_tag 不变（仍 ephemeral）
  - false_positive 恢复：flag→open，lifecycle_tag 不变
  - fail-open：memory 行不存在不影响 flag 重开
  - 中文断言具体值（"战略定位" 恢复后 tag=reference）
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.memory_reflection import restore_flag, get_resolved_flags  # noqa: E402


# ── 共享 fixture：临时 db ──

_MEMORIES_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    source TEXT, layer TEXT, type TEXT, scope TEXT,
    pointer TEXT, fts_content TEXT, updated_at TEXT,
    access_count INTEGER DEFAULT 0, lifecycle_tag TEXT DEFAULT 'reference'
)
"""

_FLAGS_DDL = """
CREATE TABLE IF NOT EXISTS memory_flags (
    id INTEGER PRIMARY KEY,
    memory_id TEXT, flag_type TEXT, confidence REAL,
    evidence TEXT, status TEXT DEFAULT 'open',
    created_at TEXT, source TEXT, resolution TEXT, resolved_at TEXT
)
"""


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(_MEMORIES_DDL)
    conn.execute(_FLAGS_DDL)
    conn.commit()

    # 插入两条记忆（中文 + 英文）
    conn.execute(
        "INSERT INTO memories (id, source, layer, lifecycle_tag, fts_content) "
        "VALUES (1, 'note', 'note', 'reference', 'Vermes战略定位：独立审计→迭代开发')",
    )
    conn.execute(
        "INSERT INTO memories (id, source, layer, lifecycle_tag, fts_content) "
        "VALUES (2, 'skill', 'procedural', 'ephemeral', 'ASCII art skill description')",
    )
    conn.commit()
    yield path, conn
    conn.close()
    Path(path).unlink()


def _monkey_db(monkeypatch, path):
    """让 _get_index_db 返回 Path（_init_db 需要 Path）"""
    from pathlib import Path as _Path
    monkeypatch.setattr(
        "agent.memory_fabric._get_index_db",
        lambda: _Path(path),
    )
    # 同时 monkeypatch _init_db 避免它真建目录/表
    monkeypatch.setattr(
        "agent.memory_fabric._init_db",
        lambda db_path: None,
    )


# ── 测试 ──

def test_restore_demote_reopens_flag_and_restores_tag(monkeypatch, temp_db):
    """demote 恢复 → flag→open + lifecycle_tag→reference（中文断言）"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    # 先 demote：flag→resolved + lifecycle_tag→ephemeral
    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, resolution, resolved_at, created_at) "
        "VALUES (100, '1', 'duplicate', 0.90, '重复：战略定位', "
        "'resolved', 'demote', '2026-08-01', '2026-08-01')",
    )
    conn.commit()

    # 记忆的 lifecycle_tag 是 reference（demote 之前）
    assert conn.execute(
        "SELECT lifecycle_tag FROM memories WHERE id=1"
    ).fetchone()[0] == "reference"

    # 模拟 demote：手动把 lifecycle_tag 改为 ephemeral
    conn.execute("UPDATE memories SET lifecycle_tag='ephemeral' WHERE id=1")
    conn.commit()

    # 现在应该是 ephemeral
    assert conn.execute(
        "SELECT lifecycle_tag FROM memories WHERE id=1"
    ).fetchone()[0] == "ephemeral"

    # 恢复
    ok = restore_flag(100)
    assert ok is True

    # 验证：flag→open + lifecycle_tag→reference
    flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=100").fetchone()
    assert flag[0] == "open"
    assert flag[1] is None

    tag = conn.execute("SELECT lifecycle_tag FROM memories WHERE id=1").fetchone()[0]
    assert tag == "reference", "demote 恢复应把 lifecycle_tag 改回 reference"


def test_restore_merge_reopens_flag_but_keeps_tag(monkeypatch, temp_db):
    """merge 恢复 → flag→open，lifecycle_tag 不变（仍 ephemeral）"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, resolution, resolved_at, created_at) "
        "VALUES (101, '2', 'duplicate', 0.90, '重复技能描述', "
        "'resolved', 'merge', '2026-08-01', '2026-08-01')",
    )
    # 记忆已是 ephemeral（P2-⑧ 降级后）
    conn.execute("UPDATE memories SET lifecycle_tag='ephemeral' WHERE id=2")
    conn.commit()

    ok = restore_flag(101)
    assert ok is True

    # flag→open，lifecycle_tag 不变（merge 不恢复权重）
    flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=101").fetchone()
    assert flag[0] == "open"
    assert flag[1] is None

    tag = conn.execute("SELECT lifecycle_tag FROM memories WHERE id=2").fetchone()[0]
    assert tag == "ephemeral", "merge 恢复不应改 lifecycle_tag"


def test_restore_false_positive_reopens_flag(monkeypatch, temp_db):
    """false_positive 恢复 → flag→open，lifecycle_tag 不变"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, resolution, resolved_at, created_at) "
        "VALUES (102, '1', 'contradiction', 0.40, '误报的矛盾', "
        "'resolved', 'false_positive', '2026-08-01', '2026-08-01')",
    )
    conn.commit()

    ok = restore_flag(102)
    assert ok is True

    flag = conn.execute("SELECT status FROM memory_flags WHERE id=102").fetchone()
    assert flag[0] == "open"

    # false_positive 不动 lifecycle_tag（记忆原本就是 reference）
    tag = conn.execute("SELECT lifecycle_tag FROM memories WHERE id=1").fetchone()[0]
    assert tag == "reference"


def test_restore_fail_open_on_missing_memory(monkeypatch, temp_db):
    """fail-open：memory 行不存在不影响 flag 重开"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    # flag 指向一个不存在的 memory_id
    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, resolution, resolved_at, created_at) "
        "VALUES (103, '999', 'duplicate', 0.85, '指向不存在记忆', "
        "'resolved', 'demote', '2026-08-01', '2026-08-01')",
    )
    conn.commit()

    ok = restore_flag(103)
    assert ok is True  # flag 重开成功

    # UPDATE memories WHERE id=999 → rowcount=0，不影响
    flag = conn.execute("SELECT status FROM memory_flags WHERE id=103").fetchone()
    assert flag[0] == "open"


def test_restore_nonexistent_flag_returns_false(monkeypatch, temp_db):
    """不存在或未 resolved 的 flag → 返回 False"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    ok = restore_flag(999)
    assert ok is False


def test_get_resolved_flags(monkeypatch, temp_db):
    """get_resolved_flags 返回 resolved 列表（含 resolution 字段）"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, resolution, resolved_at, created_at) "
        "VALUES (104, '1', 'duplicate', 0.9, 'test', 'resolved', 'demote', '2026-08-01', '2026-08-01')",
    )
    conn.commit()

    resolved = get_resolved_flags()
    assert len(resolved) >= 1
    assert resolved[0]["resolution"] == "demote"
