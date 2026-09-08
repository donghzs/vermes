"""Generic ACP client transport + Copilot-specific subclass.

Vermes uses the Agent Client Protocol (ACP, by Zed Industries) to pull
external ACP-compatible agents (Codex, Claude Code, Gemini CLI, OpenClaw,
… — see the ACP Registry) in as collaborative partners. This module defines
``AcpAgentTransportBase``: the generic transport logic (spawn the agent CLI,
drive it over stdio JSON-RPC with the initialize → session/new →
session/prompt handshake, collect ``session/update`` chunks, bridge fs
permission requests). ``CopilotACPClient`` is a thin subclass carrying the
Copilot-specific defaults and deprecation guard.

泛化说明（请神收尾 T0）：
- ``AcpAgentTransportBase`` 与具体 agent 无关；调用方通过 ``acp_command`` /
  ``acp_args`` 指定要 spawn 的 agent CLI（如
  ``npx @agentclientprotocol/codex-acp@1.8.0``）。
- ``CopilotACPClient`` 保留 Copilot 专用默认值（命令 = copilot、参数 =
  --acp --stdio、弃用检测），既有的生产调用与测试完全兼容。
- 新接入的 agent（Codex / Claude Code / …）走同一基类，由 recipe 的
  entry_point 决定 spawn 谁，详见 T1/T2/T3。
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

import json
import os
import queue
import re
import signal
import shlex
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.file_safety import get_read_block_error, is_write_denied
from agent.redact import redact_sensitive_text

# 基类默认 marker；Copilot 子类用 "acp://copilot"（见 CopilotACPClient）。
ACP_MARKER_BASE_URL = "acp://copilot"
_DEFAULT_TIMEOUT_SECONDS = 900.0

_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
_TOOL_CALL_JSON_RE = re.compile(r"\{\s*\"id\"\s*:\s*\"[^\"]+\"\s*,\s*\"type\"\s*:\s*\"function\"\s*,\s*\"function\"\s*:\s*\{.*?\}\s*\}", re.DOTALL)

# Stderr fingerprint of the deprecated `gh copilot` CLI extension
# (https://github.blog/changelog/2025-09-25-upcoming-deprecation-of-gh-copilot-cli-extension).
# We require BOTH the literal product name ("gh-copilot") AND a deprecation
# marker, so generic stderr from the NEW `@github/copilot` CLI — whose repo
# is github.com/github/copilot-cli and which legitimately mentions "copilot-cli"
# in its own banners and error messages — doesn't get misclassified as the
# deprecated extension.
_DEPRECATION_REQUIRED = ("gh-copilot",)
_DEPRECATION_MARKERS = (
    "has been deprecated",
    "no commands will be executed",
)


def _is_gh_copilot_deprecation_message(stderr_text: str) -> bool:
    """True iff stderr looks like the deprecated gh-copilot extension's banner."""

    lower = stderr_text.lower()
    if not any(req in lower for req in _DEPRECATION_REQUIRED):
        return False
    return any(marker in lower for marker in _DEPRECATION_MARKERS)


def _resolve_command() -> str:
    """Default ACP command. Falls back to ``copilot`` for backward compat
    (existing Copilot path) and honours explicit env overrides."""
    return (
        os.getenv("VERMES_COPILOT_ACP_COMMAND", "").strip()
        or os.getenv("COPILOT_CLI_PATH", "").strip()
        or "copilot"
    )


def _resolve_args() -> list[str]:
    raw = os.getenv("VERMES_COPILOT_ACP_ARGS", "").strip()
    if not raw:
        return ["--acp", "--stdio"]
    return shlex.split(raw)


