"""T3 技能自动采纳 —— 止住 pending 待办债，但不是无脑全收。

背景：技能提取出来一律 status=pending，等用户在面板里点确认。用户不点，
系统学到的东西就永远用不上 —— 「越用越顺手」的链路在最后一米断掉。
采纳是可逆的（一键 reject）、爆炸半径小的，属于 L1：自动做 + 通知 + 可撤回。

这些测试锁住：达标才收、阈值可外置且有 >0 护栏、采纳后账本里那条通知
在技能还 active 时可撤回、以及通知失败不会回退已经生效的采纳。

运行：.venv/bin/python -m pytest tests/agent/test_skill_auto_adopt.py -p no:xdist -o addopts="" -q
"""

import sqlite3

import pytest

import agent.change_ledger as cl
import agent.skill_extractor as se
from agent.skill_extractor import (
    ExtractedSkill,
    SkillExtractor,
    ensure_skill_tables,
    load_skill_adopt_config,
    should_auto_adopt,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "self-model.db"
    conn = sqlite3.connect(db)
    ensure_skill_tables(conn)
    conn.close()
    monkeypatch.setattr(cl, "_db_path", lambda: db)
    import agent.evolution_manager as em
    monkeypatch.setattr(em, "get_self_model_db", lambda: db)
    yield db


def _stub_cfg(monkeypatch, **kw):
    """Stub config.yaml's memory.skillAdopt subtree."""
    import vermes_cli.config as vc
    monkeypatch.setattr(vc, "load_config", lambda: {"memory": {"skillAdopt": kw}})


def _skill(**kw):
    d = dict(cluster_id=1, name="grep_then_read", description="搜完就读",
             tool_sequence=["Grep", "Read"], usage_count=20, success_rate=0.95)
    d.update(kw)
    return ExtractedSkill(**d)


# ── 判定：两个条件都要满足 ───────────────────────────────────────────

def test_high_confidence_and_frequent_is_adopted():
    adopt, why = should_auto_adopt(_skill())
    assert adopt is True
    assert "95%" in why and "20" in why


def test_low_success_rate_stays_pending():
    adopt, why = should_auto_adopt(_skill(success_rate=0.85))
    assert adopt is False
    assert "成功率" in why


def test_rare_skill_stays_pending():
    """成功率再高，只用过几次也不够 —— 3/3 成功不代表这是个稳定模式。"""
    adopt, why = should_auto_adopt(_skill(usage_count=3, success_rate=1.0))
    assert adopt is False
    assert "使用" in why


def test_adoption_bar_is_above_extraction_bar():
    """采纳门槛必须严于提取门槛，否则「自动采纳」= 全收。"""
    cfg = load_skill_adopt_config()
    assert cfg["min_success_rate"] > se.SKILL_SUCCESS_RATE_FLOOR
    assert cfg["min_usage"] > 5          # _find_skill_candidates 的 event_count 门槛


# ── 阈值外置 + P1 >0 护栏 ────────────────────────────────────────────

def test_config_overrides_thresholds(monkeypatch):
    _stub_cfg(monkeypatch, min_success_rate=0.99, min_usage=100)
    cfg = load_skill_adopt_config()
    assert cfg["min_success_rate"] == 0.99
    assert cfg["min_usage"] == 100
    assert should_auto_adopt(_skill(), cfg)[0] is False


def test_zero_threshold_falls_back_to_default(monkeypatch):
    """配 0 = 「全部自动采纳」，正是阈值要防的事 → 按 P1 护栏回落默认。"""
    _stub_cfg(monkeypatch, min_success_rate=0, min_usage=0)
    cfg = load_skill_adopt_config()
    assert cfg["min_success_rate"] == se._SKILL_ADOPT_DEFAULTS["min_success_rate"]
    assert cfg["min_usage"] == se._SKILL_ADOPT_DEFAULTS["min_usage"]
    assert should_auto_adopt(_skill(usage_count=1, success_rate=0.1), cfg)[0] is False


def test_negative_threshold_also_falls_back(monkeypatch):
    _stub_cfg(monkeypatch, min_usage=-5)
    assert load_skill_adopt_config()["min_usage"] == se._SKILL_ADOPT_DEFAULTS["min_usage"]


def test_can_be_disabled(monkeypatch):
    """enabled=false 是明确的用户意愿（收紧），照办。"""
    _stub_cfg(monkeypatch, enabled=False)
    adopt, why = should_auto_adopt(_skill(), load_skill_adopt_config())
    assert adopt is False
    assert "已关闭" in why


def test_unreadable_config_uses_defaults(monkeypatch):
    import vermes_cli.config as vc

    def _boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(vc, "load_config", _boom)
    assert load_skill_adopt_config() == se._SKILL_ADOPT_DEFAULTS


# ── 落库 + 通知 ──────────────────────────────────────────────────────

def _insert(db, skill):
    ex = SkillExtractor(str(db))
    conn = sqlite3.connect(db)
    ensure_skill_tables(conn)
    ex._insert_skill(conn, skill)
    return ex, conn


def _status(db, sid):
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM extracted_skills WHERE id=?", (sid,)).fetchone()
    conn.close()
    return row[0] if row else None


def test_adopt_flips_status_and_notifies(_isolated_db):
    db = _isolated_db
    skill = _skill()
    ex, conn = _insert(db, skill)

    assert ex._maybe_auto_adopt(conn, skill) is True
    conn.close()

    assert _status(db, skill.id) == "active"
    rows = cl.list_changes()
    assert len(rows) == 1
    assert rows[0]["kind"] == cl.KIND_SKILL_ADOPTED
    assert rows[0]["tier"] == cl.TIER_L1
    assert rows[0]["unread"] is True          # 进角标
    assert rows[0]["ref_kind"] == cl.REF_SKILL
    assert rows[0]["ref_id"] == skill.id


def test_below_bar_stays_pending_and_silent(_isolated_db):
    db = _isolated_db
    skill = _skill(usage_count=2)
    ex, conn = _insert(db, skill)

    assert ex._maybe_auto_adopt(conn, skill) is False
    conn.close()

    assert _status(db, skill.id) == "pending"
    assert cl.list_changes() == []            # 没做事就别通知


def test_adopted_skill_is_retractable_while_active(_isolated_db):
    """技能没有 .bak，但「撤回」是打回 rejected —— 不能因为没备份就灰掉。"""
    db = _isolated_db
    skill = _skill()
    ex, conn = _insert(db, skill)
    ex._maybe_auto_adopt(conn, skill)
    conn.close()

    row = cl.list_changes()[0]
    assert row["ref_status"] == "active"
    assert row["retractable"] is True


def test_retracted_skill_is_no_longer_retractable(_isolated_db):
    db = _isolated_db
    skill = _skill()
    ex, conn = _insert(db, skill)
    ex._maybe_auto_adopt(conn, skill)
    conn.close()

    SkillExtractor(str(db)).reject_skill(skill.id)

    row = cl.list_changes()[0]
    assert row["ref_status"] == "rejected"    # 状态回源现查，账本没双写
    assert row["retractable"] is False


def test_notice_failure_does_not_undo_adoption(_isolated_db, monkeypatch):
    """账本是旁路：通知写不进去，采纳照样算数（只是会 warning）。"""
    db = _isolated_db
    monkeypatch.setattr(cl, "record_change",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("ledger down")))
    skill = _skill()
    ex, conn = _insert(db, skill)

    assert ex._maybe_auto_adopt(conn, skill) is True
    conn.close()
    assert _status(db, skill.id) == "active"


