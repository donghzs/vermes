"""T5 变更通知中心 — 让 L1「静默执行 + 通知 + 可撤回」真正成立。

这些测试锁住的核心不变量：
  - L1 未读（进角标），L0 / L2 落库即已读（不打扰 / 当场已知情）；
  - 状态一律回查源表，账本里不缓存 —— 撤回后不用双写；
  - 账本是旁路：坏掉时返回空/0，绝不抛异常打断它正在记录的那次变更。
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import agent.change_ledger as cl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the ledger at a throwaway DB so tests never touch ~/.vermes."""
    db = tmp_path / "self-model.db"
    sqlite3.connect(db).close()          # 预建文件，跳过 seed 分支
    monkeypatch.setattr(cl, "_db_path", lambda: db)
    import agent.evolution_manager as em
    monkeypatch.setattr(em, "get_self_model_db", lambda: db)
    yield db


# ── 分层 → 未读语义 ──────────────────────────────────────────────────

def test_l1_change_is_unread():
    cid = cl.record_change(kind=cl.KIND_CONFIG_AUTO_APPLY, tier=cl.TIER_L1,
                           title="自动调整 duplicate 阈值")
    assert cid
    assert cl.unread_count() == 1
    row = cl.list_changes()[0]
    assert row["unread"] is True


def test_l0_change_is_recorded_but_silent():
    """L0 = 自动处理不打扰：可追溯，但不进角标。"""
    cl.record_change(kind=cl.KIND_SKILL_ADOPTED, tier=cl.TIER_L0,
                     title="采纳技能")
    assert cl.unread_count() == 0
    assert len(cl.list_changes()) == 1


def test_l2_change_is_recorded_but_silent():
    """L2 走弹窗，用户当场已确认过，事后再红点是噪音。"""
    cl.record_change(kind=cl.KIND_SOURCE_MODIFY, tier=cl.TIER_L2,
                     title="改写源码")
    assert cl.unread_count() == 0
    assert len(cl.list_changes()) == 1


def test_unread_only_filter():
    cl.record_change(kind="a", tier=cl.TIER_L0, title="silent")
    cl.record_change(kind="b", tier=cl.TIER_L1, title="loud")
    assert [c["title"] for c in cl.list_changes(unread_only=True)] == ["loud"]


# ── 读/清 ────────────────────────────────────────────────────────────

def test_mark_read_specific_ids():
    a = cl.record_change(kind="k", tier=cl.TIER_L1, title="a")
    cl.record_change(kind="k", tier=cl.TIER_L1, title="b")
    assert cl.unread_count() == 2
    assert cl.mark_read([a]) == 1
    assert cl.unread_count() == 1


def test_mark_read_is_idempotent():
    a = cl.record_change(kind="k", tier=cl.TIER_L1, title="a")
    assert cl.mark_read([a]) == 1
    assert cl.mark_read([a]) == 0          # 已读的不再计数


def test_mark_read_ignores_garbage_ids():
    cl.record_change(kind="k", tier=cl.TIER_L1, title="a")
    assert cl.mark_read(["not-an-id", None]) == 0
    assert cl.unread_count() == 1


def test_mark_all_read_clears_badge():
    for i in range(3):
        cl.record_change(kind="k", tier=cl.TIER_L1, title=f"t{i}")
    assert cl.mark_all_read() == 3
    assert cl.unread_count() == 0


# ── 引用状态回查（不双写）────────────────────────────────────────────

def test_ref_status_is_resolved_live(monkeypatch):
    """撤回只更新 proposals 一处，账本不存状态 —— 列表里应立刻反映新状态。"""
    state = {"status": "auto_applied"}
    import agent.evolution_manager as em
    monkeypatch.setattr(em, "get_proposal", lambda pid: dict(state, id=pid))

    cl.record_change(kind=cl.KIND_CONFIG_AUTO_APPLY, tier=cl.TIER_L1,
                     title="t", ref_kind=cl.REF_PROPOSAL, ref_id=7)
    assert cl.list_changes()[0]["ref_status"] == "auto_applied"

    state["status"] = "retracted"          # 只改源表
    assert cl.list_changes()[0]["ref_status"] == "retracted"


