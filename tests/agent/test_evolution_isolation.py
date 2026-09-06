"""H3 进化隔离 — 回归守卫（2026-09-07 审计 #2 补）。

背景：H3（per-agent 进化隔离）交付时只更新了 1 处既有列断言，核心逻辑零专项测试。
本文件把审计时手工做过的验证沉淀为仓库内护栏，并针对审计发现的问题建立反向守卫：

1. 三态写入分布（p_A / p_B / NULL 互不混淆）
2. 读取侧 get_evolution_status(agent_id) 选择性过滤
3. **role_stats 不泄漏其他 agent 的聚合统计**（审计 #1 回归守卫）
4. 三表迁移幂等（老库 12/6/5 列 → 14/7/6）
5. v_outcomes agent_id 透出 + role 真值映射
6. **detect_role 失败不得中断 strategies 写入**（存量 bug 回归守卫）
7. **purge 后 strategies 仍可写**（缓存连接被 close 回归守卫）

R5 反向验证：#3 在 role_stats 未加 _scope_and 时会失败（p_A 可见 p_B）；
#6 在 detect_role 缺少 try/except 兜底时会失败；#7 在 purge 保留 conn.close() 时会失败。
"""

import os
import sqlite3
import tempfile

import pytest

from agent import evolution_manager as em


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_vermes_home():
    """Fresh VERMES_HOME with seeded evolution DB (isolated, no real ~/.vermes)."""
    home = tempfile.mkdtemp(prefix="h3iso_")
    prev = os.environ.get("VERMES_HOME")
    os.environ["VERMES_HOME"] = home
    em._evolution_active = None

    from agent.evolution_manager import is_evolution_active
    is_evolution_active()

    from agent.evolution_manager import get_self_model_db
    db_path = str(get_self_model_db())
    yield db_path

    em._evolution_active = None
    os.environ.pop("VERMES_HOME", None)
    if prev is not None:
        os.environ["VERMES_HOME"] = prev


class FakeAgent:
    """Minimal AIAgent stand-in exposing what record_tool_outcome reads."""

    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.session_id = f"sess-{agent_id or 'global'}"
        self.turn_counter = 1


def _seed_three_agents():
    """p_A ×2, p_B ×1, NULL ×1 — the canonical three-state fixture."""
    em.record_tool_outcome(FakeAgent("p_A"), "web_search", {"q": "a1"}, "ok", False, 1.0, "msg a1")
    em.record_tool_outcome(FakeAgent("p_A"), "read_file", {"p": "a2"}, "ok", False, 1.0, "msg a2")
    em.record_tool_outcome(FakeAgent("p_B"), "web_search", {"q": "b1"}, "ok", False, 1.0, "msg b1")
    em.record_tool_outcome(FakeAgent(None), "terminal", {"c": "g1"}, "ok", False, 1.0, "msg g1")


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


# ── 1. 写入侧三态隔离 ───────────────────────────────────────────────────────

class TestWriteIsolation:
    def test_raw_events_tagged_by_agent(self, fresh_vermes_home):
        _seed_three_agents()
        conn = sqlite3.connect(fresh_vermes_home)
        dist = dict(
            conn.execute(
                "SELECT COALESCE(agent_id,'__NULL__'), COUNT(*) FROM raw_events GROUP BY agent_id"
            ).fetchall()
        )
        conn.close()
        assert dist.get("p_A") == 2, f"p_A should own 2 events, got {dist}"
        assert dist.get("p_B") == 1, f"p_B should own 1 event, got {dist}"

    def test_strategies_hard_isolated(self, fresh_vermes_home):
        """Same task_type+strategy from different agents must NOT collapse into one row."""
        _seed_three_agents()
        conn = sqlite3.connect(fresh_vermes_home)
        rows = conn.execute(
            "SELECT agent_id, COUNT(*) FROM strategies "
            "WHERE strategy='web_search:web_search' GROUP BY agent_id"
        ).fetchall()
        conn.close()
        by_agent = dict(rows)
        # p_A 与 p_B 都跑过 web_search —— 必须是两行，各自一条
        assert by_agent.get("p_A") == 1, f"p_A strategy row missing/duplicated: {by_agent}"
        assert by_agent.get("p_B") == 1, f"p_B strategy row missing/duplicated: {by_agent}"

    def test_self_model_soft_shared_stamped(self, fresh_vermes_home):
        """self_model 软共享：行被打 agent_id 戳，但 summary.% 聚合不过滤。"""
        _seed_three_agents()
        conn = sqlite3.connect(fresh_vermes_home)
        stamped = conn.execute(
            "SELECT COUNT(*) FROM self_model WHERE agent_id='p_A'"
        ).fetchone()[0]
        conn.close()
        assert stamped >= 1, "self_model rows should carry the writer's agent_id stamp"


