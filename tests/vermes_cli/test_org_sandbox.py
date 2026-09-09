"""Sprint D · ⑤ R3：org → 蜂群执行沙箱委派 单元测试。

覆盖：幂等投递 / ACP 守卫 / workspace·超时透传 / 状态单向回填映射 /
有界轮询 / chat.py _org_runner_factory 三路 gating（委派/ACP 永不进/默认内联）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from vermes_cli import kanban_db as kb
from vermes_cli.blueprints import chat as chat_bp
from vermes_cli.org_sandbox import (
    SANDBOX_ORG_BLOCKED,
    SANDBOX_ORG_DONE,
    SANDBOX_ORG_STALE,
    SANDBOX_ORG_TIMEOUT,
    dispatch_org_subtask_to_sandbox,
    map_sandbox_outcome_to_org,
    org_sandbox_enabled,
    poll_sandbox_outcome,
    run_org_subtask_in_sandbox,
)


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """隔离 VERMES_HOME + 空 kanban DB（照抄 test_kanban_db 脚手架）。"""
    home = tmp_path / ".vermes"
    home.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(home))
    monkeypatch.setattr(__import__("pathlib").Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# ---------------------------------------------------------------------------
# 1) 幂等：同一 org_task_id 重复派发命中已存在 task
# ---------------------------------------------------------------------------
def test_dispatch_is_idempotent(kanban_home):
    with kb.connect() as conn:
        tid1 = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:T1", profile={"id": "p1"},
            instruction="写报告", transport="native",
        )
        tid2 = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:T1", profile={"id": "p1"},
            instruction="写报告（重试）", transport="native",
        )
    assert tid1 == tid2
    with kb.connect() as conn:
        assert kb.get_task(conn, tid1) is not None


# ---------------------------------------------------------------------------
# 2) ACP 守卫：transport=="acp" 直接 raise
# ---------------------------------------------------------------------------
def test_dispatch_rejects_acp(kanban_home):
    with kb.connect() as conn:
        with pytest.raises(ValueError):
            dispatch_org_subtask_to_sandbox(
                conn, org_task_id="org:A", profile={"id": "p1"},
                instruction="x", transport="acp",
            )


def test_ensure_kanban_profile_bridges_missing_profile(kanban_home):
    """桥接：org 岗位 profile.id 无对应 kanban profile 目录时，
    投递前补建（clone default config 拿 key），worker 才不至于 spawn 即死。"""
    from vermes_cli.org_sandbox import _ensure_kanban_profile
    from vermes_cli import profiles as _profiles
    assignee = "researcher"
    # 隔离环境初始无该 profile 目录
    assert not _profiles.profile_exists(assignee)
    ok = _ensure_kanban_profile(assignee)
    assert ok is True
    assert _profiles.profile_exists(assignee)
    # 幂等：再次调用不抛错
    assert _ensure_kanban_profile(assignee) is True


def test_ensure_kanban_profile_default_short_circuits(kanban_home):
    """default 是 ~/.vermes 恒存在，桥接直接 True 不建目录。"""
    from vermes_cli.org_sandbox import _ensure_kanban_profile
    assert _ensure_kanban_profile("default") is True


def test_sandbox_assignee_normalizes_invalid_names():
    """非法岗位 id（带冒号/中文/前缀）→ 稳定 slug；合法名原样返回。"""
    from vermes_cli.org_sandbox import _sandbox_assignee
    # 合法名原样
    assert _sandbox_assignee("researcher") == "researcher"
    assert _sandbox_assignee("coder") == "coder"
    # 非法名 → 稳定 slug（幂等 + 可过 validate）
    s1 = _sandbox_assignee("local:aider")
    s2 = _sandbox_assignee("local:aider")
    assert s1 == s2
    assert s1.startswith("org-") and len(s1) == len("org-") + 12
    # 中文岗位名
    s3 = _sandbox_assignee("sec:汇报总编")
    assert s3.startswith("org-")
    # default 特殊：不 sloted，但 assignee 不会传 default（org 岗位无 default）
    from vermes_cli.profiles import validate_profile_name
    for s in (s1, s3):
        validate_profile_name(s)  # 不抛 ValueError 即合法


# ---------------------------------------------------------------------------
# 3) 透传：workspace_kind / max_runtime_seconds 落到 kanban task
# ---------------------------------------------------------------------------
def test_dispatch_passthrough_workspace_and_timeout(kanban_home):
    with kb.connect() as conn:
        tid = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:W", profile={"id": "p1"},
            instruction="长程任务", transport="cli",
            workspace_kind="worktree", max_runtime_seconds=120,
        )
        task = kb.get_task(conn, tid)
    assert task.workspace_kind == "worktree"
    assert task.max_runtime_seconds == 120
    # 不传 initial_status → create_task 内部落成 ready（等待 dispatcher 领取）
    assert task.status == "ready"


def test_dispatch_writes_assignee_display_for_invalid_name(kanban_home):
    """非法岗位 id（带冒号/中文）→ assignee 列是 slug，assignee_display 保留原始名。"""
    with kb.connect() as conn:
        tid = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:AD", profile={"id": "local:aider"},
            instruction="x", transport="cli",
        )
        task = kb.get_task(conn, tid)
    from vermes_cli.org_sandbox import _sandbox_assignee
    assert task.assignee == _sandbox_assignee("local:aider")  # slug
    assert task.assignee.startswith("org-")
    assert task.assignee_display == "local:aider"  # 原始名


def test_dispatch_omits_assignee_display_for_valid_name(kanban_home):
    """合法岗位 id → assignee 原样，assignee_display 为 None（不冗余）。"""
    with kb.connect() as conn:
        tid = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:VD", profile={"id": "researcher"},
            instruction="x", transport="native",
        )
        task = kb.get_task(conn, tid)
    assert task.assignee == "researcher"
    assert task.assignee_display is None


# ---------------------------------------------------------------------------
# 4) 状态单向回填映射：kanban 终态 → org 子任务状态
# ---------------------------------------------------------------------------
def test_map_outcome_statuses():
    cases = {
        "done": SANDBOX_ORG_DONE,
        "completed": SANDBOX_ORG_DONE,
        "blocked": SANDBOX_ORG_BLOCKED,
        "timed_out": SANDBOX_ORG_TIMEOUT,
        "stale": SANDBOX_ORG_STALE,
    }
    for kanban_status, expect_org in cases.items():
        org_status, out = map_sandbox_outcome_to_org(
            SimpleNamespace(status=kanban_status, result="RESULT_TEXT")
        )
        assert org_status == expect_org
        assert out == "RESULT_TEXT"


def test_map_outcome_falls_back_to_blocked_on_unknown():
    org_status, out = map_sandbox_outcome_to_org(
        SimpleNamespace(status="weird", result="")
    )
    assert org_status == SANDBOX_ORG_BLOCKED
    assert out == "weird"


def test_map_outcome_reads_latest_summary_when_result_empty(kanban_home):
    """回归守卫：worker 用 complete_task(summary=...) 写交付物，tasks.result 留空；
    map 必须经 kb.latest_summary 回退取回真实产出，而非退化成 status 枚举。"""
    with kb.connect() as conn:
        tid = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:S", profile={"id": "p1"},
            instruction="x", transport="native",
        )
        conn.execute(
            "UPDATE tasks SET status='done', result=NULL WHERE id=?", (tid,)
        )
        conn.execute(
            "INSERT INTO task_runs (task_id, profile, status, outcome, summary, "
            "started_at, ended_at) VALUES (?, 'p1', 'done', 'completed', '沙箱OK', "
            "1, 2)",
            (tid,),
        )
        task = kb.get_task(conn, tid)
        assert task.result is None
        org_status, out = map_sandbox_outcome_to_org(task, conn=conn)
    assert org_status == SANDBOX_ORG_DONE
    assert out == "沙箱OK"


# ---------------------------------------------------------------------------
# 5) 有界轮询：预置 done 任务立即返回（不超时挂死）
# ---------------------------------------------------------------------------
def test_poll_returns_terminal_immediately(kanban_home):
    with kb.connect() as conn:
        tid = dispatch_org_subtask_to_sandbox(
            conn, org_task_id="org:P", profile={"id": "p1"},
            instruction="x", transport="native",
        )
        conn.execute(
            "UPDATE tasks SET status='done', result='DONE_OUT' WHERE id=?",
            (tid,),
        )
    with kb.connect() as conn:
        task = poll_sandbox_outcome(conn, tid, timeout_seconds=5, interval_seconds=0.05)
    assert task is not None
    assert task.status == "done"
    assert task.result == "DONE_OUT"


def test_poll_returns_none_when_missing(kanban_home):
    with kb.connect() as conn:
        assert poll_sandbox_outcome(conn, "nope", timeout_seconds=1, interval_seconds=0.05) is None


# ---------------------------------------------------------------------------
# 6) chat.py _org_runner_factory 三路 gating
# ---------------------------------------------------------------------------
def _factory_with_sandbox_connect(monkeypatch, tmp_path):
    db_file = tmp_path / "kanban.db"
    kb.init_db(db_path=db_file)
    real_connect = kb.connect

    def fake_connect(db_path=None, *, board=None):
        return real_connect(db_path=db_file, board=board)

    monkeypatch.setattr(kb, "connect", fake_connect)
    return db_file


def test_runner_delegates_native_to_sandbox_when_enabled(monkeypatch, tmp_path):
    _factory_with_sandbox_connect(monkeypatch, tmp_path)
    monkeypatch.setenv("VERMES_ORG_SANDBOX", "1")
    monkeypatch.setattr(
        chat_bp.org_sandbox, "poll_sandbox_outcome",
        lambda *a, **k: SimpleNamespace(status="done", result="SANDBOX_OUT"),
    )
    runner = chat_bp._org_runner_factory(MagicMock(), "room-x", "room-x")
    out = asyncio.run(runner(
        {"id": "p1", "transport": "native"}, "instr",
        SimpleNamespace(task={"id": "org-1"}),
    ))
    assert out == "SANDBOX_OUT"


def test_runner_acp_never_enters_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("VERMES_ORG_SANDBOX", "1")
    captured = {"sandbox": False}
    real = run_org_subtask_in_sandbox

    def spy(*a, **k):
        captured["sandbox"] = True
        return real(*a, **k)

    monkeypatch.setattr(chat_bp.org_sandbox, "run_org_subtask_in_sandbox", spy)
    monkeypatch.setattr(chat_bp, "_acp_agent_chat_sync", lambda p, i, **k: "ACP_OUT")
    runner = chat_bp._org_runner_factory(MagicMock(), "room-x", "room-x")
    out = asyncio.run(runner(
        {"id": "p1", "transport": "acp"}, "instr",
        SimpleNamespace(task={"id": "o1"}),
    ))
    assert out == "ACP_OUT"
    assert captured["sandbox"] is False


def test_runner_default_off_uses_inline_native(monkeypatch, tmp_path):
    monkeypatch.delenv("VERMES_ORG_SANDBOX", raising=False)
    captured = {"sandbox": False}

    def spy(*a, **k):
        captured["sandbox"] = True
        return "X"

    monkeypatch.setattr(chat_bp.org_sandbox, "run_org_subtask_in_sandbox", spy)
    fake_agent = SimpleNamespace(chat=lambda instr, **k: "INLINE_OUT")

    async def _fake_build(sid, p):
        return fake_agent

    monkeypatch.setattr(chat_bp, "_bot_build_agent", _fake_build)
    runner = chat_bp._org_runner_factory(MagicMock(), "room-x", "room-x")
    out = asyncio.run(runner(
        {"id": "p1", "transport": "native"}, "instr",
        SimpleNamespace(task={"id": "o1"}),
    ))
    assert out == "INLINE_OUT"
    assert captured["sandbox"] is False


def test_org_sandbox_enabled_triple_fallback():
    # 默认 off
    assert org_sandbox_enabled(SimpleNamespace()) is False
    # env 触发
    import os
    os.environ["VERMES_ORG_SANDBOX"] = "1"
    try:
        assert org_sandbox_enabled(SimpleNamespace()) is True
    finally:
        os.environ.pop("VERMES_ORG_SANDBOX", None)
    # _ctx.use_sandbox 属性
    assert org_sandbox_enabled(SimpleNamespace(use_sandbox=True)) is True
    # dict ctx
    assert org_sandbox_enabled({"use_sandbox": True}) is True


def test_runner_delegates_via_dict_ctx_use_sandbox(monkeypatch, tmp_path):
    """UI 开关链路：OrgContext.use_sandbox → runner dict ctx → org_sandbox_enabled 命中。

    模拟房间级开关开启（不依赖 env）：runner 拿到的 _ctx 是 dict，含 use_sandbox=True
    → 应委派沙箱。这钉死 _run_agent 把 ctx.use_sandbox 透传进 dict 的那一行。
    """
    _factory_with_sandbox_connect(monkeypatch, tmp_path)
    monkeypatch.delenv("VERMES_ORG_SANDBOX", raising=False)
    monkeypatch.setattr(
        chat_bp.org_sandbox, "poll_sandbox_outcome",
        lambda *a, **k: SimpleNamespace(status="done", result="SANDBOX_OUT"),
    )
    runner = chat_bp._org_runner_factory(MagicMock(), "room-x", "room-x")
    # 模拟 _run_agent 传进来的 dict ctx（含 use_sandbox=True，来自 OrgContext 房间开关）
    out = asyncio.run(runner(
        {"id": "p1", "transport": "native"}, "instr",
        {"task": {"id": "org-1"}, "use_sandbox": True},
    ))
    assert out == "SANDBOX_OUT"


def test_org_context_carries_use_sandbox():
    """OrgContext 新增 use_sandbox 属性：显式传入即真，默认 False。"""
    from vermes_cli.botmode.org_engine import OrgContext
    mk = lambda **kw: OrgContext(
        task={"id": "t1"}, roles=[], profiles={},
        store=lambda tid, t: None, **kw,
    )
    assert mk().use_sandbox is False
    assert mk(use_sandbox=True).use_sandbox is True
