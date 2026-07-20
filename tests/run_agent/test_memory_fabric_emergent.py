"""P2a-safe 涌现式自适应：检索命中计数 + 频次 tiebreaker 回归测试。

验证：
- recall 命中后 access_count +1（传感器）
- 同 FTS rank 档内高频靠前（涌现 tiebreaker）
- 全量留存，零删除
- 空 query fail-open 返回 []
- get_memory_stats 形态正确

设计铁律（与 MEMORY.md 一致）：
  全量留存、永不删；冷热由真实使用分布涌现，不预设阈值；
  绝不 LLM 改写事实、绝不物理删除。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# 让系统 python 能 import agent.memory_fabric
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

from agent import memory_fabric as mf


def _fresh_db():
    """Create an isolated tmp memory_index.db and point mf at it."""
    tmp = Path(tempfile.mkdtemp()) / "memory_index.db"
    mf._get_index_db = lambda: tmp
    mf._init_db(tmp)
    return tmp


def _set_access_count(db_path, pointer, count):
    """Manually set access_count for a memory by pointer (test helper)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE memories SET access_count = ? WHERE pointer = ?",
            (count, pointer),
        )
        conn.commit()
    finally:
        conn.close()


def test_recall_bumps_access_count():
    """每次 recall 命中，access_count +1（传感器）。"""
    _fresh_db()
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#x", "fts_content": "rust async tokio runtime"})
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#y", "fts_content": "rust async await future"})
    res = mf.recall("rust async", limit=10)
    assert len(res) >= 1
    for r in res:
        assert r["access_count"] >= 1, f"命中后应 +1，got {r['access_count']}"
    before = {r["pointer"]: r["access_count"] for r in res}
    res2 = mf.recall("rust async", limit=10)
    for r in res2:
        assert r["access_count"] == before[r["pointer"]] + 1, "第二次召回应再 +1"


def test_same_rank_high_freq_ranks_first():
    """同 FTS rank（相同文本）时，access_count 高者靠前（涌现 tiebreaker）。"""
    _fresh_db()
    # 两条内容完全相同 → FTS rank 相等 → access_count DESC 决定序
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#low", "fts_content": "rust async tokio runtime"})
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#high", "fts_content": "rust async tokio runtime"})
    db = mf._get_index_db()
    _set_access_count(db, "web#high", 50)
    _set_access_count(db, "web#low", 0)
    res = mf.recall("rust async tokio", limit=10)
    assert len(res) == 2
    assert res[0]["pointer"] == "web#high", f"高频应靠前，got {res[0]['pointer']}"


def test_no_delete_preserves_all():
    """多次 recall 不删除任何记忆（全量留存，零删除）。"""
    _fresh_db()
    for i in range(5):
        mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
                   "pointer": f"web#{i}", "fts_content": f"rust async topic {i}"})
    for _ in range(10):
        mf.recall("rust async", limit=3)
    # 5 条全部仍在（零删除铁律）
    res = mf.recall("rust async topic", limit=50)
    pointers = {r["pointer"] for r in res}
    assert pointers == {f"web#{i}" for i in range(5)}, "零删除：5 条全留存"


def test_empty_query_returns_empty():
    """空 query fail-open 返回 []，不抛错。"""
    _fresh_db()
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#x", "fts_content": "rust async"})
    assert mf.recall("", limit=10) == []
    assert mf.recall("   ", limit=10) == []


def test_get_memory_stats_shape():
    """get_memory_stats 返回 {layer: {count, total_hits}}，fail-closed。"""
    _fresh_db()
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#x", "fts_content": "rust async tokio"})
    mf.record({"source": "web", "layer": mf.L4_REFERENCE, "type": "doc",
               "pointer": "web#y", "fts_content": "rust async await"})
    mf.recall("rust async", limit=10)
    stats = mf.get_memory_stats()
    assert isinstance(stats, dict)
    assert mf.L4_REFERENCE in stats
    assert stats[mf.L4_REFERENCE]["count"] == 2
    assert stats[mf.L4_REFERENCE]["total_hits"] >= 2, "至少 2 次命中"


if __name__ == "__main__":
    fns = [f for f in dir() if f.startswith("test_")]
    ok = fail = 0
    for fn in fns:
        try:
            globals()[fn]()
            ok += 1
            print("PASS", fn)
        except Exception as e:
            fail += 1
            print("FAIL", fn, "->", repr(e))
    print(f"TOTAL {len(fns)}: {ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)
