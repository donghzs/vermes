"""P2 AEGIS 闭环提案引擎 — 聚焦测试（四阶段 + 护栏 + 过期）。

不依赖真实 LLM / 真实 ~/.vermes：所有外部依赖（memory_flags DB、self-model
DB、LLM）均可注入或 monkeypatch。

运行：.venv/bin/python -m pytest tests/agent/test_aegis_proposals.py -p no:xdist -o addopts="" -q
"""

import datetime
import json
import sqlite3

import pytest

from agent import emergence_critic as ec
from agent import evolution_manager as em
from agent import memory_reflection as mr


@pytest.fixture(autouse=True)
def _clear_critic_cache():
    ec.clear_critic_cache()
    yield
    ec.clear_critic_cache()


# ── fixtures / helpers ────────────────────────────────────────────────

def _make_index_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, source TEXT)")
    conn.execute(
        "CREATE TABLE memory_flags ("
        "id INTEGER PRIMARY KEY, memory_id TEXT, flag_type TEXT, "
        "confidence REAL, status TEXT)"
    )
    return conn


def _seed(conn, specs):
    """specs: list of dict(mem_id=Optional[int], ftype, conf, source=Optional).

    mem_id=None → orphan (NULL memory_id, no memory row).
    source=None → no memory row inserted (orphan-style).
    """
    fid = 0
    for spec in specs:
        fid += 1
        mem_id = spec.get("mem_id")
        ftype = spec["ftype"]
        conf = spec["conf"]
        source = spec.get("source")
        if mem_id is not None and source is not None:
            conn.execute(
                "INSERT INTO memories (id, source) VALUES (?,?)", (mem_id, source)
            )
        conn.execute(
            "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, status) "
            "VALUES (?,?,?,?,'open')",
            (fid, None if mem_id is None else str(mem_id), ftype, conf),
        )
    conn.commit()


def _make_selfmodel_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE raw_events (id INTEGER PRIMARY KEY, timestamp TEXT, "
        "tool_name TEXT, args_preview TEXT, result_preview TEXT, success INTEGER, "
        "duration REAL, session_id TEXT, turn_number INTEGER)"
    )
    conn.execute(
        "CREATE VIEW v_outcomes AS SELECT id, timestamp, tool_name AS tool, "
        "success, result_preview AS details, duration, '' AS domain, "
        "'' AS error_type, 'default' AS role, tool_name AS task, '' AS action, "
        "CASE WHEN success=0 THEN result_preview ELSE '' END AS error_msg "
        "FROM raw_events"
    )
    return conn


def _insert_outcome(conn, tool, days_ago, success):
    ts = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn.execute(
        "INSERT INTO raw_events (timestamp, tool_name, args_preview, "
        "result_preview, success, duration, session_id, turn_number) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (ts, tool, "{}", "ok", 1 if success else 0, 0.1, "t", 0),
    )


# ── 确定性闸门：回放正确性 ─────────────────────────────────────────────

def test_replay_auto_resolve_counts(tmp_path):
    db = tmp_path / "mem.db"
    conn = _make_index_db(db)
    # 3 skill dups + 2 orphan + 2 outdated（全部会被 demote）；4 个非 skill 现存 dup（eligible 但不 demote）
    specs = [
        {"mem_id": 1, "ftype": "duplicate", "conf": 0.95, "source": "skill"},
        {"mem_id": 2, "ftype": "duplicate", "conf": 0.92, "source": "skill"},
        {"mem_id": 3, "ftype": "duplicate", "conf": 0.90, "source": "skill"},
        {"mem_id": None, "ftype": "duplicate", "conf": 0.95},  # orphan
        {"mem_id": None, "ftype": "duplicate", "conf": 0.91},  # orphan
        {"mem_id": 6, "ftype": "duplicate", "conf": 0.95, "source": "user"},
        {"mem_id": 7, "ftype": "duplicate", "conf": 0.93, "source": "user"},
        {"mem_id": 8, "ftype": "duplicate", "conf": 0.91, "source": "user"},
        {"mem_id": 9, "ftype": "duplicate", "conf": 0.90, "source": "user"},
        {"mem_id": 11, "ftype": "outdated", "conf": 0.90},
        {"mem_id": 12, "ftype": "outdated", "conf": 0.86},
    ]
    _seed(conn, specs)
    conn.close()

    r = ec.replay_auto_resolve(db_path=str(db), dup_threshold=0.9, out_threshold=0.85)
    # eligible: 9 dup(>=0.9) + 2 outdated = 11；would_demote: 3 skill+2 orphan+2 outdated = 7
    assert r["eligible"] == 11
    assert r["would_demote"] == 7
    assert abs(r["safe_rate"] - 7 / 11) < 1e-6