def _resolve_home_dir() -> str:
    """Return a stable HOME for child ACP processes."""

    try:
        from vermes_constants import get_subprocess_home

        profile_home = get_subprocess_home()
        if profile_home:
            return profile_home
    except Exception as e:
        logger.debug("copilot_acp_client.py:  resolve home dir failed: %s", e)

    home = os.environ.get("HOME", "").strip()
    if home:
        return home

    expanded = os.path.expanduser("~")
    if expanded and expanded != "~":
        return expanded

    try:
        import pwd

        resolved = pwd.getpwuid(os.getuid()).pw_dir.strip()  # windows-footgun: ok — POSIX fallback inside try/except (pwd import fails on Windows)
        if resolved:
            return resolved
    except Exception as e:
        logger.debug("copilot_acp_client.py:  resolve home dir failed: %s", e)

    # Last resort: /tmp (writable on any POSIX system). Avoids crashing the
    # subprocess with no HOME; callers can set VERMES_HOME explicitly if they
    # need a different writable dir.
    return "/tmp"


def _build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    home = _resolve_home_dir()
    env["HOME"] = home
    # Always expose the real user home so child scripts can find
    # ~/.vermes/ even when HOME is overridden for profile isolation.
    from vermes_constants import get_real_home
    real = get_real_home()
    if real and real != home:
        env["VERMES_REAL_HOME"] = real
    return env


def _jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _permission_denied(message_id: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": {
            "outcome": {
                "outcome": "cancelled",
            }
        },
    }


def _format_messages_as_prompt(
    messages: list[dict[str, Any]],
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    sections: list[str] = [
        "You are being used as the active ACP agent backend for Vermes.",
        "Use ACP capabilities to complete tasks.",
        "IMPORTANT: If you take an action with a tool, you MUST output tool calls using <tool_call>{...}</tool_call> blocks with JSON exactly in OpenAI function-call shape.",
        "If no tool is needed, answer normally.",
    ]
    if model:
        sections.append(f"Vermes requested model hint: {model}")

    if isinstance(tools, list) and tools:
        tool_specs: list[dict[str, Any]] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            fn = t.get("function") or {}
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            tool_specs.append(
                {
                    "name": name.strip(),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                }
            )
        if tool_specs:
            sections.append(
                "Available tools (OpenAI function schema). "
                "When using a tool, emit ONLY <tool_call>{...}</tool_call> with one JSON object "
                "containing id/type/function{name,arguments}. arguments must be a JSON string.\n"
                + json.dumps(tool_specs, ensure_ascii=False)
            )

    if tool_choice is not None:
        sections.append(f"Tool choice hint: {json.dumps(tool_choice, ensure_ascii=False)}")

    transcript: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "unknown").strip().lower()
        if role == "tool":
            role = "tool"
        elif role not in {"system", "user", "assistant"}:
            role = "context"

        content = message.get("content")
        rendered = _render_message_content(content)
        if not rendered:
            continue

        label = {
            "system": "System",
            "user": "User",
            "assistant": "Assistant",
            "tool": "Tool",
            "context": "Context",
        }.get(role, role.title())
        transcript.append(f"{label}:\n{rendered}")

    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))

    sections.append("Continue the conversation from the latest user request.")
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "").strip()
        if "content" in content and isinstance(content.get("content"), str):
            return str(content.get("content") or "").strip()
        return json.dumps(content, ensure_ascii=True)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts).strip()
    return str(content).strip()


