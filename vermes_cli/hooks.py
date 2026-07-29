"""vermes hooks — inspect and manage shell-script hooks.

Usage::

    vermes hooks list
    vermes hooks test <event> [--for-tool X] [--payload-file F]
    vermes hooks revoke <command>
    vermes hooks doctor

Consent records live under ``~/.vermes/shell-hooks-allowlist.json`` and
hook definitions come from the ``hooks:`` block in ``~/.vermes/config.yaml``
(the same config read by the CLI / gateway at startup).

This module is a thin CLI shell over :mod:`agent.shell_hooks`; every
shared concern (payload serialisation, response parsing, allowlist
format) lives there.
"""

from __future__ import annotations

import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

from typing import Any, Dict, List

def hooks_command(args) -> None:
    """Entry point for ``vermes hooks`` — dispatches to the requested action."""
    sub = getattr(args, "hooks_action", None)

    if not sub:
        logger.info("Usage: vermes hooks {list|test|revoke|doctor}")
        logger.info("Run 'vermes hooks --help' for details.")
        return

    if sub in {"list", "ls"}:
        _cmd_list(args)
    elif sub == "test":
        _cmd_test(args)
    elif sub in {"revoke", "remove", "rm"}:
        _cmd_revoke(args)
    elif sub == "doctor":
        _cmd_doctor(args)
    else:
        logger.info(f"Unknown hooks subcommand: {sub}")

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _cmd_list(_args) -> None:
    from vermes_cli.config import load_config
    from agent import shell_hooks

    specs = shell_hooks.iter_configured_hooks(load_config())

    if not specs:
        logger.info("No shell hooks configured in ~/.vermes/config.yaml.")
        logger.info("See `vermes hooks --help` or")
        logger.info("    website/docs/user-guide/features/hooks.md")
        logger.info("for the config schema and worked examples.")
        return

    by_event: Dict[str, List] = {}
    for spec in specs:
        by_event.setdefault(spec.event, []).append(spec)

    allowlist = shell_hooks.load_allowlist()
    approved = {
        (e.get("event"), e.get("command"))
        for e in allowlist.get("approvals", [])
        if isinstance(e, dict)
    }

    logger.info(f"Configured shell hooks ({len(specs)} total):\n")

    for event in sorted(by_event.keys()):
        logger.info(f"  [{event}]")
        for spec in by_event[event]:
            is_approved = (spec.event, spec.command) in approved
            status = "✓ allowed" if is_approved else "✗ not allowlisted"
            matcher_part = f" matcher={spec.matcher!r}" if spec.matcher else ""
            logger.info(
                f"    - {spec.command}{matcher_part} "
                f"(timeout={spec.timeout}s, {status})"
            )

            if is_approved:
                entry = shell_hooks.allowlist_entry_for(spec.event, spec.command)
                if entry and entry.get("approved_at"):
                    logger.info(f"      approved_at: {entry['approved_at']}")
                    mtime_now = shell_hooks.script_mtime_iso(spec.command)
                    mtime_at = entry.get("script_mtime_at_approval")
                    if mtime_now and mtime_at and mtime_now > mtime_at:
                        logger.info(
                            f"      ⚠ script modified since approval "
                            f"(was {mtime_at}, now {mtime_now}) — "
                            f"run `vermes hooks doctor` to re-validate"
                        )
        logger.info()

# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