# ── 确定性闸门：通过 / 精度降拒 / 数量爆拒 ────────────────────────────

def _build_index(tmp_path, specs):
    db = tmp_path / "mem.db"
    conn = _make_index_db(db)
    _seed(conn, specs)
    conn.close()
    return db


def test_gate_pass_when_composition_stable(tmp_path):
    # 仅 skill/orphan/outdated，无 非skill 现存 dup → 降阈值不稀释
    db = _build_index(tmp_path, [
        {"mem_id": 1, "ftype": "duplicate", "conf": 0.95, "source": "skill"},
        {"mem_id": 2, "ftype": "duplicate", "conf": 0.90, "source": "skill"},
        {"mem_id": None, "ftype": "duplicate", "conf": 0.95},
        {"mem_id": 11, "ftype": "outdated", "conf": 0.90},
    ])
    old = {"duplicate": 0.9, "outdated": 0.85, "cluster_min_interval": 60, "merge_cleanup": 0.7}
    new = dict(old, duplicate=0.85)
    g = ec.run_deterministic_gate(db_path=str(db), new_cfg=new, old_cfg=old)
    assert g["passed"] is True
    assert g["precision_ok"] is True
    assert g["count_ok"] is True


def test_gate_fails_on_precision_dilution(tmp_path):
    # 旧配置 0.9 时有 4 个 非skill 现存 dup(>=0.9)；降到 0.7 又拉进 5 个非skill
    # → eligible 暴涨但 would_demote 不变 → safe_rate 稀释 → 精度拒
    specs = (
        [{"mem_id": i, "ftype": "duplicate", "conf": 0.90 + 0.01 * i, "source": "skill"} for i in range(3)]
        + [{"mem_id": None, "ftype": "duplicate", "conf": 0.95}]
        + [{"mem_id": None, "ftype": "duplicate", "conf": 0.91}]
        + [{"mem_id": 10 + i, "ftype": "duplicate", "conf": 0.90 + 0.01 * i, "source": "user"} for i in range(4)]
        + [{"mem_id": 20 + i, "ftype": "duplicate", "conf": 0.70 + 0.02 * i, "source": "user"} for i in range(5)]
        + [{"mem_id": 30 + i, "ftype": "outdated", "conf": 0.90} for i in range(2)]
    )
    db = _build_index(tmp_path, specs)
    old = {"duplicate": 0.9, "outdated": 0.85, "cluster_min_interval": 60, "merge_cleanup": 0.7}
    new = dict(old, duplicate=0.7)
    g = ec.run_deterministic_gate(db_path=str(db), new_cfg=new, old_cfg=old)
    assert g["passed"] is False
    assert g["precision_ok"] is False
    assert g["count_ok"] is True  # 数量未爆（非skill 不 demote）


def test_gate_fails_on_count_explosion(tmp_path):
    # 旧 0.95（仅 1 个 skill dup）；新 0.7（10 个 skill dup）→ would_demote 10x → 数量爆拒
    specs = (
        [{"mem_id": i, "ftype": "duplicate", "conf": 0.70 + 0.02 * i, "source": "skill"} for i in range(10)]
    )
    db = _build_index(tmp_path, specs)
    old = {"duplicate": 0.95, "outdated": 0.85, "cluster_min_interval": 60, "merge_cleanup": 0.7}
    new = dict(old, duplicate=0.7)
    g = ec.run_deterministic_gate(db_path=str(db), new_cfg=new, old_cfg=old)
    assert g["passed"] is False
    assert g["count_ok"] is False
    assert g["count_delta"] > 1.5


