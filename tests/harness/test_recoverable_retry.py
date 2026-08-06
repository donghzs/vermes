"""P3 聚焦测试：invoke_with_retry（瞬态网络抖动重试）。

核心不变量：
- 仅 network_error（含折入的 TimeoutError）重试，其余确定性失败 0 重试立即冒泡。
- 内置确定性工具（_BUILTIN_NO_RETRY）短路，直接调用一次。
- 所有尝试耗尽 / 命中非瞬态异常 → raise 最后一次异常，外层 except 行为不变。
- retry 日志格式固定，供 P3.5 熔断 grep 重试率。

反向验证（R5 解药）：本文件拷到 P3 之前 commit（35dc16bcd，无 invoke_with_retry）
跑 → ImportError/AttributeError 收集失败，证测试验真功能非空过。
"""

import logging
from unittest.mock import MagicMock

import pytest

from harness.recoverable import (
    _BUILTIN_NO_RETRY,
    _RETRYABLE_ERROR_TYPES,
    classify_failure,
    invoke_with_retry,
)


def _mk(behaviour):
    """构造 invoke_fn + 调用计数。

    behaviour(n) 第 n 次调用返回 "ok" 或某个 Exception 实例（被 raise）。
    """
    state = {"n": 0}

    def fn():
        state["n"] += 1
        out = behaviour(state["n"])
        if isinstance(out, Exception):
            raise out
        return out

    return fn, state


# --- 正向：network_error 重试 1 次后成功 ----------------------------------
def test_positive_network_retry_then_success():
    fn, calls = _mk(lambda n: ConnectionError("connect failed") if n == 1 else "ok")
    result = invoke_with_retry(fn, "web_fetch", max_attempts=2, base_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 2


def test_timeout_folded_into_network_is_retryable():
    # TimeoutError 被 classify_failure 折入 network_error → 应重试
    fn, calls = _mk(lambda n: TimeoutError("timed out") if n == 1 else "ok")
    result = invoke_with_retry(fn, "slow_tool", max_attempts=2, base_delay=0.001)
    assert result == "ok"
    assert calls["n"] == 2


# --- 负向：非瞬态异常 0 重试立即冒泡 --------------------------------------
def test_negative_non_retryable_no_retry():
    fn, calls = _mk(lambda n: ValueError("bad input"))
    with pytest.raises(ValueError):
        invoke_with_retry(fn, "some_tool", max_attempts=2, base_delay=0.001)
    assert calls["n"] == 1  # 绝不重试


def test_negative_missing_dependency_no_retry():
    fn, calls = _mk(lambda n: ModuleNotFoundError("no mod"))
    with pytest.raises(ModuleNotFoundError):
        invoke_with_retry(fn, "some_tool", max_attempts=2, base_delay=0.001)
    assert calls["n"] == 1


# --- 全失败：network_error 每次都失败 → 重试 max_attempts 次后 raise ------
def test_all_attempts_fail_network():
    fn, calls = _mk(lambda n: ConnectionError("down"))
    with pytest.raises(ConnectionError):
        invoke_with_retry(fn, "web_fetch", max_attempts=2, base_delay=0.001)
    assert calls["n"] == 2


# --- R6：内置确定性工具短路，跳过重试 -------------------------------------
def test_builtin_short_circuit_no_retry_on_network():
    fn, calls = _mk(lambda n: ConnectionError("connect failed"))
    with pytest.raises(ConnectionError):
        invoke_with_retry(fn, "todo", max_attempts=2, base_delay=0.001)
    assert calls["n"] == 1  # 内置工具即使网络错也不重试


def test_builtin_short_circuit_success():
    fn, calls = _mk(lambda n: "ok")
    assert invoke_with_retry(fn, "memory", max_attempts=2, base_delay=0.001) == "ok"
    assert calls["n"] == 1


def test_builtin_no_retry_set_complete():
    # 五个内置名都在 _BUILTIN_NO_RETRY 中
    assert _BUILTIN_NO_RETRY == {
        "todo", "memory", "session_search", "clarify", "delegate_task"
    }


# --- retry 日志格式（P3.5 熔断可 grep） ----------------------------------
def test_retry_log_format():
    mock_log = MagicMock(spec=logging.Logger)
    fn, _ = _mk(lambda n: ConnectionError("connect failed") if n == 1 else "ok")
    invoke_with_retry(
        fn, "web_fetch", max_attempts=2, base_delay=0.001, log=mock_log
    )
    mock_log.warning.assert_called_once()
    args, kwargs = mock_log.warning.call_args
    # 格式：("tool %s retry %d/%d after %s", function_name, attempt+1, max_attempts-1, etype)
    assert args[0] == "tool %s retry %d/%d after %s"
    assert args[1] == "web_fetch"
    assert args[2] == 1  # attempt+1 (第 1 次重试)
    assert args[3] == 1  # max_attempts-1 (允许 1 次重试)
    assert args[4] == "network_error"


# --- 分类集合正确性（与审计对齐） ----------------------------------------
def test_retryable_set_is_only_network_error():
    assert _RETRYABLE_ERROR_TYPES == {"network_error"}
    # 非 network 分类分类后不应进重试集合
    for exc in [
        ModuleNotFoundError("x"), FileNotFoundError("x"), PermissionError("x"),
        KeyError("x"), AttributeError("x"), ValueError("x"),
        RuntimeError("x"), Exception("x"),
    ]:
        etype, _ = classify_failure(exc)
        assert etype not in _RETRYABLE_ERROR_TYPES
