"""契约测试：approval 队列的 pop 与 outcome 提交必须是同一临界区（#112548 镜像）。

上游 2afb405337c3 修的三层里，Vermes 有等价暴露面的是第 2 层——「pop 队列项」与
「提交 entry.result / entry.event.set()」若分裂（pop 在锁内、commit 在锁外），则
waiter 的 ``_drop_entry``（锁内读 result 后移除）可能在用户已 /approve 之后、
commit 之前抢到锁，把已 acked 的选择 pop-and-lose 成 timeout。

第 1 层（on_result(None) → withdraw_gateway_approval）依赖 server→client 往返协议
（server_requests.send/resolve_response），Vermes 用单向 register_gateway_notify
+ _emit，无该暴露面，故不采纳。第 3 层（settle "set"→"resolved"/"session_closed"）
依赖 request.cancel 通知机制，Vermes 无 settle，故不采纳。
"""

import threading

import pytest

import tools.approval as mod


def _enqueue(session_key, data=None):
    entry = mod._ApprovalEntry(data or {"command": "rm -rf build", "pattern_key": "dangerous"})
    mod._gateway_queues[session_key] = [entry]
    return entry


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    # 清空所有 gateway 队列，避免跨测试泄漏
    for key in list(mod._gateway_queues):
        mod._gateway_queues.pop(key, None)
    mod._gateway_notify_cbs.clear()


def test_resolve_commits_choice_inside_the_same_lock_section(monkeypatch):
    """resolve_gateway_approval 在释放锁之前，entry.result 必须已提交、event 必须已 set。

    用插桩锁捕获 __exit__ 时刻的 result/event 状态：若提交在锁外，释放锁时二者
    仍未就绪，waiter 可抢锁 pop-and-lose（上游 #112548 的原始竞态）。
    """
    session_key = "resolve-lock"
    entry = _enqueue(session_key)
    real_lock = mod._lock
    seen = {}

    class _Instrumented:
        def __enter__(self):
            return real_lock.__enter__()

        def __exit__(self, *exc):
            seen["result_at_release"] = entry.result
            seen["event_at_release"] = entry.event.is_set()
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(mod, "_lock", _Instrumented())
    assert mod.resolve_gateway_approval(session_key, "once") == 1
    assert seen == {"result_at_release": "once", "event_at_release": True}
    # 队列已空，且 entry.result 对 waiter 可见
    assert session_key not in mod._gateway_queues
    assert entry.result == "once"


def test_unregister_commits_deny_inside_lock(monkeypatch):
    """unregister_gateway_notify 释放锁时，被 pop 的 entry 必须已 result="deny" 且 event 已 set。

    与 clear_session 对齐：会话边界清理提交确定的 deny 结果，而非 choice=None，
    让 waiter 醒来时得到确定性 outcome（而非被归一成 timeout）。
    """
    session_key = "unregister-lock"
    entry = _enqueue(session_key)
    mod._gateway_notify_cbs[session_key] = lambda data: None
    real_lock = mod._lock
    seen = {}

    class _Instrumented:
        def __enter__(self):
            return real_lock.__enter__()

        def __exit__(self, *exc):
            seen["result_at_release"] = entry.result
            seen["event_at_release"] = entry.event.is_set()
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(mod, "_lock", _Instrumented())
    mod.unregister_gateway_notify(session_key)
    assert seen == {"result_at_release": "deny", "event_at_release": True}
    assert session_key not in mod._gateway_queues


def test_clear_session_commits_deny_inside_lock(monkeypatch):
    """clear_session 释放锁时，被 pop 的 entry 必须已 result="deny" 且 event 已 set。"""
    session_key = "clear-lock"
    entry = _enqueue(session_key)
    real_lock = mod._lock
    seen = {}

    class _Instrumented:
        def __enter__(self):
            return real_lock.__enter__()

        def __exit__(self, *exc):
            seen["result_at_release"] = entry.result
            seen["event_at_release"] = entry.event.is_set()
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(mod, "_lock", _Instrumented())
    mod.clear_session(session_key)
    assert seen == {"result_at_release": "deny", "event_at_release": True}
    assert session_key not in mod._gateway_queues


def test_resolve_all_commits_every_target_inside_lock(monkeypatch):
    """resolve_all=True 时，所有被 pop 的 entry 在释放锁时都已提交。"""
    session_key = "resolve-all-lock"
    e1 = mod._ApprovalEntry({"command": "a", "pattern_key": "x"})
    e2 = mod._ApprovalEntry({"command": "b", "pattern_key": "y"})
    mod._gateway_queues[session_key] = [e1, e2]
    real_lock = mod._lock
    seen = {}

    class _Instrumented:
        def __enter__(self):
            return real_lock.__enter__()

        def __exit__(self, *exc):
            seen["results_at_release"] = (e1.result, e2.result)
            seen["events_at_release"] = (e1.event.is_set(), e2.event.is_set())
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(mod, "_lock", _Instrumented())
    assert mod.resolve_gateway_approval(session_key, "always", resolve_all=True) == 2
    assert seen == {"results_at_release": ("always", "always"), "events_at_release": (True, True)}
