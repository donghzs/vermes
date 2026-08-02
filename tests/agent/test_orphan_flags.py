"""测试孤儿 flag 清理 + auto_resolve orphan 分支"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent.memory_fabric import _init_db, _get_index_db
from agent.memory_reflection import (
    auto_resolve_eligible_flags,
    cleanup_merged_skill_memories,
    ensure_reflection_schema,
)


def _make_db(with_merge_cleanup_done=False):
    """创建临时记忆库。

    Args:
        with_merge_cleanup_done: 若 True，标记 merge_cleanup_done=1，
            阻止 _init_db 的 merge 清扫删 skill 记忆。
            用于测试孤儿清理时需要保留 skill 记忆的场景。
    """
    db = tempfile.mktemp(suffix=".db")
    _init_db(Path(db))
    ensure_reflection_schema(Path(db))
    conn = sqlite3.connect(db)
    if with_merge_cleanup_done:
        # 标记 merge 清扫已完成，防止 _init_db 删 skill 记忆
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('merge_cleanup_done', '1')"
        )
    # 插入几条记忆
    conn.execute(
        "INSERT INTO memories (id,source,layer,type,scope,pointer,fts_content,updated_at,lifecycle_tag) "
        "VALUES (1,'skill','procedural','skill_text','','skill#1','test skill 1','2026-08-02','ephemeral')"
    )
    conn.execute(
        "INSERT INTO memories (id,source,layer,type,scope,pointer,fts_content,updated_at,lifecycle_tag) "
        "VALUES (2,'note','note','note_text','','note#2','test note','2026-08-02','preference')"
    )
    conn.execute(
        "INSERT INTO memories (id,source,layer,type,scope,pointer,fts_content,updated_at,lifecycle_tag) "
        "VALUES (3,'skill','procedural','skill_text','','skill#3','test skill 3','2026-08-02','ephemeral')"
    )
    # 插入 flag
    # flag 10: duplicate conf=0.95 指向 memory 1 (skill)
    conn.execute(
        "INSERT INTO memory_flags (id,memory_id,flag_type,confidence,evidence,status,created_at,source) "
        "VALUES (10,1,'duplicate',0.95,'orphan test 1','open','2026-08-02','reflection')"
    )
    # flag 20: 指向已删记忆(id=99不存在)的孤儿 flag
    conn.execute(
        "INSERT INTO memory_flags (id,memory_id,flag_type,confidence,evidence,status,created_at,source) "
        "VALUES (20,99,'duplicate',0.95,'orphan test 2','open','2026-08-02','reflection')"
    )
    # flag 30: 孤儿 outdated
    conn.execute(
        "INSERT INTO memory_flags (id,memory_id,flag_type,confidence,evidence,status,created_at,source) "
        "VALUES (30,99,'outdated',0.9,'orphan outdated','open','2026-08-02','reflection')"
    )
    # flag 40: 指向存在的 note 记忆（不应被孤儿清理处理）
    conn.execute(
        "INSERT INTO memory_flags (id,memory_id,flag_type,confidence,evidence,status,created_at,source) "
        "VALUES (40,2,'contradiction',0.85,'real contradiction','open','2026-08-02','reflection')"
    )
    conn.commit()
    conn.close()
    return db


class TestOrphanFlagCleanup:
    def test_orphan_flags_resolved(self):
        """孤儿 flag（指向已删记忆）应被自动 resolve 为 'orphan'"""
        # 阻止 merge 清扫删 skill 记忆，这样只有 flag 20/30 是真正的孤儿
        db = _make_db(with_merge_cleanup_done=True)
        # 重新跑 _init_db 触发孤儿清理
        _init_db(Path(db))
        conn = sqlite3.connect(db)
        # flag 20, 30 指向 memory_id=99（不存在）→ 应已 resolve 为 orphan
        orphan_flags = conn.execute(
            "SELECT id, resolution FROM memory_flags WHERE resolution='orphan'"
        ).fetchall()
        assert len(orphan_flags) >= 2
        ids = [r[0] for r in orphan_flags]
        assert 20 in ids
        assert 30 in ids
        # flag 10 指向 memory 1 (skill, 存在) → 不应被孤儿清理影响
        # flag 40 指向 memory 2 (note, 存在) → 不应被孤儿清理影响
        still_open = conn.execute(
            "SELECT id FROM memory_flags WHERE status='open'"
        ).fetchall()
        open_ids = [r[0] for r in still_open]
        assert 10 in open_ids
        assert 40 in open_ids
        conn.close()
        Path(db).unlink(missing_ok=True)

    def test_orphan_cleanup_idempotent(self):
        """孤儿清理幂等——重跑零效果"""
        db = _make_db(with_merge_cleanup_done=True)
        _init_db(Path(db))
        orphan_count_before = 0
        conn = sqlite3.connect(db)
        orphan_count_before = len(conn.execute(
            "SELECT id FROM memory_flags WHERE resolution='orphan'"
        ).fetchall())
        conn.close()
        _init_db(Path(db))  # 第二次跑
        conn = sqlite3.connect(db)
        orphan_count_after = len(conn.execute(
            "SELECT id FROM memory_flags WHERE resolution='orphan'"
        ).fetchall())
        assert orphan_count_after == orphan_count_before
        conn.close()
        Path(db).unlink(missing_ok=True)


class TestAutoResolveOrphan:
    def test_auto_resolve_orphan_duplicate(self):
        """auto_resolve 应处理指向已删记忆的 duplicate flag"""
        db = _make_db(with_merge_cleanup_done=True)
        # 先删掉 memory 1，让 flag 10 变孤儿
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM memories WHERE id=1")
        conn.commit()
        conn.close()
        # 跑 auto_resolve，monkeypatch db_path
        db_path = Path(db)
        with patch('agent.memory_fabric._get_index_db', return_value=db_path):
            resolved = auto_resolve_eligible_flags()
        # flag 10 (duplicate conf=0.95, memory已删) + flag 20 (duplicate conf=0.95, memory不存在)
        assert resolved >= 2
        conn = sqlite3.connect(db)
        f10 = conn.execute(
            "SELECT status, resolution FROM memory_flags WHERE id=10"
        ).fetchone()
        f20 = conn.execute(
            "SELECT status, resolution FROM memory_flags WHERE id=20"
        ).fetchone()
        assert f10[0] == "resolved"
        assert f20[0] == "resolved"
        conn.close()
        Path(db).unlink(missing_ok=True)

    def test_auto_resolve_skill_duplicate_still_works(self):
        """auto_resolve 对 source=skill 的 duplicate 仍正常工作"""
        db = _make_db(with_merge_cleanup_done=True)
        # flag 10 指向 memory 1 (source=skill)
        db_path = Path(db)
        with patch('agent.memory_fabric._get_index_db', return_value=db_path):
            resolved = auto_resolve_eligible_flags()
        assert resolved >= 1
        conn = sqlite3.connect(db)
        f10 = conn.execute(
            "SELECT status, resolution FROM memory_flags WHERE id=10"
        ).fetchone()
        assert f10[0] == "resolved"
        assert f10[1] == "demote"
        conn.close()
        Path(db).unlink(missing_ok=True)

    def test_auto_resolve_does_not_demote_note_duplicate(self):
        """auto_resolve 不自动处理非 skill 源的 duplicate"""
        db = _make_db(with_merge_cleanup_done=True)
        # 添加一个指向 note 记忆的 duplicate flag
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO memory_flags (id,memory_id,flag_type,confidence,evidence,status,created_at,source) "
            "VALUES (50,2,'duplicate',0.95,'note dup','open','2026-08-02','reflection')"
        )
        conn.commit()
        conn.close()
        db_path = Path(db)
        with patch('agent.memory_fabric._get_index_db', return_value=db_path):
            resolved = auto_resolve_eligible_flags()
        # flag 50 指向 note (source!=skill) → 不自动处理
        conn = sqlite3.connect(db)
        f50 = conn.execute(
            "SELECT status FROM memory_flags WHERE id=50"
        ).fetchone()
        assert f50[0] == "open"
        conn.close()
        Path(db).unlink(missing_ok=True)