# ── 硬编码护栏：删表/设0/越界/越权段必拒 ─────────────────────────────

def test_hardcoded_guard_rejects_zero():
    ok, reason = ec.hardcoded_guard({"memory": {"autoResolve": {"duplicate": 0}}})
    assert ok is False and "0" in reason


def test_hardcoded_guard_rejects_non_autoResolve_section():
    ok, _ = ec.hardcoded_guard({"model": {"temperature": 0.5}})
    assert ok is False


def test_hardcoded_guard_rejects_unknown_dial():
    ok, _ = ec.hardcoded_guard({"memory": {"autoResolve": {"bogus": 0.5}}})
    assert ok is False


def test_hardcoded_guard_rejects_out_of_range():
    ok, _ = ec.hardcoded_guard({"memory": {"autoResolve": {"duplicate": 1.5}}})
    assert ok is False
    ok, _ = ec.hardcoded_guard({"memory": {"autoResolve": {"cluster_min_interval": 99999}}})
    assert ok is False


def test_hardcoded_guard_accepts_valid():
    ok, _ = ec.hardcoded_guard({"memory": {"autoResolve": {"duplicate": 0.85}}})
    assert ok is True


# ── Critic 闸门：批处理 / 否决 / fail-open / 缓存 ────────────────────

def test_critic_accepts_safe():
    fake = lambda p: {"final": '[{"idx":1,"safe":true,"concerns":"","confidence":0.9}]'}
    v = ec.critic_review([{"task_type": "x"}], llm_call=fake)
    assert len(v) == 1 and v[0]["safe"] is True and v[0]["confidence"] == 0.9


def test_critic_rejects_unsafe():
    fake = lambda p: {"final": '[{"idx":1,"safe":false,"concerns":"risky","confidence":0.9}]'}
    v = ec.critic_review([{"task_type": "x"}], llm_call=fake)
    assert v[0]["safe"] is False


def test_critic_fail_open_on_llm_error():
    fake = lambda p: (_ for _ in ()).throw(RuntimeError("boom"))
    assert ec.critic_review([{"task_type": "x"}], llm_call=fake) == []


def test_critic_caches_within_ttl():
    calls = {"n": 0}
    def fake(p):
        calls["n"] += 1
        return {"final": '[{"idx":1,"safe":true,"concerns":"","confidence":0.9}]'}
    cands = [{"task_type": "web", "config_patch": {"memory": {"autoResolve": {"duplicate": 0.85}}}}]
    v1 = ec.critic_review(cands, llm_call=fake)
    v2 = ec.critic_review(cands, llm_call=fake)
    assert calls["n"] == 1  # second hit cache
    assert v2[0].get("cached") is True


# ── 提案队列 CRUD + 过期 ──────────────────────────────────────────────

@pytest.fixture
def tmp_selfmodel(tmp_path, monkeypatch):
    db = tmp_path / "self-model.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE raw_events (id INTEGER PRIMARY KEY)")
    conn.close()
    monkeypatch.setattr(em, "get_self_model_db", lambda: db)
    return db


def test_proposal_record_and_get(tmp_selfmodel):
    pid = em.record_proposal(
        phase="A→B→C→D", task_type="web", title="t", rationale="r",
        target_kind="config", config_patch={"memory": {"autoResolve": {"duplicate": 0.85}}},
        status="proposed",
    )
    assert pid is not None
    rows = em.get_proposals(status="proposed")
    assert len(rows) == 1
    assert rows[0]["task_type"] == "web"
    assert json.loads(rows[0]["config_patch"])["memory"]["autoResolve"]["duplicate"] == 0.85


def test_proposal_records_bak_path_and_deadline(tmp_selfmodel):
    """L1 撤回契约：bak_path / retract_deadline 必须能落库并读回。"""
    pid = em.record_proposal(
        phase="A→B→C→D (auto)", task_type="web", title="t", rationale="r",
        target_kind="config", status="auto_applied",
        bak_path="/tmp/config.yaml.bak.20260802170000",
        retract_deadline="2026-08-03T17:00:00+00:00",
        applied_by="agent",
    )
    row = em.get_proposal(pid)
    assert row["bak_path"] == "/tmp/config.yaml.bak.20260802170000"
    assert row["retract_deadline"] == "2026-08-03T17:00:00+00:00"
    assert row["applied_by"] == "agent" and row["applied_at"]