# ── T6 接线：档位真的作用到采纳上（光有原语不算数）────────────────────

def test_conservative_mode_keeps_skill_pending(_isolated_db, monkeypatch):
    """tier_mode=conservative → 达标也不自动启用，退回人工确认。"""
    db = _isolated_db
    monkeypatch.setattr(se, "_adopt_tier", lambda: "L2")
    skill = _skill()
    ex, conn = _insert(db, skill)

    assert ex._maybe_auto_adopt(conn, skill) is False
    conn.close()

    assert _status(db, skill.id) == "pending"
    assert cl.list_changes() == []            # 没做事，也就没有可撤回的通知


def test_adopt_tier_reads_config(monkeypatch):
    import tools.approval as ap
    monkeypatch.setattr(ap, "_get_approval_config",
                        lambda: {"tier_mode": "conservative"})
    assert se._adopt_tier() == "L2"
    monkeypatch.setattr(ap, "_get_approval_config",
                        lambda: {"tier_mode": "balanced"})
    assert se._adopt_tier() == "L1"


def test_adopt_tier_falls_back_to_baseline_when_unreadable(monkeypatch):
    """读不出偏好 ≠ 改变安全基线：该动作本身可逆且有通知，照旧 L1。"""
    def _boom(*a, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr("tools.approval.effective_tier", _boom)
    assert se._adopt_tier() == "L1"
