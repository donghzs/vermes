"""ACP 握手进程清理：死锁防线 / 进程组清理 / 安全护栏。

背景（2026-09-08 实测，勿删）：
``_handshake`` 结束后若只 ``proc.kill()``，bridge 起的 node **孙进程会继续持有
stdout 写端** → ``for line in proc.stdout`` 的读线程永远等不到 EOF 并死握
TextIOWrapper 锁 → 主线程 ``stream.close()`` 抢同一把锁 → **互锁挂起 45s+**
（faulthandler 抓到：主线程卡 close、读线程卡 readline）。

三条防线缺一不可，各自有对应测试：
  ① spawn 独立进程组（``start_new_session=True``）
  ② ``killpg`` 杀整组，让孙进程一起死
  ③ 读线程 join 超时则放弃 close（fd 泄漏轻于死锁）
  ④ 安全护栏：绝不 killpg 自己的进程组（否则杀死 Vermes 自身）
"""

import os
import subprocess
import threading
import time

import pytest

from agent.copilot_acp_client import AcpAgentTransportBase

_CLEANUP = AcpAgentTransportBase._cleanup_handshake_process


def _alive(pid: int) -> bool:
    """进程是否还活着（僵尸在未被 reap 前也算活，故测试里用轮询等待）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_gone(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _spawn_with_grandchild(tmp_path):
    """起一个「wrapper + 孙进程」组合，模拟 bridge 拉起 node 子进程。

    孙进程 pid 写入 gc.pid。wrapper 用 ``start_new_session=True`` 建立独立进程组，
    与真实 ``_handshake`` 的 spawn 方式保持一致。
    """
    gc_file = tmp_path / "gc.pid"
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        '#!/bin/sh\n'
        'sleep 60 &\n'
        f'echo $! > "{gc_file}"\n'
        'wait\n'
    )
    wrapper.chmod(0o755)
    proc = subprocess.Popen(
        [str(wrapper)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if gc_file.exists() and gc_file.read_text().strip():
            break
        time.sleep(0.05)
    gcpid = int(gc_file.read_text().strip())
    return proc, gcpid


def test_cleanup_kills_grandchild_in_process_group(tmp_path):
    """② killpg 必须连孙进程一起杀——否则它持有 stdout 写端导致读线程永不 EOF。"""
    proc, gcpid = _spawn_with_grandchild(tmp_path)
    assert _alive(gcpid), "前置条件：孙进程应已启动"

    _CLEANUP(None, proc)

    assert _wait_gone(gcpid), (
        "孙进程未被清理：它会继续持有 stdout 写端，读线程等不到 EOF，"
        "进而与主线程 close() 互锁（这正是挂起 45s 的根因）"
    )


def test_cleanup_never_kills_own_process_group(monkeypatch):
    """④ P0 安全护栏：pgid 若是自己所在的组，绝不能 killpg（会杀死 Vermes 自身）。

    未独立成组（或取 pgid 失败）时，``getpgid(proc.pid)`` 会返回调用者自己的组；
    此时 killpg 等于自杀。必须退化为 ``proc.kill()``。
    """
    killed_groups = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: killed_groups.append(pgid))
    monkeypatch.setattr(os, "getpgid", lambda pid: os.getpgrp())

    class FakeProc:
        stdin = stdout = stderr = None

        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    fp = FakeProc()
    _CLEANUP(None, fp)

    assert killed_groups == [], "killpg 了自己的进程组 → 会连同 Vermes 自身一起杀掉"
    assert fp.killed is True, "护栏生效时应退化为只 kill 父进程"


def test_cleanup_does_not_hang_when_reader_thread_stuck(tmp_path):
    """③ 读线程卡死时 cleanup 必须限时返回（宁可漏 fd，不可死锁）。"""
    stuck = threading.Thread(target=lambda: time.sleep(30), daemon=True)
    stuck.start()
    proc, gcpid = _spawn_with_grandchild(tmp_path)

    started = time.time()
    _CLEANUP(None, proc, threads=(stuck,))
    elapsed = time.time() - started

    assert elapsed < 5.0, f"cleanup 挂起 {elapsed:.1f}s（读线程未退出时不应死等 close）"
    assert _wait_gone(gcpid)


def test_handshake_spawns_isolated_process_group(monkeypatch):
    """① spawn 必须带 start_new_session=True——缺失则 killpg 会误杀自己，防线②失效。"""
    import agent.copilot_acp_client as mod

    captured: dict = {}

    class FakeProc:
        stdin = stdout = stderr = None

        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: FakeProc(*a, **kw))

    transport = mod.AcpAgentTransportBase.__new__(mod.AcpAgentTransportBase)
    transport._acp_command = "echo"
    transport._acp_args = []
    transport._acp_cwd = None
    transport._auth_env = None
    transport._auth_value = None

    transport._handshake(timeout_seconds=1)

    assert captured.get("start_new_session") is True, (
        "handshake spawn 缺少 start_new_session=True：进程组不独立，"
        "killpg 会命中 Vermes 自己的组（被护栏拦下）→ 孙进程泄漏 → 读线程等不到 EOF"
    )


@pytest.mark.parametrize("exc", [ProcessLookupError, PermissionError, OSError])
def test_cleanup_survives_getpgid_failures(monkeypatch, exc):
    """取 pgid 失败（进程已退出/权限不足）时不得抛异常，且需退化为 kill。"""

    def _boom(pid):
        raise exc("boom")

    monkeypatch.setattr(os, "getpgid", _boom)

    class FakeProc:
        stdin = stdout = stderr = None

        def __init__(self):
            self.killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    fp = FakeProc()
    _CLEANUP(None, fp)  # 不应抛
    assert fp.killed is True
