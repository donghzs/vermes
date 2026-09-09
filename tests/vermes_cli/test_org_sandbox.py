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
