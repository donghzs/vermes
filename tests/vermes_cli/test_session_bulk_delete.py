"""会话批量删除 + 后台来源过滤的回归测试。

守护两类最近修复：
1. bulk_delete_sessions 级联清理 messages，并返回真实删除数（空列表安全 no-op）。
2. list_sessions_rich 用 exclude_sources 过滤后台来源（curator），
   让「我的对话」列表不出现 curator 审查会话（仅「后台任务」视图可见）。
"""
import os
import sqlite3
import tempfile
from pathlib import Path

from vermes_state import SessionDB


def _new_db():
    path = Path(tempfile.mktemp(suffix=".db"))
    db = SessionDB(db_path=path)
    return db, path


def _insert_message(path, session_id):
    """绕过 SessionDB 高层 API，直接落一条 message，验证级联删除。"""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (session_id, "user", "hello", 1700000000.0),
        )
        conn.commit()
    finally:
        conn.close()


def _count_messages(path, session_id):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_exclude_sources_filters_curator():
    db, path = _new_db()
    try:
        db.create_session("cur1", "curator")
        db.create_session("tg1", "telegram")
        db.create_session("web1", "web")

        # 默认「我的对话」排除 web + curator，只留 telegram
        # include_empty=True：隔离 exclude_sources 行为（默认会顺带排除空壳会话）
        rows = db.list_sessions_rich(exclude_sources=["web", "curator"], include_empty=True)
        ids = {r["id"] for r in rows}
        assert "tg1" in ids, ids
        assert "cur1" not in ids, ids
        assert "web1" not in ids, ids
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_bulk_delete_cascades_messages_and_counts():
    db, path = _new_db()
    try:
        db.create_session("cur1", "curator")
        db.create_session("tg1", "telegram")
        db.create_session("web1", "web")
        _insert_message(path, "cur1")

        # 批量删除 curator + telegram
        n = db.bulk_delete_sessions(["cur1", "tg1"])
        assert n == 2, n

        # 会话行已删，web 仍在
        remaining = {r["id"] for r in db.list_sessions_rich(include_empty=True)}
        assert remaining == {"web1"}, remaining

        # 级联：cur1 的 message 应被清空
        assert _count_messages(path, "cur1") == 0
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)


def test_bulk_delete_empty_is_noop():
    db, path = _new_db()
    try:
        db.create_session("cur1", "curator")
        assert db.bulk_delete_sessions([]) == 0
        # 未被误删
        remaining = {r["id"] for r in db.list_sessions_rich(include_empty=True)}
        assert remaining == {"cur1"}, remaining
    finally:
        db.close()
        os.path.exists(path) and os.remove(path)
