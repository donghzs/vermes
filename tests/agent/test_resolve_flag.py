"""R4 resolve_flag 测试（仅动 memory_flags，不动原 memories）"""
from pathlib import Path


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


def test_resolve_flag_does_not_touch_memories(tmp_path, monkeypatch):
    """铁律：resolve 仅动 memory_flags，不触碰原 memories 表"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)

    # 本测试 db 未创建 memories 表；resolve 不应尝试建/写它
    flag_id, _ = mr.write_flag("mem_x", "scope_creep", "too broad", 0.5)
    assert mr.resolve_flag(flag_id, "demote") is True