def test_auto_apply_binds_its_own_backup(tmp_selfmodel, monkeypatch):
    """回归：连续两次自动 apply，每条提案必须绑定*自己*那次的备份。

    早期实现靠「找目录里最新的 .bak」反推，导致撤回第一次变更时会错误地
    还原成第二次变更的快照。
    """
    import agent.emergent_change as emc
    import vermes_cli.config as vcfg

    monkeypatch.setattr(vcfg, "load_config", lambda: {"memory": {}})

    baks = iter(["/tmp/c.yaml.bak.001", "/tmp/c.yaml.bak.002"])

    class _Result:
        committed = True
        def __init__(self):
            self.backup_path = next(baks)

    class _Pipe:
        def apply_change(self, proposal, force=False):
            return _Result()

    monkeypatch.setattr(emc, "get_pipeline", lambda: _Pipe())

    cand = {"task_type": "web", "title": "t", "rationale": "r",
            "config_patch": {"memory": {"autoResolve": {"duplicate": 0.85}}}}
    assert mr._auto_apply_proposal(cand, {"safe": True}, {"passed": True}, "/tmp/c.yaml")
    assert mr._auto_apply_proposal(cand, {"safe": True}, {"passed": True}, "/tmp/c.yaml")

    rows = sorted(em.get_proposals(status="auto_applied"), key=lambda r: r["id"])
    assert [r["bak_path"] for r in rows] == ["/tmp/c.yaml.bak.001", "/tmp/c.yaml.bak.002"]
    # 撤回截止时间应落在未来
    for r in rows:
        assert datetime.datetime.fromisoformat(r["retract_deadline"]) > \
            datetime.datetime.now(datetime.timezone.utc)


def test_proposal_update_status(tmp_selfmodel):
    pid = em.record_proposal(phase="x", task_type="web", title="t", rationale="r",
                             target_kind="config", status="proposed")
    assert em.update_proposal_status(pid, "applied", applied_by="user") is True
    rows = em.get_proposals(status="applied")
    assert len(rows) == 1 and rows[0]["applied_by"] == "user"