def _extract_tool_calls_from_text(text: str) -> tuple[list[SimpleNamespace], str]:
    if not isinstance(text, str) or not text.strip():
        return [], ""

    extracted: list[SimpleNamespace] = []
    consumed_spans: list[tuple[int, int]] = []

    def _try_add_tool_call(raw_json: str) -> None:
        try:
            obj = json.loads(raw_json)
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        fn = obj.get("function")
        if not isinstance(fn, dict):
            return
        fn_name = fn.get("name")
        if not isinstance(fn_name, str) or not fn_name.strip():
            return
        fn_args = fn.get("arguments", "{}")
        if not isinstance(fn_args, str):
            fn_args = json.dumps(fn_args, ensure_ascii=False)
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"acp_call_{len(extracted)+1}"

        extracted.append(
            SimpleNamespace(
                id=call_id,
                call_id=call_id,
                response_item_id=None,
                type="function",
                function=SimpleNamespace(name=fn_name.strip(), arguments=fn_args),
            )
        )

    for m in _TOOL_CALL_BLOCK_RE.finditer(text):
        raw = m.group(1)
        _try_add_tool_call(raw)
        consumed_spans.append((m.start(), m.end()))

    # Only try bare-JSON fallback when no XML blocks were found.
    if not extracted:
        for m in _TOOL_CALL_JSON_RE.finditer(text):
            raw = m.group(0)
            _try_add_tool_call(raw)
            consumed_spans.append((m.start(), m.end()))

    if not consumed_spans:
        return extracted, text.strip()

    consumed_spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in consumed_spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)
    if cursor < len(text):
        parts.append(text[cursor:])

    cleaned = "\n".join(p.strip() for p in parts if p and p.strip()).strip()
    return extracted, cleaned


def _ensure_path_within_cwd(path_text: str, cwd: str) -> Path:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        raise PermissionError("ACP file-system paths must be absolute.")
    resolved = candidate.resolve()
    root = Path(cwd).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Path '{resolved}' is outside the session cwd '{root}'.") from exc
    return resolved


class _ACPChatCompletions:
    def __init__(self, client: "AcpAgentTransportBase"):
        self._client = client

    def create(self, **kwargs: Any) -> Any:
        return self._client._create_chat_completion(**kwargs)


class _ACPChatNamespace:
    def __init__(self, client: "AcpAgentTransportBase"):
        self.completions = _ACPChatCompletions(client)


