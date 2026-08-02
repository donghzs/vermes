"""涌现飞轮 B 系列修复的聚焦单元测试（B1/B2/B3/B5/B6）。

这些测试覆盖审计报告中"飞轮断点"的关键回归路径，全部用临时 DB，
不触碰任何活库。运行：pytest tests/agent/test_emergence_flywheel_bfixes.py
"""

import os
import sys
import sqlite3
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_cluster_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE clusters (
            id INTEGER PRIMARY KEY, name TEXT, event_count INTEGER,
            success_rate REAL, lifecycle_stage TEXT, is_active INTEGER,
            last_active_at TEXT, last_seen TEXT, first_seen TEXT)"""
    )
    conn.execute(
        """CREATE TABLE raw_events (
            id INTEGER PRIMARY KEY, cluster_id INTEGER, timestamp TEXT,
            tool_name TEXT, args_preview TEXT, success INTEGER)"""
    )
    conn.execute(
        """CREATE TABLE cluster_lifecycle_events (
            id INTEGER PRIMARY KEY, cluster_id INTEGER,
            from_stage TEXT, to_stage TEXT, reason TEXT)"""
    )
    conn.commit()
    conn.close()
    return path


# ── B1: 阈值地板 + 簇复活 + is_active 一致性 ─────────────────────────────────

def test_threshold_floor_prevents_subsecond_death():
    """Bug 1: 突发灌入导致 avg_interval≈0.001s 时 k_dead 不再塌成毫秒级。"""
    from agent.cluster_lifecycle import ClusterLifecycleManager

    m = ClusterLifecycleManager.__new__(ClusterLifecycleManager)
    m.MIN_INTERVAL = 60.0

    class Fake(dict):
        pass

    c = Fake(id=1, event_count=5, lifecycle_stage="emerging",
             last_active_at=None, last_seen=None)
    m._compute_avg_interval = staticmethod(lambda cl: 0.001)
    t = m.compute_thresholds(c)
    # 地板 60s → k_dead >= 900s（旧实现是 0.015s）
    assert t.k_dead >= 60 * 15, f"k_dead 未受地板保护: {t.k_dead}"


def test_resurrect_dead_clusters_with_new_events():
    """dead 簇若收到新事件，应复活为 emerging 且 is_active=1。"""
    from agent.cluster_lifecycle import ClusterLifecycleManager

    path = _make_cluster_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO clusters VALUES (1,'behaviour_x',10,1.0,'dead',0,"
            "'2020-01-01T00:00:00','2020-01-01T00:00:00','2020-01-01T00:00:00')"
        )
        # 死亡之后到达的新事件
        conn.execute(
            "INSERT INTO raw_events VALUES (1,1,'2024-01-01T00:00:05','t','',1)"
        )
        conn.commit()
        conn.close()

        m = ClusterLifecycleManager(path)
        n = m._resurrect_dead_clusters()
        assert n == 1, f"应复活 1 个簇，实际 {n}"

        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT lifecycle_stage, is_active FROM clusters WHERE id=1"
        ).fetchone()
        conn.close()
        assert row == ("emerging", 1), row
    finally:
        os.remove(path)


def test_normalize_contradictory_active_flag():
    """stable 但 is_active=0 的矛盾簇应被纠正为 is_active=1。"""
    from agent.cluster_lifecycle import ClusterLifecycleManager

    path = _make_cluster_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO clusters VALUES (2,'y',10,1.0,'stable',0,"
            "'2024-01-01T00:00:00','2024-01-01T00:00:00','2024-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()

        m = ClusterLifecycleManager(path)
        n = m._normalize_active_flag()
        assert n == 1, f"应纠正 1 个矛盾簇，实际 {n}"

        conn = sqlite3.connect(path)
        flag = conn.execute(
            "SELECT is_active FROM clusters WHERE id=2"
        ).fetchone()[0]
        conn.close()
        assert flag == 1
    finally:
        os.remove(path)


def test_dead_cluster_without_new_events_stays_dead():
    """无新事件的 dead 簇保持 dead（复活机制不滥用）。"""
    from agent.cluster_lifecycle import ClusterLifecycleManager

    path = _make_cluster_db()
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO clusters VALUES (3,'z',10,1.0,'dead',0,"
            "'2020-01-01T00:00:00','2020-01-01T00:00:00','2020-01-01T00:00:00')"
        )
        conn.commit()
        conn.close()

        m = ClusterLifecycleManager(path)
        n = m._resurrect_dead_clusters()
        assert n == 0
    finally:
        os.remove(path)


# ── B2: 去掉 emergence 硬刹车（单簇也能涌现）─────────────────────────────────

def test_emergence_single_cluster_not_blocked():
    """修复后：仅 1 个 stable 簇也应能产生 pattern_repetition 信号（旧 len<3 刹车会拦截）。"""
    from agent.capability_evolver import _check_pattern_repetition

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE clusters (
                id INTEGER PRIMARY KEY, name TEXT, event_count INTEGER,
                success_rate REAL, lifecycle_stage TEXT, is_active INTEGER)"""
        )
        # 仅 1 个稳定、确立、非系统自噬簇
        conn.execute(
            "INSERT INTO clusters VALUES (1,'read_file:.py',20,1.0,'stable',1)"
        )
        conn.commit()
        from datetime import datetime
        signals = _check_pattern_repetition(conn, datetime.now())
        conn.close()
        assert len(signals) >= 1, "单簇下应产生涌现信号"
        assert signals[0].capability_name == "skill_extraction"
    finally:
        os.remove(path)


