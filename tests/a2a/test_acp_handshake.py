"""⑭ 真实 ACP 握手（_handshake）+ 健康检查升级测试。

背景：健康检查原为 ``shutil.which`` 可达性探针（命令在 PATH 即判健康），
无法回答「这 agent 真能 spawn 起来、真能说 ACP 吗」。本轮升级为真实
``initialize`` 握手。

测试纪律：**一律 mock subprocess.Popen，绝不真 spawn 外部命令**。
真 spawn npx 会联网拉包、慢且 flaky（botmode 路由测试曾因此红过）。
握手协议行为用注入的 stdout 行模拟。

覆盖：
- 握手成功 → (True, protocolVersion)
- 握手超时 → (False, timed out) 且**进程被清理**
- 命令不存在 / spawn 失败 → 不抛，返回 (False, 原因)
- initialize 被拒（JSON-RPC error）→ (False, rejected)
- 进程提前退出 → (False, exited early) 带 stderr
- 健康检查集成：快检失败不 spawn；握手能力缺失时退化
- **进程清理是硬要求**（每次握手不留孤儿进程）
"""

from __future__ import annotations

import json
from unittest import mock

import pytest

from agent.copilot_acp_client import AcpAgentTransportBase


def _fake_proc(stdout_lines, stderr_lines=None, poll_value=None):
    """mock Popen：stdout 可迭代 + 带 close，poll 可控（None=存活）。

    stdout 必须是 MagicMock 而非裸 list —— 生产代码清理时会调
    ``stream.close()``，裸 list 没有该方法会让清理断言假失败。
    """
    proc = mock.MagicMock()
    proc.stdin = mock.MagicMock()
    stdout = mock.MagicMock()
    stdout.__iter__ = mock.MagicMock(return_value=iter(stdout_lines))
    proc.stdout = stdout
    stderr = mock.MagicMock()
    stderr.__iter__ = mock.MagicMock(return_value=iter(stderr_lines or []))
    proc.stderr = stderr
    proc.poll.return_value = poll_value
    proc.returncode = None if poll_value is None else poll_value
    return proc


def _init_response(protocol_version: int = 1) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": protocol_version}}
    )


@pytest.fixture
def transport():
    return AcpAgentTransportBase(acp_command="fake-agent", acp_args=["--acp"])


# ── 握手成功 ──────────────────────────────────────────────

def test_handshake_success(transport):
    proc = _fake_proc([_init_response(1)])
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ) as popen:
        ok, detail = transport._handshake(timeout_seconds=5)

    assert ok is True
    assert "protocolVersion=1" in detail
    # spawn argv == command + args
    popen.assert_called_once()
    assert popen.call_args.args[0] == ["fake-agent", "--acp"]


def test_handshake_ignores_session_notifications(transport):
    """握手阶段收到的 session/update 通知应被忽略，只认 id==1 的响应。"""
    lines = [
        json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {}}),
        _init_response(1),
    ]
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=_fake_proc(lines)
    ):
        ok, detail = transport._handshake(timeout_seconds=5)
    assert ok is True
    assert "protocolVersion=1" in detail


# ── 失败路径（都不抛异常，返回 (False, 原因)）────────────────

def test_handshake_timeout(transport):
    """stdout 无响应 → 超时，且必须清理进程。"""
    proc = _fake_proc([])
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        ok, detail = transport._handshake(timeout_seconds=1)
    assert ok is False
    assert "timed out" in detail
    # 关键：超时也必须清理，不留孤儿进程
    assert proc.kill.called, "超时后必须 kill 子进程，否则每次注册留下一个孤儿进程"


def test_handshake_command_not_found(transport):
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", side_effect=FileNotFoundError
    ):
        ok, detail = transport._handshake(timeout_seconds=1)
    assert ok is False
    assert "command not found" in detail


def test_handshake_spawn_oserror(transport):
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen",
        side_effect=OSError("permission denied"),
    ):
        ok, detail = transport._handshake(timeout_seconds=1)
    assert ok is False
    assert "spawn failed" in detail


