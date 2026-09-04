"""⑤ MCP 指挥中心：per-tool 调用监控测试。

覆盖：
  * ``_record_mcp_call`` 成功/失败累加与派生字段（avg_ms / success_rate）
  * ``_is_error_payload`` 各分支（含**刻意**与旧内联写法不同的 list 判定）
  * 容量上限 ``_MAX_TRACKED_MCP_TOOLS``（超限静默丢弃新条目）
  * ``_make_tool_handler`` 端到端埋点（成功 / 工具返回错误 / 抛异常 三态）
  * fail-open：统计异常不影响调用链路

状态隔离说明（重要）：
    ``_MCP_CALL_STATS`` 是模块级全局。容量上限测试会把它塞满 500 条，
    若不在每个用例前后清理，**后续所有用例的新条目都会被容量上限丢弃**
    （这本身正确，但会让无关用例诡异失败）。故用 autouse fixture 双向清理。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

import tools.mcp_tool as mt


@pytest.fixture(autouse=True)
def _clean_stats():
    """每个用例前后都清空统计，避免容量上限测试污染其他用例。"""
    mt.reset_mcp_call_stats()
    yield
    mt.reset_mcp_call_stats()


def _one(server="srv", tool="do_thing"):
    for s in mt.get_mcp_call_stats():
        if s["server"] == server and s["tool"] == tool:
            return s
    return None


# --------------------------------------------------------------------------
# _record_mcp_call / get_mcp_call_stats
# --------------------------------------------------------------------------

def test_record_success_and_error_accumulates():
    mt._record_mcp_call("srv", "t1", True, 100.0)
    mt._record_mcp_call("srv", "t1", True, 300.0)
    mt._record_mcp_call("srv", "t1", False, 50.0, "boom")

    s = _one(tool="t1")
    assert s is not None, "调用统计未记录"
    assert s["calls"] == 3
    assert s["errors"] == 1
    assert s["total_ms"] == 450.0
    assert s["max_ms"] == 300.0
    assert s["avg_ms"] == 150.0                 # 450 / 3
    assert s["success_rate"] == round(2 / 3, 4)
    assert s["last_error"] == "boom"
    assert s["last_error_at"] > 0
    assert s["last_ok_at"] > 0


def test_record_separates_server_and_tool():
    """key 是 "server/tool" —— 同名工具在不同 server 下必须分开统计。"""
    mt._record_mcp_call("srvA", "search", True, 10.0)
    mt._record_mcp_call("srvB", "search", True, 20.0)
    mt._record_mcp_call("srvA", "fetch", True, 30.0)

    stats = mt.get_mcp_call_stats()
    assert len(stats) == 3, f"应有 3 条独立统计，实际 {len(stats)}"
    assert _one("srvA", "search")["calls"] == 1
    assert _one("srvB", "search")["calls"] == 1
    assert _one("srvA", "fetch")["calls"] == 1


def test_stats_sorted_by_calls_desc():
    mt._record_mcp_call("s", "lo", True, 1.0)
    mt._record_mcp_call("s", "hi", True, 1.0)
    mt._record_mcp_call("s", "hi", True, 1.0)
    mt._record_mcp_call("s", "mid", True, 1.0)
    mt._record_mcp_call("s", "mid", True, 1.0)

    # hi=2, mid=2, lo=1 —— 同 calls 时按 (server, tool) 升序：hi < mid < lo
    tools_order = [s["tool"] for s in mt.get_mcp_call_stats()]
    assert tools_order == ["hi", "mid", "lo"], f"排序不符：{tools_order}"


def test_empty_stats_is_empty_list():
    assert mt.get_mcp_call_stats() == []


# --------------------------------------------------------------------------
# 容量上限
# --------------------------------------------------------------------------

def test_capacity_cap_drops_new_entries_but_keeps_counting():
    """超 _MAX_TRACKED_MCP_TOOLS 后静默丢弃**新**条目，已有条目继续累加。

    这是 fail-open 取舍：宁可丢新条目的统计，也不让字典无限吃内存。
    """
    cap = mt._MAX_TRACKED_MCP_TOOLS
    for i in range(cap):
        mt._record_mcp_call(f"s{i}", "t", True, 1.0)
    assert len(mt.get_mcp_call_stats()) == cap

    # 第 cap+1 个不同 key → 被丢弃
    mt._record_mcp_call("overflow", "t", True, 1.0)
    assert _one("overflow", "t") is None, "超限后新条目应被丢弃"
    assert len(mt.get_mcp_call_stats()) == cap

    # 已有条目继续累加
    mt._record_mcp_call("s0", "t", True, 1.0)
    assert _one("s0", "t")["calls"] == 2, "已有条目应继续统计"


# --------------------------------------------------------------------------
# _is_error_payload
# --------------------------------------------------------------------------

def test_is_error_payload_branches():
    assert mt._is_error_payload(json.dumps({"error": "x"})) == (True, "x")
    assert mt._is_error_payload(json.dumps({"result": "ok"})) == (False, "")
    assert mt._is_error_payload("not json") == (False, "")
    assert mt._is_error_payload(None) == (False, "")
    assert mt._is_error_payload(12345) == (False, "")


def test_is_error_payload_list_is_not_error():
    """刻意的行为差异（勿"修正"回旧写法）。

    旧内联写法 ``if "error" in parsed`` 对 list 退化成成员判定：
    ``"error" in ["error"]`` 为真 → 返回 JSON 数组且含字符串 "error" 的工具
    结果会被误判成失败，进而误触发断路器。加严为 isinstance(dict) 后消除。
    """
    assert mt._is_error_payload(json.dumps(["error"])) == (False, "")
    assert mt._is_error_payload(json.dumps(["a", "error", "b"])) == (False, "")


def test_error_text_truncated_to_500():
    mt._record_mcp_call("s", "t", False, 1.0, "E" * 5000)
    assert len(_one("s", "t")["last_error"]) == 500


# --------------------------------------------------------------------------
# 端到端：_make_tool_handler 埋点
# --------------------------------------------------------------------------

class _FakeServer:
    def __init__(self):
        self.session = object()
        self._rpc_lock = asyncio.Lock()


def _build_handler(monkeypatch, run_result):
    """构造 tool handler 并短路真正的 MCP 调用。

    ``_run_on_mcp_loop`` 被替换成同步返回，因此只测「埋点是否正确」，
    不测真实 MCP 传输（那属于既有 transport 测试的职责）。
    """
    monkeypatch.setattr(mt, "_servers", {"srv": _FakeServer()}, raising=True)
    monkeypatch.setattr(mt, "_server_error_counts", {}, raising=True)
    monkeypatch.setattr(mt, "_server_breaker_opened_at", {}, raising=True)

    def _fake_run(_coro_or_fn, timeout=None):
        # 注意：_call_once 传的是 **async 函数对象**（``_run_on_mcp_loop(_call, ...)``），
        # 不是已创建的 coroutine —— 不要对它调 close()，否则 AttributeError。
        if isinstance(run_result, Exception):
            raise run_result
        return run_result

    monkeypatch.setattr(mt, "_run_on_mcp_loop", _fake_run, raising=True)
    return mt._make_tool_handler("srv", "do_thing", 30.0)


def test_handler_records_success(monkeypatch):
    h = _build_handler(monkeypatch, json.dumps({"result": "ok"}))
    out = h({})
    assert json.loads(out)["result"] == "ok"

    s = _one()
    assert s is not None, "成功调用未被埋点记录"
    assert s["calls"] == 1 and s["errors"] == 0
    assert s["success_rate"] == 1.0
    assert s["total_ms"] >= 0


def test_handler_records_tool_level_error(monkeypatch):
    """工具本身返回 {"error": ...} —— 计入 errors，但仍算一次调用。"""
    h = _build_handler(monkeypatch, json.dumps({"error": "tool said no"}))
    h({})

    s = _one()
    assert s["calls"] == 1
    assert s["errors"] == 1, "工具返回 error 应计入 errors"
    assert s["last_error"] == "tool said no"
    assert s["success_rate"] == 0.0


def test_handler_records_exception(monkeypatch):
    h = _build_handler(monkeypatch, RuntimeError("kaboom"))
    out = h({})
    assert "error" in json.loads(out)

    s = _one()
    assert s["calls"] == 1
    assert s["errors"] == 1
    assert "RuntimeError" in s["last_error"]


def test_handler_non_json_result_counts_as_success(monkeypatch):
    """非 JSON 结果 = 成功（与断路器既有约定一致）。"""
    h = _build_handler(monkeypatch, "plain text result")
    h({})
    s = _one()
    assert s["calls"] == 1 and s["errors"] == 0


# --------------------------------------------------------------------------
# fail-open / 并发
# --------------------------------------------------------------------------

class _BrokenDict(dict):
    """``get()`` 抛异常的 dict —— 用来触发 _record_mcp_call 的 except 分支。

    刻意**不**用 ``None`` 替换全局字典：那样 fixture teardown 的
    ``.clear()`` 也会跟着炸，把一个 fail-open 用例变成 teardown error。
    """

    def get(self, *args, **kwargs):
        raise RuntimeError("simulated stats failure")


def test_record_never_raises_on_internal_error(monkeypatch):
    """统计内部出错必须静默 —— 监控不能拖垮它观察的调用链路。"""
    monkeypatch.setattr(mt, "_MCP_CALL_STATS", _BrokenDict(), raising=True)
    mt._record_mcp_call("s", "t", True, 1.0)  # 不应抛


def test_concurrent_records_are_consistent():
    """多线程并发记录不丢计数（独立锁的正确性）。"""
    def _burst():
        for _ in range(50):
            mt._record_mcp_call("s", "t", True, 1.0)

    threads = [threading.Thread(target=_burst) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _one("s", "t")["calls"] == 400, "并发计数丢失"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:xdist", "-o", "addopts="]))