# ── B3: skill 候选集排除仅 __*__ 自噬簇（GLOB 健壮写法）──────────────────────

def test_skill_candidates_exclude_only_self_eating():
    """候选集应排除 __self_assessment__，但保留普通行为簇。"""
    from agent.skill_extractor import SkillExtractor

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE clusters (
                id INTEGER PRIMARY KEY, name TEXT, event_count INTEGER,
                success_count INTEGER, success_rate REAL, feature_signature TEXT,
                lifecycle_stage TEXT, is_active INTEGER)"""
        )
        conn.execute(
            "INSERT INTO clusters VALUES (1,'__self_assessment__',88,88,1.0,'','stable',1)"
        )
        conn.execute(
            "INSERT INTO clusters VALUES (2,'read_file:.py',20,20,1.0,'','stable',1)"
        )
        conn.execute(
            "INSERT INTO clusters VALUES (3,'search_files:.js+.py',31,30,0.96,'','stable',1)"
        )
        conn.commit()
        conn.close()

        se = SkillExtractor.__new__(SkillExtractor)
        # SkillExtractor 内部用 self._find_skill_candidates(conn)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row  # _find_skill_candidates 内部用 dict(r)，需 Row factory
        cands = se._find_skill_candidates(conn)
        conn.close()
        names = {c["name"] for c in cands}
        assert "__self_assessment__" not in names, "自噬簇不应进候选"
        assert "read_file:.py" in names, "行为簇应进候选"
        assert "search_files:.js+.py" in names
    finally:
        os.remove(path)


# ── B5: 幽灵边清理（只删 emotional_state，保留 strategy/anti_pattern）─────────

def test_purge_phantom_emotional_state_relations():
    """purge 应删除 emotional_state 幽灵边，保留有效 strategy/anti_pattern 边。"""
    from agent.evolution_manager import purge_phantom_emotional_state_relations
    from agent.evolution_manager import _GHOST_EMOTION_PURGED

    # 重置进程级守卫，保证本测试可重复
    import agent.evolution_manager as em
    em._GHOST_EMOTION_PURGED = False

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE relations (
                id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER,
                target_type TEXT, target_id INTEGER, rel_type TEXT,
                weight REAL, timestamp TEXT)"""
        )
        conn.execute(
            "INSERT INTO relations VALUES (1,'outcome',1,'emotional_state',5,'caused_emotion',0.5,'t')"
        )
        conn.execute(
            "INSERT INTO relations VALUES (2,'outcome',1,'strategy',7,'used',1.0,'t')"
        )
        conn.execute(
            "INSERT INTO relations VALUES (3,'outcome',1,'anti_pattern',9,'avoided',1.0,'t')"
        )
        conn.commit()
        conn.close()

        # 让 get_self_model_db() 指向我们的临时库
        orig = em.get_self_model_db
        em.get_self_model_db = lambda: path
        try:
            removed = purge_phantom_emotional_state_relations()
        finally:
            em.get_self_model_db = orig

        assert removed == 1, f"应删除 1 条幽灵边，实际 {removed}"
        conn = sqlite3.connect(path)
        n_emo = conn.execute(
            "SELECT COUNT(*) FROM relations WHERE target_type='emotional_state'"
        ).fetchone()[0]
        n_valid = conn.execute(
            "SELECT COUNT(*) FROM relations WHERE target_type IN ('strategy','anti_pattern')"
        ).fetchone()[0]
        conn.close()
        assert n_emo == 0, "emotional_state 幽灵边应被清空"
        assert n_valid == 2, "有效边应保留"
    finally:
        os.remove(path)


# ── B6: embeddings 空向量守卫（无向量不写空壳）──────────────────────────────

def test_store_embedding_skips_null_vector():
    """当 embedding 向量不可用（provider 无 /embeddings 端点）时，不写入 NULL 空壳。"""
    import agent.hybrid_retriever as hr

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        conn.execute(
            """CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY, content_hash TEXT UNIQUE,
                content TEXT, target TEXT, vector BLOB, created_at TEXT)"""
        )
        conn.commit()
        conn.close()

        # 重定向 _get_conn 到临时库，并让 _get_embedding 返回 None（无端点）
        orig_conn = hr._get_conn
        orig_emb = hr._get_embedding
        hr._get_conn = lambda: sqlite3.connect(path)
        hr._get_embedding = lambda content: None
        try:
            hr.store_embedding("some content", target="memory")
            # store_embedding 是后台线程；等它落库
            import time
            time.sleep(0.3)
        finally:
            hr._get_conn = orig_conn
            hr._get_embedding = orig_emb

        conn = sqlite3.connect(path)
        n = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        conn.close()
        assert n == 0, "无向量时不应写入空壳行"
    finally:
        os.remove(path)