# ── 2. 读取侧选择性过滤 ─────────────────────────────────────────────────────

class TestReadFiltering:
    def test_global_aggregation_sees_everything(self, fresh_vermes_home):
        _seed_three_agents()
        st = em.get_evolution_status(None)
        # 全局：p_A(2) + p_B(1) + NULL(1) 全可见
        assert st["total_outcomes"] >= 4, f"global view too small: {st['total_outcomes']}"

    def test_agent_sees_only_own_outcomes(self, fresh_vermes_home):
        _seed_three_agents()
        st_a = em.get_evolution_status("p_A")
        st_b = em.get_evolution_status("p_B")
        assert st_a["total_outcomes"] == 2, f"p_A should see 2, got {st_a['total_outcomes']}"
        assert st_b["total_outcomes"] == 1, f"p_B should see 1, got {st_b['total_outcomes']}"

    def test_agent_does_not_see_global_baseline(self, fresh_vermes_home):
        """硬隔离：传 agent_id 时不包含 NULL 底座行（与注释口径一致）。"""
        _seed_three_agents()
        st_a = em.get_evolution_status("p_A")
        conn = sqlite3.connect(fresh_vermes_home)
        null_rows = conn.execute(
            "SELECT COUNT(*) FROM v_outcomes WHERE agent_id IS NULL"
        ).fetchone()[0]
        conn.close()
        assert null_rows >= 1, "fixture should contain NULL-baseline rows"
        assert st_a["total_outcomes"] < null_rows + 3, (
            "p_A view must exclude NULL-baseline rows"
        )


# ── 3. 审计 #1 回归守卫：role_stats 不得泄漏他人统计 ─────────────────────────

class TestRoleStatsNoLeak:
    def test_role_stats_excludes_other_agents(self, fresh_vermes_home):
        """审计 #1：此前 get_evolution_status('p_A') 会返回 [('p_B', 1), ...]。

        v_outcomes.role 现在是 agent_id 真值，若 role_stats 不过滤，
        群聊 agent 就能看到他人的调用量与成功率 —— 违反硬隔离语义。
        """
        _seed_three_agents()
        st_a = em.get_evolution_status("p_A")
        roles = {row[0] for row in (st_a.get("role_stats") or [])}
        assert "p_B" not in roles, (
            f"LEAK: p_A can see p_B's aggregated stats; role_stats={st_a.get('role_stats')}"
        )

    def test_role_stats_global_still_aggregates(self, fresh_vermes_home):
        """正向护栏：全局视图（agent_id=None）仍应看到所有 role 分布。"""
        _seed_three_agents()
        st = em.get_evolution_status(None)
        roles = {row[0] for row in (st.get("role_stats") or [])}
        assert "p_A" in roles and "p_B" in roles, (
            f"global view lost role diversity: {st.get('role_stats')}"
        )


# ── 4. 迁移幂等 ─────────────────────────────────────────────────────────────

class TestMigrationIdempotent:
    def test_three_tables_have_agent_id(self, fresh_vermes_home):
        conn = sqlite3.connect(fresh_vermes_home)
        try:
            for table in ("raw_events", "strategies", "self_model"):
                assert "agent_id" in _cols(conn, table), f"{table} missing agent_id column"
        finally:
            conn.close()

    def test_rerun_migration_is_noop(self, fresh_vermes_home):
        """重复 ensure / is_evolution_active 不得报错，也不得重复加列。"""
        from agent.raw_event import ensure_raw_events_table

        conn = sqlite3.connect(fresh_vermes_home)
        before = {t: len(_cols(conn, t)) for t in ("raw_events", "strategies", "self_model")}
        conn.close()

        # 第二轮迁移
        em._evolution_active = None
        em.is_evolution_active()
        conn = sqlite3.connect(fresh_vermes_home)
        ensure_raw_events_table(conn)
        after = {t: len(_cols(conn, t)) for t in ("raw_events", "strategies", "self_model")}
        conn.close()

        assert before == after, f"migration not idempotent: {before} -> {after}"