def test_proposal_expire_stale(tmp_selfmodel):
    # 插入一个陈旧 proposed（created 远早于 7 天）与一个新鲜 proposed
    em.ensure_proposals_schema()
    conn = sqlite3.connect(str(tmp_selfmodel))
    conn.execute(
        "INSERT INTO evolution_proposals (phase, task_type, title, rationale, "
        "target_kind, status, created) VALUES (?,?,?,?,?,?,?)",
        ("x", "old", "o", "r", "config", "proposed",
         (datetime.datetime.now() - datetime.timedelta(days=30)).isoformat()),
    )
    conn.execute(
        "INSERT INTO evolution_proposals (phase, task_type, title, rationale, "
        "target_kind, status, created) VALUES (?,?,?,?,?,?,?)",
        ("x", "new", "n", "r", "config", "proposed", datetime.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
    n = em.expire_stale_proposals(max_age_days=7)
    assert n == 1
    assert len(em.get_proposals(status="expired")) == 1
    assert len(em.get_proposals(status="proposed")) == 1


# ── Phase A 退化点检测 ────────────────────────────────────────────────

def test_discover_regression_points(tmp_path, monkeypatch):
    db = tmp_path / "self-model.db"
    conn = _make_selfmodel_db(db)
    # web: 30d 内 20 条成功 + 近 7d 10 条全失败 → 退化
    for _ in range(20):
        _insert_outcome(conn, "web", 15, success=True)
    for _ in range(10):
        _insert_outcome(conn, "web", 3, success=False)
    # fine: 全成功
    for _ in range(20):
        _insert_outcome(conn, "fine", 10, success=True)
    conn.commit()
    conn.close()
    monkeypatch.setattr(em, "get_self_model_db", lambda: db)
    points = mr._discover_regression_points()
    tasks = {p["task_type"] for p in points}
    assert "web" in tasks
    assert "fine" not in tasks
    web = next(p for p in points if p["task_type"] == "web")
    assert web["delta"] < 0  # 近7d 成功率低于基线


# ── 端到端：_scan_evolution_proposals 入队（A→B→C→D 全链路）──────────

def test_scan_evolution_proposals_records_proposal(tmp_path, monkeypatch):
    # self-model.db（退化点）
    sm = tmp_path / "self-model.db"
    conn = _make_selfmodel_db(sm)
    for _ in range(20):
        _insert_outcome(conn, "web", 15, success=True)
    for _ in range(10):
        _insert_outcome(conn, "web", 3, success=False)
    conn.commit()
    conn.close()
    monkeypatch.setattr(em, "get_self_model_db", lambda: sm)

    # memory_index.db（仅 skill/orphan/outdated → 闸门稳定通过）
    idx = tmp_path / "memory_index.db"
    conn = _make_index_db(idx)
    _seed(conn, [
        {"mem_id": 1, "ftype": "duplicate", "conf": 0.95, "source": "skill"},
        {"mem_id": 2, "ftype": "duplicate", "conf": 0.90, "source": "skill"},
        {"mem_id": None, "ftype": "duplicate", "conf": 0.95},
        {"mem_id": 11, "ftype": "outdated", "conf": 0.90},
    ])
    conn.close()
    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: idx)

    # 反射 state（独立 3600s 门控）用内存字典
    state = {}
    monkeypatch.setattr(mr, "_load_state", lambda: dict(state))
    monkeypatch.setattr(mr, "_save_state", lambda s: (state.clear(), state.update(s)))

    # Phase B / C 用注入的确定性数据（不调真实 LLM）
    cand = {"task_type": "web", "title": "t", "rationale": "r",
            "config_patch": {"memory": {"autoResolve": {"duplicate": 0.85}}},
            "expected_effect": "e"}
    monkeypatch.setattr(mr, "_generate_b1_candidates", lambda pts, llm_call=None: [cand])
    monkeypatch.setattr(ec, "critic_review",
                        lambda c, outcomes_summary="", llm_call=None:
                        [{"safe": True, "concerns": "", "confidence": 0.9}])
    # auto_apply 关闭 → 走 proposed 路径（入待审队列）
    monkeypatch.setattr(mr, "_is_auto_apply_enabled", lambda: False)

    created = mr._scan_evolution_proposals(llm_call=lambda p: {"final": "[]"})
    assert created == 1
    # auto_apply 关闭 → 进入 proposed 队列
    rows = em.get_proposals(status="proposed")
    assert len(rows) == 1
    assert rows[0]["target_kind"] == "config"
    assert json.loads(rows[0]["config_patch"])["memory"]["autoResolve"]["duplicate"] == 0.85
    # 闸门结果应记录且通过
    det = json.loads(rows[0]["deterministic_result"])
    assert det["passed"] is True


# ── auto_apply 开启时走自动 apply 路径 ──────────────────────────────

def test_scan_evolution_proposals_auto_apply(tmp_path, monkeypatch):
    """When auto_apply is enabled, passed proposals go to auto_applied."""
    sm = tmp_path / "self-model.db"
    conn = _make_selfmodel_db(sm)
    for _ in range(20):
        _insert_outcome(conn, "web", 15, success=True)
    for _ in range(10):
        _insert_outcome(conn, "web", 3, success=False)
    conn.commit()
    conn.close()
    monkeypatch.setattr(em, "get_self_model_db", lambda: sm)

    idx = tmp_path / "memory_index.db"
    conn = _make_index_db(idx)
    _seed(conn, [
        {"mem_id": 1, "ftype": "duplicate", "conf": 0.95, "source": "skill"},
        {"mem_id": 2, "ftype": "duplicate", "conf": 0.90, "source": "skill"},
        {"mem_id": None, "ftype": "duplicate", "conf": 0.95},
        {"mem_id": 11, "ftype": "outdated", "conf": 0.90},
    ])
    conn.close()
    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: idx)

    state = {}
    monkeypatch.setattr(mr, "_load_state", lambda: dict(state))
    monkeypatch.setattr(mr, "_save_state", lambda s: (state.clear(), state.update(s)))

    cand = {"task_type": "web", "title": "auto-test", "rationale": "r",
            "config_patch": {"memory": {"autoResolve": {"duplicate": 0.88}}},
            "expected_effect": "e"}
    monkeypatch.setattr(mr, "_generate_b1_candidates", lambda pts, llm_call=None: [cand])
    monkeypatch.setattr(ec, "critic_review",
                        lambda c, outcomes_summary="", llm_call=None:
                        [{"safe": True, "concerns": "", "confidence": 0.9}])
    # auto_apply 开启 → 走自动 apply 路径
    monkeypatch.setattr(mr, "_is_auto_apply_enabled", lambda: True)
    # mock _auto_apply_proposal 避免真写文件
    captured = {}
    def fake_auto_apply(c, v, g, p):
        captured["title"] = c.get("title")
        captured["patch"] = c.get("config_patch")
        captured["critic"] = v
        captured["gate"] = g
        # record_proposal 会在真实 DB 中创建记录
        em.record_proposal(
            phase="A→B→C→D (auto)",
            task_type=c.get("task_type", ""),
            title=c.get("title", ""),
            rationale=c.get("rationale", ""),
            target_kind="config",
            target_path=p,
            config_patch=c.get("config_patch"),
            critic_verdict=v,
            deterministic_result=g,
            status="auto_applied",
        )
        return True
    monkeypatch.setattr(mr, "_auto_apply_proposal", fake_auto_apply)

    created = mr._scan_evolution_proposals(llm_call=lambda p: {"final": "[]"})
    assert created == 1
    assert captured["title"] == "auto-test"
    assert captured["patch"]["memory"]["autoResolve"]["duplicate"] == 0.88
    # auto_applied 队列应有 1 条
    rows = em.get_proposals(status="auto_applied")
    assert len(rows) == 1
    assert rows[0]["title"] == "auto-test"
    # proposed 队列应为空
    pending = em.get_proposals(status="proposed")
    assert len(pending) == 0