# Synthetic kwargs matching the real invoke_hook() call sites — these are
# passed verbatim to agent.shell_hooks.run_once(), which routes them through
# the same _serialize_payload() that production firings use.  That way the
# stdin a script sees under `vermes hooks test` and `vermes hooks doctor`
# is identical in shape to what it will see at runtime.
_DEFAULT_PAYLOADS = {
    "pre_tool_call": {
        "tool_name": "terminal",
        "args": {"command": "echo hello"},
        "session_id": "test-session",
        "task_id": "test-task",
        "tool_call_id": "test-call",
    },
    "post_tool_call": {
        "tool_name": "terminal",
        "args": {"command": "echo hello"},
        "session_id": "test-session",
        "task_id": "test-task",
        "tool_call_id": "test-call",
        "result": '{"output": "hello"}',
        "duration_ms": 42,
    },
    "pre_llm_call": {
        "session_id": "test-session",
        "user_message": "What is the weather?",
        "conversation_history": [],
        "is_first_turn": True,
        "model": "gpt-4",
        "platform": "cli",
    },
    "post_llm_call": {
        "session_id": "test-session",
        "model": "gpt-4",
        "platform": "cli",
    },
    "on_session_start": {"session_id": "test-session"},
    "on_session_end": {"session_id": "test-session"},
    "on_session_finalize": {"session_id": "test-session"},
    "on_session_reset": {"session_id": "test-session"},
    "pre_api_request": {
        "session_id": "test-session",
        "task_id": "test-task",
        "platform": "cli",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
        "api_call_count": 1,
        "message_count": 4,
        "tool_count": 12,
        "approx_input_tokens": 2048,
        "request_char_count": 8192,
        "max_tokens": 4096,
    },
    "post_api_request": {
        "session_id": "test-session",
        "task_id": "test-task",
        "platform": "cli",
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_mode": "anthropic_messages",
        "api_call_count": 1,
        "api_duration": 1.234,
        "finish_reason": "stop",
        "message_count": 4,
        "response_model": "claude-sonnet-4-6",
        "usage": {"input_tokens": 2048, "output_tokens": 512},
        "assistant_content_chars": 1200,
        "assistant_tool_call_count": 0,
    },
    "subagent_stop": {
        "parent_session_id": "parent-sess",
        "child_role": None,
        "child_summary": "Synthetic summary for hooks test",
        "child_status": "completed",
        "duration_ms": 1234,
    },
}

def _cmd_test(args) -> None:
    from vermes_cli.config import load_config
    from vermes_cli.plugins import VALID_HOOKS
    from agent import shell_hooks

    event = args.event
    if event not in VALID_HOOKS:
        logger.info(f"Unknown event: {event!r}")
        logger.info(f"Valid events: {', '.join(sorted(VALID_HOOKS))}")
        return

    # Synthetic kwargs in the same shape invoke_hook() would pass.  Merged
    # with --for-tool (overrides tool_name) and --payload-file (extra kwargs).
    payload = dict(_DEFAULT_PAYLOADS.get(event, {"session_id": "test-session"}))

    if getattr(args, "for_tool", None):
        payload["tool_name"] = args.for_tool

    if getattr(args, "payload_file", None):
        try:
            custom = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
            if isinstance(custom, dict):
                payload.update(custom)
            else:
                logger.info(f"Warning: {args.payload_file} is not a JSON object; ignoring")
        except Exception as exc:
            logger.info(f"Error reading payload file: {exc}")
            return

    specs = shell_hooks.iter_configured_hooks(load_config())
    specs = [s for s in specs if s.event == event]

    if getattr(args, "for_tool", None):
        specs = [
            s for s in specs
            if s.event not in {"pre_tool_call", "post_tool_call"}
            or s.matches_tool(args.for_tool)
        ]

    if not specs:
        logger.info(f"No shell hooks configured for event: {event}")
        if getattr(args, "for_tool", None):
            logger.info(f"(with matcher filter --for-tool={args.for_tool})")
        return

    logger.info(f"Firing {len(specs)} hook(s) for event '{event}':\n")
    for spec in specs:
        logger.info(f"  → {spec.command}")
        result = shell_hooks.run_once(spec, payload)
        _print_run_result(result)
        logger.info()

def _print_run_result(result: Dict[str, Any]) -> None:
    if result.get("error"):
        logger.info(f"      ✗ error: {result['error']}")
        return
    if result.get("timed_out"):
        logger.info(f"      ✗ timed out after {result['elapsed_seconds']}s")
        return

    rc = result.get("returncode")
    elapsed = result.get("elapsed_seconds", 0)
    logger.info(f"      exit={rc}  elapsed={elapsed}s")

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if stdout:
        logger.info(f"      stdout: {_truncate(stdout, 400)}")
    if stderr:
        logger.warning(f"      stderr: {_truncate(stderr, 400)}")

    parsed = result.get("parsed")
    if parsed:
        logger.info(f"      parsed (Vermes wire shape): {json.dumps(parsed)}")
    else:
        logger.info("      parsed: <none — hook contributed nothing to the dispatcher>")

def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."

# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------