class AcpAgentTransportBase:
    """Generic ACP client transport.

    Spawns an external ACP-compatible agent over stdio JSON-RPC and drives it
    with the initialize → session/new → session/prompt handshake. Subclasses
    (or direct callers) supply the agent CLI via ``acp_command`` / ``acp_args``.

    The protocol logic here is agent-agnostic. Anything Copilot-specific lives
    in :class:`CopilotACPClient`.
    """

    # Subclass-overridable labels/defaults so error messages name the right agent.
    agent_label = "ACP"
    acp_marker_base_url = "acp://"
    default_api_key = "acp"
    default_model = "acp"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        default_headers: dict[str, str] | None = None,
        acp_command: str | None = None,
        acp_args: list[str] | None = None,
        acp_cwd: str | None = None,
        command: str | None = None,
        args: list[str] | None = None,
        auth_env: str | None = None,
        auth_value: str | None = None,
        **_: Any,
    ):
        self.api_key = api_key or self.default_api_key
        self.base_url = base_url or self.acp_marker_base_url
        self._default_headers = dict(default_headers or {})
        # 默认回退到 copilot 命令（向后兼容既有 Copilot 路径）；recipe 驱动时
        # 由调用方通过 acp_command / acp_args 指定任意 ACP 兼容 agent。
        self._acp_command = acp_command or command or _resolve_command()
        self._acp_args = list(acp_args or args or _resolve_args())
        self._acp_cwd = str(Path(acp_cwd or os.getcwd()).resolve())
        # 授权 Key 持久化 · 读侧：recipe 要求的 env_var 及其持久化值（由
        # build_acp_transport 从进程环境 / 凭据库解析后传入）。
        self._auth_env = auth_env
        self._auth_value = auth_value
        self.chat = _ACPChatNamespace(self)
        self.is_closed = False
        self._active_process: subprocess.Popen[str] | None = None
        self._active_process_lock = threading.Lock()

    def _apply_auth_env(self, env: dict[str, str]) -> dict[str, str]:
        """Inject a persisted auth key into the subprocess env if missing.

        授权 Key 持久化 · 读侧最后一步：recipe 要求的 ``env_var`` 若不在当前
        进程环境，则使用 ``build_acp_transport`` 从凭据库解析出的持久化值。
        仅当缺失时才注入，避免覆盖已显式设置的环境变量。
        """
        if self._auth_env and self._auth_value and self._auth_env not in env:
            env = dict(env)
            env[self._auth_env] = self._auth_value
        # 通用 wrapper 运行时补齐：厂商 Electron 壳包出来的 CLI（各家 openclaw
        # 系/桌面 agent 常见）依赖宿主主进程注入的 env，第三方 spawn 会秒退。
        # 这里按「模式」解析补齐，不绑定任何厂商。run 与 handshake 两个 Popen
        # 点共用本方法，故补一处即全覆盖。
        try:
            from vermes_cli.adapters.cli_env import resolve_cli_env

            env = resolve_cli_env(self._acp_command, env)
        except Exception:  # noqa: BLE001 — 环境补齐失败不影响原有行为
            pass
        return env

    def _check_deprecation(self, stderr_text: str) -> "RuntimeError | None":
        """Hook for subclass-specific deprecation guards.

        Base implementation performs no check. CopilotACPClient overrides this
        to detect the deprecated ``gh copilot`` extension.
        """
        return None

    def close(self) -> None:
        proc: subprocess.Popen[str] | None
        with self._active_process_lock:
            proc = self._active_process
            self._active_process = None
        self.is_closed = True
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception as e:
                logger.debug("copilot_acp_client.py: close failed: %s", e)

    def _create_chat_completion(
        self,
        *,
        model: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        timeout: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        **_: Any,
    ) -> Any:
        prompt_text = _format_messages_as_prompt(
            messages or [],
            model=model,
            tools=tools,
            tool_choice=tool_choice,
        )
        # Normalise timeout: run_agent.py may pass an httpx.Timeout object
        # (used natively by the OpenAI SDK) rather than a plain float.
        if timeout is None:
            _effective_timeout = _DEFAULT_TIMEOUT_SECONDS
        elif isinstance(timeout, (int, float)):
            _effective_timeout = float(timeout)
        else:
            # httpx.Timeout or similar — pick the largest component so the
            # subprocess has enough wall-clock time for the full response.
            _candidates = [
                getattr(timeout, attr, None)
                for attr in ("read", "write", "connect", "pool", "timeout")
            ]
            _numeric = [float(v) for v in _candidates if isinstance(v, (int, float))]
            _effective_timeout = max(_numeric) if _numeric else _DEFAULT_TIMEOUT_SECONDS

        response_text, reasoning_text = self._run_prompt(
            prompt_text,
            timeout_seconds=_effective_timeout,
        )

        tool_calls, cleaned_text = _extract_tool_calls_from_text(response_text)

        usage = SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        assistant_message = SimpleNamespace(
            content=cleaned_text,
            tool_calls=tool_calls,
            reasoning=reasoning_text or None,
            reasoning_content=reasoning_text or None,
            reasoning_details=None,
        )
        finish_reason = "tool_calls" if tool_calls else "stop"
        choice = SimpleNamespace(message=assistant_message, finish_reason=finish_reason)
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=model or self.default_model,
        )

    def _handshake(self, *, timeout_seconds: float = 30.0) -> tuple[bool, str]:
        """真实 ACP initialize 握手：spawn → initialize → 清理。

        与 ``_run_prompt`` 的区别：只做**协议版本协商**（initialize），
        不建 session、不发 prompt——无副作用、不烧 token、快。

        健康检查语义：任何失败都返回 ``(False, 原因)`` 而**不抛异常**，
        由调用方决定是否放行注册。

        进程清理是硬要求：握手结束后必须 kill 子进程并关闭 stdio，
        否则每次注册都会留下一个孤儿 ACP 进程。
        """
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                [self._acp_command] + self._acp_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self._acp_cwd,
                env=self._apply_auth_env(_build_subprocess_env()),
                # 独立进程组：清理时能 killpg 整组（含 bridge 起的 node 孙进程）。
                # 否则孙进程会一直持有 stdout 写端 → 读线程等不到 EOF 并死握
                # TextIOWrapper 锁 → 主线程 close() 等同一把锁 → 互锁挂起（实测 45s+）。
                start_new_session=True,
            )
        except FileNotFoundError:
            return False, f"command not found: {self._acp_command!r}"
        except OSError as exc:
            return False, f"spawn failed: {exc}"

        inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=20)

        def _stdout_reader() -> None:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                try:
                    inbox.put(json.loads(line))
                except Exception:
                    inbox.put({"raw": line.rstrip("\n")})

        def _stderr_reader() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                stderr_tail.append(line.rstrip("\n"))

        out_thread = threading.Thread(target=_stdout_reader, daemon=True)
        err_thread = threading.Thread(target=_stderr_reader, daemon=True)
        out_thread.start()
        err_thread.start()

        detail = ""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": True, "writeTextFile": True}
                    },
                    "clientInfo": {"name": "vermes", "version": "1.0.0"},
                },
            }
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    msg = inbox.get(timeout=0.1)
                except queue.Empty:
                    continue
                # 握手阶段只需匹配 id==1 的响应；session/update 通知一律忽略
                if msg.get("id") != 1:
                    continue
                if "error" in msg:
                    err = msg.get("error") or {}
                    return False, f"initialize rejected: {err.get('message') or err}"
                result = msg.get("result") or {}
                detail = f"handshake ok: protocolVersion={result.get('protocolVersion')}"
                return True, detail

            # 超时 / 进程提前退出
            stderr_text = "\n".join(stderr_tail).strip()
            if proc.poll() is not None:
                return False, f"process exited early (rc={proc.returncode})" + (
                    f": {stderr_text}" if stderr_text else ""
                )
            return False, f"initialize timed out after {timeout_seconds}s"
        except (BrokenPipeError, OSError) as exc:
            return False, f"handshake io error: {exc}"
        except Exception as exc:  # 兜底：健康检查不该抛
            return False, f"handshake failed: {exc}"
        finally:
            # 硬清理：kill 整个进程组 + 关 stdio。握手进程无复用价值，留着就是孤儿。
            self._cleanup_handshake_process(proc, threads=(out_thread, err_thread))

    def _cleanup_handshake_process(
        self,
        proc: "subprocess.Popen[str] | None",
        threads: "tuple[threading.Thread, ...]" = (),
    ) -> None:
        """终止握手子进程（含其进程组）并关闭管道（幂等，任何异常都吞掉）。

        死锁防线（2026-09-08 实测，勿退回旧写法）：
        若只 ``proc.kill()``，bridge 起的 node **孙进程会继续持有 stdout 写端** →
        ``for line in proc.stdout`` 的读线程永远等不到 EOF，并死握 TextIOWrapper 锁；
        主线程随后 ``stream.close()`` 要抢同一把锁 → **双向互锁，挂起 45s+**
        （faulthandler 抓到：主线程卡 close、读线程卡 readline）。
        故必须三层齐下：
          ① spawn 时 ``start_new_session=True`` 建立独立进程组；
          ② 清理时 ``killpg`` 杀整组，让孙进程一起死、写端关闭、读线程自然 EOF；
          ③ ``join`` 读线程（1s）确认释放锁后再 close；**join 失败就放弃 close**
             —— fd 泄漏远轻于死锁。

        安全护栏（P0）：``os.getpgid`` 若返回**本进程自己的组**（未独立成组或
        取 pid 失败），绝不能 killpg —— 那会连同 Vermes 自身一起杀掉。
        """
        if proc is None:
            return
        try:
            if proc.poll() is None:
                killed_group = False
                try:
                    pgid = os.getpgid(proc.pid)
                    # 护栏：只杀「不是自己」的进程组
                    if pgid != os.getpgrp():
                        os.killpg(pgid, signal.SIGKILL)
                        killed_group = True
                except Exception:
                    # 取 pgid 失败 / 进程已退出 / 无 pid 属性 → 一律退化为 kill 父进程。
                    # 注意：必须是 Exception 而非具体异常类型——否则漏捕（如
                    # AttributeError）会让异常逃到外层 except 被吞，
                    # 连「退化 kill 父进程」都不会执行，清理彻底失效。
                    killed_group = False
                if not killed_group:
                    proc.kill()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        except Exception:
            pass

        # 等读线程退出（拿到 EOF 释放锁）后再 close；超时则放弃 close 以避险
        stuck: "list[threading.Thread]" = []
        for th in threads:
            try:
                th.join(timeout=1.0)
                if th.is_alive():
                    stuck.append(th)
            except Exception:
                pass
        if stuck:
            logger.debug(
                "copilot_acp_client.py: handshake 读线程未退出(%d)，跳过 stdio close 以避免死锁",
                len(stuck),
            )
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def _run_prompt(self, prompt_text: str, *, timeout_seconds: float) -> tuple[str, str]:
        try:
            proc = subprocess.Popen(
                [self._acp_command] + self._acp_args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self._acp_cwd,
                env=self._apply_auth_env(_build_subprocess_env()),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Could not start {self.agent_label} command '{self._acp_command}'. "
                "Ensure the ACP-compatible CLI is installed and on PATH, or set the "
                "explicit command via the agent recipe / environment override."
            ) from exc

        if proc.stdin is None or proc.stdout is None:
            proc.kill()
            raise RuntimeError(f"{self.agent_label} process did not expose stdin/stdout pipes.")

        self.is_closed = False
        with self._active_process_lock:
            self._active_process = proc

        inbox: queue.Queue[dict[str, Any]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=40)

        def _stdout_reader() -> None:
            if proc.stdout is None:
                return
            for line in proc.stdout:
                try:
                    inbox.put(json.loads(line))
                except Exception:
                    inbox.put({"raw": line.rstrip("\n")})

        def _stderr_reader() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                stderr_tail.append(line.rstrip("\n"))

        out_thread = threading.Thread(target=_stdout_reader, daemon=True)
        err_thread = threading.Thread(target=_stderr_reader, daemon=True)
        out_thread.start()
        err_thread.start()

        next_id = 0

        def _request(method: str, params: dict[str, Any], *, text_parts: list[str] | None = None, reasoning_parts: list[str] | None = None) -> Any:
            nonlocal next_id
            next_id += 1
            request_id = next_id
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    msg = inbox.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self._handle_server_message(
                    msg,
                    process=proc,
                    cwd=self._acp_cwd,
                    text_parts=text_parts,
                    reasoning_parts=reasoning_parts,
                ):
                    continue

                if msg.get("id") != request_id:
                    continue
                if "error" in msg:
                    err = msg.get("error") or {}
                    raise RuntimeError(
                        f"{self.agent_label} {method} failed: {err.get('message') or err}"
                    )
                return msg.get("result")

            stderr_text = "\n".join(stderr_tail).strip()
            if proc.poll() is not None and stderr_text:
                dep = self._check_deprecation(stderr_text)
                if dep is not None:
                    raise dep
                raise RuntimeError(f"{self.agent_label} process exited early: {stderr_text}")
            raise TimeoutError(f"Timed out waiting for {self.agent_label} response to {method}.")

        try:
            _request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {
                            "readTextFile": True,
                            "writeTextFile": True,
                        }
                    },
                    "clientInfo": {
                        "name": "Vermes-agent",
                        "title": "Vermes Agent",
                        "version": "0.0.0",
                    },
                },
            )
            session = _request(
                "session/new",
                {
                    "cwd": self._acp_cwd,
                    "mcpServers": [],
                },
            ) or {}
            session_id = str(session.get("sessionId") or "").strip()
            if not session_id:
                raise RuntimeError(f"{self.agent_label} did not return a sessionId.")

            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            _request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": prompt_text,
                        }
                    ],
                },
                text_parts=text_parts,
                reasoning_parts=reasoning_parts,
            )
            return "".join(text_parts), "".join(reasoning_parts)
        finally:
            self.close()

    def _handle_server_message(
        self,
        msg: dict[str, Any],
        *,
        process: subprocess.Popen[str],
        cwd: str,
        text_parts: list[str] | None,
        reasoning_parts: list[str] | None,
    ) -> bool:
        method = msg.get("method")
        if not isinstance(method, str):
            return False

        if method == "session/update":
            params = msg.get("params") or {}
            update = params.get("update") or {}
            kind = str(update.get("sessionUpdate") or "").strip()
            content = update.get("content") or {}
            chunk_text = ""
            if isinstance(content, dict):
                chunk_text = str(content.get("text") or "")
            if kind == "agent_message_chunk" and chunk_text and text_parts is not None:
                text_parts.append(chunk_text)
            elif kind == "agent_thought_chunk" and chunk_text and reasoning_parts is not None:
                reasoning_parts.append(chunk_text)
            return True

        if process.stdin is None:
            return True

        message_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "session/request_permission":
            response = _permission_denied(message_id)
        elif method == "fs/read_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                block_error = get_read_block_error(str(path))
                if block_error:
                    raise PermissionError(block_error)
                try:
                    content = path.read_text()
                except FileNotFoundError:
                    content = ""
                line = params.get("line")
                limit = params.get("limit")
                if isinstance(line, int) and line > 1:
                    lines = content.splitlines(keepends=True)
                    start = line - 1
                    end = start + limit if isinstance(limit, int) and limit > 0 else None
                    content = "".join(lines[start:end])
                if content:
                    content = redact_sensitive_text(content, force=True)
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "content": content,
                    },
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        elif method == "fs/write_text_file":
            try:
                path = _ensure_path_within_cwd(str(params.get("path") or ""), cwd)
                if is_write_denied(str(path)):
                    raise PermissionError(
                        f"Write denied: '{path}' is a protected system/credential file."
                    )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(params.get("content") or ""))
                response = {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": None,
                }
            except Exception as exc:
                response = _jsonrpc_error(message_id, -32602, str(exc))
        else:
            response = _jsonrpc_error(
                message_id,
                -32601,
                f"ACP client method '{method}' is not supported by Vermes yet.",
            )

        process.stdin.write(json.dumps(response) + "\n")
        process.stdin.flush()
        return True