# ── T4 幅度护栏：改动过猛即便双闸门都过也强制人工审 ──────────────────

def _fake_cfg(dup=0.9, max_delta=None):
    cfg = {"memory": {"autoResolve": {"duplicate": dup, "outdated": 0.85,
                                      "cluster_min_interval": 60,
                                      "merge_cleanup": 0.7}},
           "evolution": {}}
    if max_delta is not None:
        cfg["evolution"]["autoApplyMaxDelta"] = max_delta
    return cfg


def _patch_cfg(monkeypatch, **kw):
    import vermes_cli.config as vc
    monkeypatch.setattr(vc, "load_config", lambda: _fake_cfg(**kw))


def test_magnitude_guard_allows_small_delta(monkeypatch):
    _patch_cfg(monkeypatch)
    over, reason = mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.85}}})   # 0.9→0.85 = 5.6%
    assert over is False and reason == ""


def test_magnitude_guard_blocks_large_delta(monkeypatch):
    _patch_cfg(monkeypatch)
    over, reason = mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.5}}})    # 0.9→0.5 = 44%
    assert over is True
    assert "duplicate" in reason and "44%" in reason


def test_magnitude_guard_uses_effective_autoresolve_baseline(monkeypatch):
    """基准要走 _load_auto_resolve_config（含别名归一/默认兜底），
    否则用户没显式写该键时会被误判成"新增 dial 无基准"。"""
    import vermes_cli.config as vc
    monkeypatch.setattr(vc, "load_config", lambda: {"memory": {}, "evolution": {}})
    over, _ = mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.85}}})   # 兜底基准 0.9
    assert over is False


def test_magnitude_guard_unknown_key_is_conservative(monkeypatch):
    _patch_cfg(monkeypatch)
    over, reason = mr._exceeds_magnitude({"memory": {"brandNew": {"dial": 3}}})
    assert over is True
    assert "无此项" in reason


