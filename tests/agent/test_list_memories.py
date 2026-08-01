"""阶段 C 测试：list_memories / get_memory_detail — P2-12 补测试覆盖"""
import sqlite3
import tempfile
from pathlib import Path

import pytest


def _setup_db(db_path):
    """建 memories + memories_fts 表，灌入测试数据"""
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT, layer TEXT, type TEXT, scope TEXT, pointer TEXT,
        fts_content TEXT, updated_at TEXT, access_count INTEGER DEFAULT 0,
        lifecycle_tag TEXT DEFAULT 'reference'
    )""")
    c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(fts_content, content='memories', content_rowid='id',
        tokenize='trigram')""")
    c.execute("""CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("INSERT INTO schema_meta (key, value) VALUES ('skill_demote_done', '1')")
    rows = [
        ("note", "L1_NOTE", "note_text", "", "note#user",
         "用户偏好用Python写后端", "2026-08-01", 10, "preference"),
        ("skill", "L2_PROCEDURAL", "skill", "", "skill#code/python",
         "Python programming language skill description", "2026-08-01", 5, "ephemeral"),
        ("l1_auto", "L1_NOTE", "l1_password", "", "l1_password#abc",
         "密码是MyS3cret!", "2026-08-01", 3, "reference"),
        ("note", "L1_NOTE", "note_text", "", "note#memory",
         "Vermes战略定位：独立审计迭代开发", "2026-08-01", 200, "preference"),
    ]
    for r in rows:
        c.execute(
            "INSERT INTO memories (source, layer, type, scope, pointer, fts_content, updated_at, access_count, lifecycle_tag) VALUES (?,?,?,?,?,?,?,?,?)",
            r,
        )
    # 同步 FTS
    c.execute("INSERT INTO memories_fts(rowid, fts_content) SELECT id, fts_content FROM memories")
    conn.commit()
    conn.close()


@pytest.fixture
def fab(monkeypatch):
    tmpdir = Path(tempfile.mkdtemp())
    db_path = tmpdir / "memory_index.db"
    _setup_db(db_path)
    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: db_path)
    # 不跳过 _init_db，但确保不写 schema_meta 重复
    return db_path


def test_list_memories_no_filter(fab):
    from agent.memory_fabric import list_memories
    result = list_memories()
    assert result["total"] == 4
    assert len(result["memories"]) == 4
    # 按 access_count DESC 排序
    assert result["memories"][0]["access_count"] == 200


def test_list_memories_search_chinese(fab):
    """P0-2 核心验证：中文搜索不崩溃"""
    from agent.memory_fabric import list_memories
    result = list_memories(query="战略定位")
    assert result["total"] >= 1
    assert any("战略" in m["content_preview"] for m in result["memories"])


def test_list_memories_search_english(fab):
    from agent.memory_fabric import list_memories
    result = list_memories(query="Python")
    assert result["total"] >= 1


def test_list_memories_filter_lifecycle_tag(fab):
    from agent.memory_fabric import list_memories
    result = list_memories(lifecycle_tag="preference")
    assert result["total"] == 2
    assert all(m["lifecycle_tag"] == "preference" for m in result["memories"])


def test_list_memories_filter_source(fab):
    from agent.memory_fabric import list_memories
    result = list_memories(source="skill")
    assert result["total"] == 1
    assert result["memories"][0]["source"] == "skill"


def test_list_memories_pagination(fab):
    from agent.memory_fabric import list_memories
    page1 = list_memories(limit=2, offset=0)
    page2 = list_memories(limit=2, offset=2)
    assert len(page1["memories"]) == 2
    assert len(page2["memories"]) == 2
    # 页间不重叠
    ids1 = {m["id"] for m in page1["memories"]}
    ids2 = {m["id"] for m in page2["memories"]}
    assert ids1.isdisjoint(ids2)


def test_get_memory_detail(fab):
    from agent.memory_fabric import get_memory_detail, list_memories
    listing = list_memories()
    first_id = listing["memories"][0]["id"]
    detail = get_memory_detail(first_id)
    assert detail is not None
    assert detail["id"] == first_id
    assert "content" in detail
    assert len(detail["content"]) > 0


def test_get_memory_detail_not_found(fab):
    from agent.memory_fabric import get_memory_detail
    detail = get_memory_detail(99999)
    assert detail is None