def test_ref_lookup_failure_does_not_break_listing(monkeypatch):
    import agent.evolution_manager as em

    def _boom(pid):
        raise RuntimeError("db gone")
    monkeypatch.setattr(em, "get_proposal", _boom)

    cl.record_change(kind="k", tier=cl.TIER_L1, title="t",
                     ref_kind=cl.REF_PROPOSAL, ref_id=1)
    rows = cl.list_changes()
    assert len(rows) == 1 and rows[0]["title"] == "t"


# ── 可撤回性：UI 用它决定按钮是否置灰 ────────────────────────────────

def _future():
    return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()


def _past():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def test_retractable_true_when_backup_alive(tmp_path):
    bak = tmp_path / "config.yaml.bak.001"
    bak.write_text("old")
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t",
                     bak_path=str(bak), retract_deadline=_future())
    assert cl.list_changes()[0]["retractable"] is True


def test_not_retractable_after_deadline(tmp_path):
    bak = tmp_path / "config.yaml.bak.001"
    bak.write_text("old")
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t",
                     bak_path=str(bak), retract_deadline=_past())
    assert cl.list_changes()[0]["retractable"] is False


def test_not_retractable_when_backup_reclaimed(tmp_path):
    """MAX_BACKUPS_PER_FILE=5 会回收早期备份 —— 按钮要提前置灰，
    而不是让用户点进一个错误。"""
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t",
                     bak_path=str(tmp_path / "gone.bak"),
                     retract_deadline=_future())
    assert cl.list_changes()[0]["retractable"] is False


def test_not_retractable_without_backup():
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t")
    assert cl.list_changes()[0]["retractable"] is False


# ── 存储细节 ─────────────────────────────────────────────────────────

def test_detail_roundtrips_as_object():
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t",
                     detail={"config_patch": {"a": {"b": 0.5}}})
    assert cl.list_changes()[0]["detail"]["config_patch"]["a"]["b"] == 0.5


def test_newest_first_and_limit():
    for i in range(5):
        cl.record_change(kind="k", tier=cl.TIER_L1, title=f"t{i}")
    rows = cl.list_changes(limit=2)
    assert [r["title"] for r in rows] == ["t4", "t3"]


def test_kind_and_tier_filters():
    cl.record_change(kind="alpha", tier=cl.TIER_L0, title="a")
    cl.record_change(kind="beta", tier=cl.TIER_L1, title="b")
    assert [c["title"] for c in cl.list_changes(kind="alpha")] == ["a"]
    assert [c["title"] for c in cl.list_changes(tier=cl.TIER_L1)] == ["b"]


def test_purge_keeps_unread_forever():
    """已读的老记录可以清，未读的绝不能清 —— 没被看见的通知是唯一
    必须活下来的东西。"""
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    read_id = cl.record_change(kind="k", tier=cl.TIER_L1, title="old-read")
    unread_id = cl.record_change(kind="k", tier=cl.TIER_L1, title="old-unread")
    cl.mark_read([read_id])
    conn = cl._conn()
    conn.execute("UPDATE agent_changes SET created=?", (old,))
    conn.commit()

    assert cl.purge_old(max_age_days=30) == 1
    titles = [c["title"] for c in cl.list_changes()]
    assert titles == ["old-unread"]
    assert cl.unread_count() == 1


# ── 账本是旁路：坏掉不许炸 ───────────────────────────────────────────

def test_ledger_failure_is_soft(monkeypatch):
    """DB 打不开时所有入口都必须安静降级 —— 记账失败不能反过来
    打断它正在记录的那次变更。"""
    monkeypatch.setattr(cl, "_conn", lambda: None)
    assert cl.record_change(kind="k", tier=cl.TIER_L1, title="t") is None
    assert cl.list_changes() == []
    assert cl.unread_count() == 0
    assert cl.mark_read([1]) == 0
    assert cl.mark_all_read() == 0
    assert cl.purge_old() == 0


def test_schema_is_idempotent():
    assert cl.ensure_schema() is True
    assert cl.ensure_schema() is True
    cl.record_change(kind="k", tier=cl.TIER_L1, title="t")
    assert cl.ensure_schema() is True
    assert len(cl.list_changes()) == 1