def test_magnitude_guard_ignores_bools_and_empty(monkeypatch):
    _patch_cfg(monkeypatch)
    assert mr._exceeds_magnitude({}) == (False, "")
    assert mr._exceeds_magnitude(None) == (False, "")
    # 布尔翻转不适用"相对幅度"，交给 hardcoded_guard / Critic
    assert mr._exceeds_magnitude({"memory": {"flag": True}})[0] is False


def test_magnitude_threshold_is_externalized(monkeypatch):
    _patch_cfg(monkeypatch, max_delta=0.6)
    over, _ = mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.5}}})    # 44% < 60%
    assert over is False


def test_magnitude_threshold_zero_injection_guard(monkeypatch):
    """写 0 不能把护栏关掉（沿用 P1 的 >0 注入护栏）。"""
    _patch_cfg(monkeypatch, max_delta=0)
    over, _ = mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.5}}})
    assert over is True
    _patch_cfg(monkeypatch, max_delta=-1)
    assert mr._exceeds_magnitude(
        {"memory": {"autoResolve": {"duplicate": 0.5}}})[0] is True


def test_magnitude_guard_fails_safe_on_config_error(monkeypatch):
    import vermes_cli.config as vc
    def _boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(vc, "load_config", _boom)
    over, reason = mr._exceeds_magnitude({"memory": {"autoResolve": {"duplicate": 0.5}}})
    assert over is True and "保守降级" in reason


def test_scan_routes_large_delta_to_l2_queue(tmp_path, monkeypatch):
    """端到端：双闸门都过，但幅度 >20% → 不自动 apply，进 proposed 队列。"""
    sm = tmp_path / "self-model.db"
    conn = _make_selfmodel_db(sm)
    for _ in range(20):
        _insert_outcome(conn, "web", 15, success=True)
    for _ in range(10):
        _insert_outcome(conn, "web", 3, success=False)
    conn.commit()
    conn.close()
    monkeypatch.setattr(em, "get_self_model_db", lambda: sm)

    idx = tmp_path / "memory_index.db"
    conn = _make_index_db(idx)
    _seed(conn, [
        {"mem_id": 1, "ftype": "duplicate", "conf": 0.95, "source": "skill"},
        {"mem_id": 2, "ftype": "duplicate", "conf": 0.90, "source": "skill"},
        {"mem_id": None, "ftype": "duplicate", "conf": 0.95},
        {"mem_id": 11, "ftype": "outdated", "conf": 0.90},
    ])
    conn.close()
    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: idx)

    state = {}
    monkeypatch.setattr(mr, "_load_state", lambda: dict(state))
    monkeypatch.setattr(mr, "_save_state", lambda s: (state.clear(), state.update(s)))

    # 0.9 → 0.6 = 33% > 20%
    cand = {"task_type": "web", "title": "too-aggressive", "rationale": "r",
            "config_patch": {"memory": {"autoResolve": {"duplicate": 0.6}}},
            "expected_effect": "e"}
    monkeypatch.setattr(mr, "_generate_b1_candidates", lambda pts, llm_call=None: [cand])
    monkeypatch.setattr(ec, "critic_review",
                        lambda c, outcomes_summary="", llm_call=None:
                        [{"safe": True, "concerns": "", "confidence": 0.9}])
    monkeypatch.setattr(mr, "_is_auto_apply_enabled", lambda: True)
    # 自动 apply 若被误触发，测试直接失败
    def _must_not_apply(*a, **k):
        raise AssertionError("magnitude guard should have routed this to L2")
    monkeypatch.setattr(mr, "_auto_apply_proposal", _must_not_apply)

    created = mr._scan_evolution_proposals(llm_call=lambda p: {"final": "[]"})
    assert created == 1
    assert em.get_proposals(status="auto_applied") == []
    rows = em.get_proposals(status="proposed")
    assert len(rows) == 1
    det = json.loads(rows[0]["deterministic_result"])
    assert det["passed"] is True          # 闸门本身是过的
    assert det["tier"] == "L2"            # 但被幅度护栏降级
    assert "相对变化" in det["tier_reason"]
