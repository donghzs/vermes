"""R4 resolve_flag 测试。

P3-⑩ 之前：resolve 仅动 memory_flags。
P3-⑩ 之后：resolution='demote' 联动把被标记记忆 lifecycle_tag 置 ephemeral；
merge / false_positive 不动原记忆。记忆联动失败须 fail-open（不阻断 flag 解决）。
"""
from datetime import datetime, timezone
from pathlib import Path


def _seed_memory(db, pointer, fts_content, lifecycle_tag="reference"):
    """在完整 memories 表中插一条记忆，返回其 id。"""
    import sqlite3

    from agent import memory_fabric as mf

    mf._init_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO memories "
        "(source, layer, type, scope, pointer, fts_content, updated_at, lifecycle_tag) "
        "VALUES ('note','note','note','', ?, ?, ?, ?)",
        (pointer, fts_content, datetime.now(timezone.utc).isoformat(), lifecycle_tag),
    )
    mem_id = conn.execute(
        "SELECT id FROM memories WHERE pointer=?", (pointer,)
    ).fetchone()[0]
    conn.commit()
    conn.close()
    return mem_id


def _get_tag(db, mem_id):
    import sqlite3

    conn = sqlite3.connect(str(db))
    tag = conn.execute(
        "SELECT lifecycle_tag FROM memories WHERE id=?", (mem_id,)
    ).fetchone()[0]
    conn.close()
    return tag


def test_resolve_flag_marks_resolved(tmp_path, monkeypatch):
    """resolve 后 flag 状态=resolved 且不再出现在 open 列表"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    flag_id, is_new = mr.write_flag("mem_1", "outdated", "old version", 0.8)
    assert is_new

    ok = mr.resolve_flag(flag_id, "false_positive")
    assert ok is True

    flag = mr.get_flag(flag_id)
    assert flag["status"] == "resolved"
    assert flag["resolution"] == "false_positive"
    assert flag["resolved_at"] is not None
    # 第6通道（get_open_flags）不再上浮已解决 flag
    assert mr.get_open_flags() == []


def test_resolve_flag_idempotent_and_invalid(tmp_path, monkeypatch):
    """已 resolved 再 resolve 失败；非法 resolution / 不存在 flag 均 False"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    flag_id, _ = mr.write_flag("mem_1", "duplicate", "dup", 0.7)
    assert mr.resolve_flag(flag_id, "merge") is True
    assert mr.resolve_flag(flag_id, "merge") is False  # 已 resolved

    assert mr.resolve_flag(999, "bogus") is False       # 非法 resolution
    assert mr.resolve_flag(424242, "merge") is False     # 不存在


def test_resolve_flag_demote_fails_open_without_memories_table(tmp_path, monkeypatch):
    """fail-open：即使 memories 表不存在，demote 也应成功解决 flag（不报错）"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)       # 仅建 memory_flags，不建 memories
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    flag_id, _ = mr.write_flag("mem_x", "scope_creep", "too broad", 0.5)
    assert mr.resolve_flag(flag_id, "demote") is True   # 仍成功


def test_resolve_flag_demote_demotes_memory(tmp_path, monkeypatch):
    """demote resolution 联动把被标记记忆 lifecycle_tag 置 ephemeral（英）"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    mem_id = _seed_memory(db, "ptr_en", "legacy API endpoint config")
    flag_id, _ = mr.write_flag(str(mem_id), "outdated", "stale info", 0.8)
    assert mr.resolve_flag(flag_id, "demote") is True

    assert _get_tag(db, mem_id) == "ephemeral"


def test_resolve_flag_merge_false_positive_do_not_demote(tmp_path, monkeypatch):
    """merge / false_positive 不得改动原记忆 lifecycle_tag（英）"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    mem_id = _seed_memory(db, "ptr_merge", "should stay reference")
    flag_id, _ = mr.write_flag(str(mem_id), "duplicate", "dup", 0.7)
    assert mr.resolve_flag(flag_id, "merge") is True

    assert _get_tag(db, mem_id) == "reference"  # 未变


def test_resolve_flag_demote_chinese_memory(tmp_path, monkeypatch):
    """demote 联动降级：中文记忆同样置 ephemeral（中，断言具体值）"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    mem_id = _seed_memory(db, "ptr_cn", "用户提供的数据库密码是 s3cretPass")
    flag_id, _ = mr.write_flag(str(mem_id), "outdated", "密码已轮换", 0.9)
    assert mr.resolve_flag(flag_id, "demote") is True

    assert _get_tag(db, mem_id) == "ephemeral"