def _cmd_revoke(args) -> None:
    from agent import shell_hooks

    removed = shell_hooks.revoke(args.command)
    if removed == 0:
        logger.info(f"No allowlist entry found for command: {args.command}")
        return
    logger.info(f"Removed {removed} allowlist entry/entries for: {args.command}")
    logger.info(
        "Note: currently running CLI / gateway processes keep their "
        "already-registered callbacks until they restart."
    )

# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def _cmd_doctor(_args) -> None:
    from vermes_cli.config import load_config
    from agent import shell_hooks

    specs = shell_hooks.iter_configured_hooks(load_config())

    if not specs:
        logger.info("No shell hooks configured — nothing to check.")
        return

    logger.info(f"Checking {len(specs)} configured shell hook(s)...\n")

    problems = 0
    for spec in specs:
        logger.info(f"  [{spec.event}] {spec.command}")
        problems += _doctor_one(spec, shell_hooks)
        logger.info()

    if problems:
        logger.info(f"{problems} issue(s) found.  Fix before relying on these hooks.")
    else:
        logger.info("All shell hooks look healthy.")

def _doctor_one(spec, shell_hooks) -> int:
    problems = 0

    # 1. Script exists and is executable
    if shell_hooks.script_is_executable(spec.command):
        logger.info("      ✓ script exists and is executable")
    else:
        problems += 1
        logger.info("      ✗ script missing or not executable "
              "(chmod +x the file, or fix the path)")

    # 2. Allowlist status
    entry = shell_hooks.allowlist_entry_for(spec.event, spec.command)
    if entry:
        logger.info(f"      ✓ allowlisted (approved {entry.get('approved_at', '?')})")
    else:
        problems += 1
        logger.info("      ✗ not allowlisted — hook will NOT fire at runtime "
              "(run with --accept-hooks once, or confirm at the TTY prompt)")

    # 3. Mtime drift
    if entry and entry.get("script_mtime_at_approval"):
        mtime_now = shell_hooks.script_mtime_iso(spec.command)
        mtime_at = entry["script_mtime_at_approval"]
        if mtime_now and mtime_at and mtime_now > mtime_at:
            problems += 1
            logger.info(f"      ⚠ script modified since approval "
                  f"(was {mtime_at}, now {mtime_now}) — review changes, "
                  f"then `vermes hooks revoke` + re-approve to refresh")
        elif mtime_now and mtime_at and mtime_now == mtime_at:
            logger.info("      ✓ script unchanged since approval")

    # 4. Produces valid JSON for a synthetic payload — only when the entry
    # is already allowlisted.  Otherwise `vermes hooks doctor` would execute
    # every script listed in a freshly-pulled config before the user has
    # reviewed them, which directly contradicts the documented workflow
    # ("spot newly-added hooks *before they register*").
    if not entry:
        logger.info("      ℹ skipped JSON smoke test — not allowlisted yet. "
              "Approve the hook first (via TTY prompt or --accept-hooks), "
              "then re-run `vermes hooks doctor`.")
    elif shell_hooks.script_is_executable(spec.command):
        payload = _DEFAULT_PAYLOADS.get(spec.event, {"extra": {}})
        result = shell_hooks.run_once(spec, payload)
        if result.get("timed_out"):
            problems += 1
            logger.info(f"      ✗ timed out after {result['elapsed_seconds']}s "
                  f"on synthetic payload (timeout={spec.timeout}s)")
        elif result.get("error"):
            problems += 1
            logger.info(f"      ✗ execution error: {result['error']}")
        else:
            rc = result.get("returncode")
            elapsed = result.get("elapsed_seconds", 0)
            stdout = (result.get("stdout") or "").strip()
            if stdout:
                try:
                    json.loads(stdout)
                    logger.info(f"      ✓ produced valid JSON on synthetic payload "
                          f"(exit={rc}, {elapsed}s)")
                except json.JSONDecodeError:
                    problems += 1
                    logger.info(f"      ✗ stdout was not valid JSON (exit={rc}, "
                          f"{elapsed}s): {_truncate(stdout, 120)}")
            else:
                logger.info(f"      ✓ ran clean with empty stdout "
                      f"(exit={rc}, {elapsed}s) — hook is observer-only")

    return problems