def test_handshake_rejected_by_agent(transport):
    """agent 回 JSON-RPC error → (False, rejected)。"""
    err = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unsupported"}}
    )
    proc = _fake_proc([err])
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        ok, detail = transport._handshake(timeout_seconds=5)
    assert ok is False
    assert "rejected" in detail
    assert "unsupported" in detail


def test_handshake_process_exited_early(transport):
    """进程提前退出 → 带上 stderr 便于诊断。"""
    proc = _fake_proc([], stderr_lines=["boom: bad config"], poll_value=1)
    proc.returncode = 1
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        ok, detail = transport._handshake(timeout_seconds=5)
    assert ok is False
    assert "exited early" in detail
    assert "boom: bad config" in detail


# ── 进程清理（硬要求）──────────────────────────────────────

@pytest.mark.parametrize(
    "lines,poll_value,expect_kill,label",
    [
        # 成功路径：握手完成后进程仍存活 → 必须 kill（否则成孤儿）
        ([_init_response(1)], None, True, "success-still-alive"),
        # 超时：进程还挂着 → 必须 kill
        ([], None, True, "timeout-still-alive"),
        # 提前退出：进程已死（poll 非 None）→ 不该重复 kill，但仍要关 stdio
        ([], 1, False, "already-exited"),
    ],
)
def test_handshake_always_cleans_up(transport, lines, poll_value, expect_kill, label):
    """任何路径都必须关闭 stdio；仅存活进程需要 kill。"""
    proc = _fake_proc(lines, poll_value=poll_value)
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        transport._handshake(timeout_seconds=1)

    assert proc.kill.called is expect_kill, (
        f"[{label}] kill 调用应为 {expect_kill}（存活进程才需要 kill）"
    )
    assert proc.stdin.close.called, "必须关闭 stdin"
    assert proc.stdout.close.called, "必须关闭 stdout"


def test_cleanup_handles_dead_process_gracefully(transport):
    """进程已退出时 kill 不应抛（幂等）。"""
    proc = _fake_proc([], poll_value=0)
    # poll 返回非 None 表示已退出 → 不应调 kill
    transport._cleanup_handshake_process(proc)
    assert not proc.kill.called
    assert proc.stdin.close.called


def test_cleanup_none_is_noop(transport):
    transport._cleanup_handshake_process(None)  # 不抛


# ── 健康检查集成（_acp_health_check）────────────────────────

def test_health_check_skips_spawn_when_command_missing(transport, monkeypatch):
    """快检失败（命令不在 PATH）→ 直接 unhealthy，不浪费一次 spawn。"""
    import vermes_cli.blueprints.chat as chat_bp

    monkeypatch.setattr("shutil.which", lambda name: None)
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen"
    ) as popen:
        ok, detail = chat_bp._acp_health_check(transport)
    assert ok is False
    assert "not found on PATH" in detail
    popen.assert_not_called(), "命令都不存在，不该白 spawn 一次"


def test_health_check_success_uses_handshake(transport, monkeypatch):
    import vermes_cli.blueprints.chat as chat_bp

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    proc = _fake_proc([_init_response(1)])
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        ok, detail = chat_bp._acp_health_check(transport, timeout_seconds=5)
    assert ok is True
    assert "handshake ok" in detail


def test_health_check_failure_reports_reason(transport, monkeypatch):
    """握手失败 → healthy=False 且 detail 带原因（供前端诊断），不抛异常。"""
    import vermes_cli.blueprints.chat as chat_bp

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    proc = _fake_proc([])
    with mock.patch(
        "agent.copilot_acp_client.subprocess.Popen", return_value=proc
    ):
        ok, detail = chat_bp._acp_health_check(transport, timeout_seconds=1)
    assert ok is False
    assert "handshake failed" in detail
    assert "timed out" in detail


def test_health_check_degrades_when_no_handshake_method(monkeypatch):
    """transport 无 _handshake（极端情况）→ 退化为可达性判定，不崩。"""
    import vermes_cli.blueprints.chat as chat_bp

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    stub = mock.MagicMock(spec=["_acp_command"])  # 无 _handshake 属性
    stub._acp_command = "fake-agent"
    ok, detail = chat_bp._acp_health_check(stub)
    assert ok is True
    assert "handshake unavailable" in detail
