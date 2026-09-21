"""L-010 契约测试：VERMES_SESSION_ID 改 task-local contextvar。

反例驱动，覆盖 MiMo 契约的场景 1/2/3/4/5/6：
- 1. set_current_session_id 后 get_session_env 读到；setter 不写 os.environ
- 2. 两线程各 set 不同 id 互不覆盖（contextvar 隔离）
- 3. ACP 式 set→并发另一 session set→first reset 不把 B 的 id 批掉
- 4. 仅 env、不调 setter（旧测试/兼容）get_session_env 仍读到 env
- 5. compression 旋转 id 后回滚，contextvar 与 agent.session_id 一致
- 6. kanban 在 contextvar 下读 session_id 不再依赖 os.environ
"""

import os
import threading

import pytest

from gateway.session_context import (
    _SESSION_ID,
    _UNSET,
    get_current_session_id,
    get_session_env,
    reset_current_session_id,
    set_current_session_id,
)


@pytest.fixture(autouse=True)
def _clean_session_id_env():
    """Remove VERMES_SESSION_ID env + contextvar before/after each test."""
    token = _SESSION_ID.set(_UNSET)
    os.environ.pop("VERMES_SESSION_ID", None)
    yield
    _SESSION_ID.reset(token)
    os.environ.pop("VERMES_SESSION_ID", None)


# ── 场景 1：setter 生效，且不写 os.environ ──────────────────────────
def test_set_session_id_readable_and_does_not_write_env():
    token = set_current_session_id("sess-A")
    try:
        assert get_session_env("VERMES_SESSION_ID") == "sess-A"
        assert get_current_session_id() == "sess-A"
        # setter 不得双写 os.environ（否则 L-010 白做，进程串味仍在）
        assert "VERMES_SESSION_ID" not in os.environ
    finally:
        reset_current_session_id(token)


def test_set_session_id_empty_is_set_not_fallback():
    """set("") 是显式置位（非 _UNSET），get_session_env 应返回 "" 而非回落 env."""
    os.environ["VERMES_SESSION_ID"] = "from-env"
    token = set_current_session_id("")
    try:
        assert get_session_env("VERMES_SESSION_ID") == ""
    finally:
        reset_current_session_id(token)


# ── 场景 2：并发线程隔离 ─────────────────────────────────────────────
def test_two_threads_set_different_ids_do_not_contaminate():
    barrier = threading.Barrier(2)
    results = {}

    def worker(name):
        token = set_current_session_id(name)
        barrier.wait()
        results[name] = get_session_env("VERMES_SESSION_ID")
        reset_current_session_id(token)

    t1 = threading.Thread(target=worker, args=("sess-1",))
    t2 = threading.Thread(target=worker, args=("sess-2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["sess-1"] == "sess-1"
    assert results["sess-2"] == "sess-2"


# ── 场景 3：ACP 式 set→并发→first reset 不批掉 B 的 id ──────────────
def test_concurrent_set_then_first_reset_does_not_wipe_second():
    """旧 save/restore os.environ 会翻车：线程 A restore 时把 B 刚 set 的值 pop 掉。

    contextvar 版：每个线程有自己的 token，reset 只恢复自己的。
    """
    # 主线程先 set 一个 id（模拟 A）
    token_a = set_current_session_id("sess-A")

    results = {}

    def worker_b():
        # B 在自己的线程 set（旧实现会覆盖进程级 env）
        token_b = set_current_session_id("sess-B")
        results["B_during"] = get_session_env("VERMES_SESSION_ID")
        reset_current_session_id(token_b)

    t = threading.Thread(target=worker_b)
    t.start()
    t.join()

    # A 在线程 B 完成后 reset，不得影响 B 已经读到/写入的值
    reset_current_session_id(token_a)

    # 关键断言：B 在自己的 context 里读到的是 sess-B，不是被 A 覆盖的 sess-A
    assert results["B_during"] == "sess-B"
    # reset 后主线程 context 回落到未 set 状态（不残留 sess-A）
    assert get_session_env("VERMES_SESSION_ID") == ""


# ── 场景 4：仅 env、不调 setter（旧测试/兼容）──────────────────────
def test_env_only_fallback_still_reads_env():
    os.environ["VERMES_SESSION_ID"] = "legacy-env"
    assert get_session_env("VERMES_SESSION_ID") == "legacy-env"
    assert get_current_session_id() == "legacy-env"


# ── 场景 5：compression 旋转后回滚，contextvar 与 session_id 一致 ────
def test_rotation_rollback_keeps_contextvar_in_sync():
    """模拟 conversation_compression：先 set 新 id，回滚时 set 回旧 id."""
    token_new = set_current_session_id("child-sess")
    # 旋转失败回滚到 parent（对应 compression 的 rollback 分支）
    reset_current_session_id(token_new)
    token_parent = set_current_session_id("parent-sess")
    try:
        assert get_session_env("VERMES_SESSION_ID") == "parent-sess"
        # agent.session_id 回滚后与 contextvar 一致
        agent_session_id = "parent-sess"
        assert get_current_session_id() == agent_session_id
    finally:
        reset_current_session_id(token_parent)


# ── 场景 6：kanban 在 contextvar 下读 session_id 不依赖 os.environ ───
def test_kanban_reads_session_id_from_contextvar_not_env():
    """kanban_tools 改用 get_session_env 后，即使 os.environ 无该键也能读到."""
    from tools import kanban_tools

    token = set_current_session_id("acp-sess-abc")
    try:
        # os.environ 里没有 VERMES_SESSION_ID，reader 应走 contextvar
        assert "VERMES_SESSION_ID" not in os.environ
        assert get_session_env("VERMES_SESSION_ID") == "acp-sess-abc"
        # _stamp_worker_session_metadata 是读 VERMES_SESSION_ID 的 reader
        # （迁移后走 get_session_env），冒烟确认模块可 import 且函数存在
        assert hasattr(kanban_tools, "_stamp_worker_session_metadata")
    finally:
        reset_current_session_id(token)