class CopilotACPClient(AcpAgentTransportBase):
    """Copilot-specific ACP client (Copilot defaults + deprecation guard).

    Backward-compatible facade: default command is ``copilot`` with
    ``--acp --stdio``, and the deprecated ``gh copilot`` extension is detected.
    """

    agent_label = "Copilot ACP"
    acp_marker_base_url = "acp://copilot"
    default_api_key = "copilot-acp"
    default_model = "copilot-acp"

    def _check_deprecation(self, stderr_text: str) -> "RuntimeError | None":
        if _is_gh_copilot_deprecation_message(stderr_text):
            return RuntimeError(
                "Vermes ACP mode requires the NEW GitHub Copilot CLI "
                "(github.com/github/copilot-cli), but the binary it just "
                "spawned is the deprecated `gh copilot` extension.\n\n"
                "Install the new CLI:\n"
                "  npm install -g @github/copilot\n"
                "  # then verify with: copilot --help\n\n"
                "If `copilot` already resolves to the new CLI but you still see this,\n"
                "point Vermes at it explicitly:\n"
                "  export VERMES_COPILOT_ACP_COMMAND=/path/to/new/copilot\n\n"
                "Alternative: use the `copilot` provider (no ACP, hits the Copilot API\n"
                "directly with a Copilot subscription token) via `vermes setup`.\n\n"
                f"Original error:\n{stderr_text}"
            )
        return None
