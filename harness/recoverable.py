"""Harness: recoverable tool-failure feedback.

Wraps a tool entry point so that an *unexpected* exception becomes a
structured, machine-readable recovery hint instead of an opaque stack trace
or a 500. This is harness capability #2 from the harness-insights analysis:
"check failure state + recoverable feedback".

Design goals
------------
- Non-invasive: the normal return path is untouched. Only *unexpected*
  exceptions are caught and converted.
- Preserves return shape: caller chooses ``returns="json"`` (browser-style
  tools that emit ``json.dumps`` strings) or ``returns="dict"`` (framework
  tools that return dicts).
- Classifies the failure into a small, actionable vocabulary
  (missing_dependency / network_error / invalid_input / permission_denied /
  ...) so the agent can pick a recovery action instead of re-reading a trace.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import time
import traceback
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("harness.recoverable")


@dataclass
class RecoverableFeedback:
    """Structured recovery hint returned when a tool fails unexpectedly."""

    ok: bool = False
    error_type: str = "unexpected_error"
    what_failed: str = ""
    what_missing: str = ""
    suggested_next: str = ""
    detail: str = ""
    tool: str = ""

    def to_payload(self) -> dict:
        # ``ok`` is kept (always False on the recovery path) so consumers can
        # branch on it consistently with the framework's ``success`` convention.
        return {k: v for k, v in asdict(self).items()}

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False)


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """Map an exception to ``(error_type, human_cause)``.

    Ordering matters: most specific matches first.
    """
    etype = type(exc).__name__
    msg = str(exc)

    if isinstance(exc, (ModuleNotFoundError, ImportError)):
        return "missing_dependency", f"缺少模块/依赖：{msg or etype}"
    if isinstance(exc, FileNotFoundError):
        return "missing_file", f"缺少文件：{msg or etype}"
    if isinstance(exc, PermissionError):
        return "permission_denied", f"权限不足：{msg or etype}"
    if isinstance(exc, (ConnectionError, TimeoutError)) or (
        isinstance(exc, OSError) and "connect" in msg.lower()
    ):
        return "network_error", f"网络连接失败：{msg or etype}"
    if isinstance(exc, KeyError):
        return "missing_key", f"缺少键：{msg or etype}"
    if isinstance(exc, AttributeError):
        return "missing_attribute", f"缺少属性/方法：{msg or etype}"
    if isinstance(exc, ValueError):
        return "invalid_input", f"输入参数非法：{msg or etype}"
    if isinstance(exc, (RuntimeError,)):
        return "runtime_error", f"运行时错误：{msg or etype}"
    return "unexpected_error", msg or etype


# ---------------------------------------------------------------------------
# Retry (P3): transient network-jitter tolerance for tool invocation.
# ---------------------------------------------------------------------------
# 确定性内置工具：失败是逻辑/状态错误而非瞬态，retry 无意义。
_BUILTIN_NO_RETRY = frozenset(
    {"todo", "memory", "session_search", "clarify", "delegate_task"}
)

# 仅这些分类值得重试（瞬态网络抖动）。其余（缺依赖/权限/入参错/未知等）是
# 确定性失败，重试只会浪费时间并可能误导 agent，一律不重试。
_RETRYABLE_ERROR_TYPES = frozenset({"network_error"})


def invoke_with_retry(
    invoke_fn: Callable[[], Any],
    function_name: str,
    *,
    max_attempts: int = 2,
    base_delay: float = 0.5,
    classify: Optional[Callable[[BaseException], tuple[str, str]]] = None,
    log: Optional[logging.Logger] = None,
) -> Any:
    """调用 ``invoke_fn()``，对瞬态 ``network_error`` 做指数退避重试。

    设计边界（P3 v1）：
    - 只包"调用抛异常"这一层；工具成功返回但内容错（verify_fail，P0-A 范畴）
      不在此范围——重试验证失败会得到同样的坏结果，毫无意义。
    - 用 *否定过滤* 决定重试：仅 ``network_error``（含折入的 ``TimeoutError``）
      重试，其余分类立即冒泡。
    - 确定性内置工具（``_BUILTIN_NO_RETRY``）直接调用一次，跳过重试逻辑。
    - 所有尝试耗尽或命中非瞬态异常时，raise 最后一次异常，由外层 except 走
      原有 ``classify → record → 错误串`` 流程（控制流完全不变）。
    - 熔断（基于 ``failure_learning.should_warn`` + 重试率）是 P3.5 的活，
      这里只发可被 grep 的 retry 日志，不改动执行决策。

    Args:
        invoke_fn: 无参可调用，执行真实工具调用。
        function_name: 工具名，用于内置短路与日志。
        max_attempts: 最大尝试次数（默认 2 = 1 次重试）。
        base_delay: 退避基数（秒），第 k 次重试 sleep ``base_delay * 2**k``。
        classify: 异常分类器，默认 ``classify_failure``。
        log: 日志器，默认 ``harness.recoverable``。
    """
    if function_name in _BUILTIN_NO_RETRY:
        return invoke_fn()
    _log = log or logger
    _classify = classify or classify_failure
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            return invoke_fn()
        except Exception as exc:  # noqa: BLE001 — 重试需捕获所有调用异常
            last_exc = exc
            if _classify is None:
                break
            etype, _ = _classify(exc)
            if etype not in _RETRYABLE_ERROR_TYPES:
                break  # 确定性失败，重试无意义
            if attempt < max_attempts - 1:
                _log.warning(
                    "tool %s retry %d/%d after %s",
                    function_name, attempt + 1, max_attempts - 1, etype,
                )
                time.sleep(base_delay * (2 ** attempt))
    if last_exc is None:  # 理论不可达（max_attempts>=1），仅防御
        raise RuntimeError("invoke_with_retry: no attempt executed")
    raise last_exc


def recoverable_tool(
    *,
    tool_name: str,
    missing_hint: str = "",
    returns: str = "dict",
    log: Optional[logging.Logger] = None,
) -> Callable[[Callable], Callable]:
    """Decorator: convert unexpected tool failures into recoverable feedback.

    Args:
        tool_name: tool identifier, surfaced in the feedback payload.
        missing_hint: extra "what is missing / what to do" text, appended to
            ``suggested_next``. Lets the tool author give domain-specific
            guidance (e.g. "配置 VISION_PROVIDER 或设置 xiaomi API key").
        returns: ``"json"`` forces a JSON *string* on failure (for browser-style
            tools); ``"dict"`` returns a plain dict (framework tools).
        log: optional logger; defaults to ``harness.recoverable``.

    Works for both sync and async tool functions.
    """
    _log = log or logger

    def decorator(fn: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(fn)

        def _build(exc: BaseException) -> RecoverableFeedback:
            error_type, what_failed = classify_failure(exc)
            return RecoverableFeedback(
                ok=False,
                error_type=error_type,
                what_failed=what_failed,
                what_missing=missing_hint,
                suggested_next=(
                    missing_hint
                    or "查看日志后重试；若持续失败请换用其它工具或检查配置。"
                ),
                detail=traceback.format_exc(limit=8),
                tool=tool_name,
            )

        if is_async:

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 — recoverable by design
                    fb = _build(exc)
                    _log.warning("tool[%s] failed: %s", tool_name, fb.what_failed)
                    return fb.to_json() if returns == "json" else fb.to_payload()

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — recoverable by design
                fb = _build(exc)
                _log.warning("tool[%s] failed: %s", tool_name, fb.what_failed)
                return fb.to_json() if returns == "json" else fb.to_payload()

        return sync_wrapper

    return decorator