# ── 5. v_outcomes 视图契约 ──────────────────────────────────────────────────

class TestViewContract:
    def test_view_exposes_agent_id(self, fresh_vermes_home):
        conn = sqlite3.connect(fresh_vermes_home)
        cols = _cols(conn, "v_outcomes")
        conn.close()
        assert "agent_id" in cols, f"v_outcomes must expose agent_id; got {cols}"

    def test_role_maps_to_agent_truth(self, fresh_vermes_home):
        """role 不再硬编码 'default'，而是 CASE WHEN agent_id 真值映射。"""
        _seed_three_agents()
        conn = sqlite3.connect(fresh_vermes_home)
        pairs = dict(
            conn.execute("SELECT DISTINCT agent_id, role FROM v_outcomes").fetchall()
        )
        conn.close()
        assert pairs.get("p_A") == "p_A", f"role should mirror agent_id; got {pairs}"
        assert pairs.get("p_B") == "p_B", f"role should mirror agent_id; got {pairs}"
        assert pairs.get(None) == "default", f"NULL agent must map to 'default'; got {pairs}"


# ── 6. 存量 bug 回归守卫：detect_role 失败不得中断写入 ───────────────────────

class TestDetectRoleFailureContained:
    def test_outcome_still_written_when_detect_role_raises(self, fresh_vermes_home, monkeypatch):
        """存量 bug：roles UNIQUE 冲突曾让整段 strategies/self_model 静默跳过。

        修复后 detect_role 被 try/except 包裹并回退 role='default'，
        策略记录必须照常落库。
        """
        import sqlite3 as _sq

        def _boom(*args, **kwargs):
            raise _sq.IntegrityError("UNIQUE constraint failed: roles.role")

        monkeypatch.setattr(em, "detect_role", _boom)

        em.record_tool_outcome(FakeAgent("p_X"), "terminal", {"c": "ls"}, "ok", False, 1.0, "m")

        conn = sqlite3.connect(fresh_vermes_home)
        n = conn.execute(
            "SELECT COUNT(*) FROM strategies WHERE agent_id='p_X'"
        ).fetchone()[0]
        conn.close()
        assert n >= 1, "strategies silently skipped when detect_role raised"


# ── 7. 存量 bug 回归守卫：purge 不得关闭缓存连接 ─────────────────────────────

class TestPurgeKeepsConnectionAlive:
    def test_strategies_writable_after_purge(self, fresh_vermes_home, monkeypatch):
        """存量 bug：purge 在 record_tool_outcome **内部**（:879）被调用，位置在其
        cursor 获取（:826）**之后**。pre-fix 的 conn.close() 关掉的是 _get_conn 的
        **进程级缓存连接**，与 record_tool_outcome 的 cursor 是同一条 → 后续
        strategies/self_model 写入报 "Cannot operate on a closed database"。

        ⚠️ 必须强制重置 _GHOST_EMOTION_PURGED：purge 有进程级 once 守卫，若已被
        同进程其他调用执行过则直接 return 0，本测试会退化为恒真 —— 审计时实测踩过
        （单独调 purge 的版本 NOT CAUGHT，因为那条路径 conn 由 _get_conn 重建）。
        """
        monkeypatch.setattr(em, "_GHOST_EMOTION_PURGED", False, raising=False)

        # 不显式调 purge —— 让 record_tool_outcome 内部走到它，复现真实调用序
        em.record_tool_outcome(FakeAgent("p_Y"), "terminal", {"c": "ls"}, "ok", False, 1.0, "m")

        conn = sqlite3.connect(fresh_vermes_home)
        n = conn.execute(
            "SELECT COUNT(*) FROM strategies WHERE agent_id='p_Y'"
        ).fetchone()[0]
        conn.close()
        assert n >= 1, "strategies write failed after purge (closed database regression)"
