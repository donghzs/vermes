"""MessageHandlerMixin — inbound message handling for GatewayRunner.

Extracted (P3) from gateway/run.py as part of Phase 3 mixin decomposition.
Contains the 5 methods that form the message-processing pipeline:

  - _deliver_platform_notice     (29 lines)
  - _handle_message               (1,130 lines)
  - _prepare_inbound_message_text (235 lines)
  - _consume_pending_native_image_paths (4 lines)
  - _handle_message_with_agent    (1,079 lines)

Module-level names from gateway.run are resolved lazily via _get_run_attr()
to support test monkeypatching — same pattern as session_mixin / watcher_mixin.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, List, Dict

from agent.i18n import t
from gateway._run_attr import _get_run_attr
from gateway.config import Platform, GatewayConfig, PlatformConfig
from gateway.gateway_utils import _home_target_env_var, _platform_config_key
from gateway.platforms.base import (
    EphemeralReply,
    MessageEvent,
    MessageType,
    _reply_anchor_for_event,
    merge_pending_message_event,
)
from gateway.session import (
    SessionStore,
    SessionSource,
    SessionContext,
    build_session_context,
    build_session_context_prompt,
    build_session_key,
    is_shared_multi_user_session,
)

logger = logging.getLogger(__name__)


class MessageHandlerMixin:
    """Inbound message handling pipeline, extracted from GatewayRunner.

    These methods were moved verbatim from gateway/run.py (P3 decomposition).
    All ``self.`` references work unchanged because GatewayRunner inherits
    this mixin. Module-level names from gateway.run are resolved via
    ``_get_run_attr()`` at call time to honor test monkeypatching.
"""

    async def _deliver_platform_notice(self, source, content: str) -> None:
        """Deliver a setup/operational notice using platform-specific privacy rules."""
        adapter = self.adapters.get(source.platform)
        if not adapter:
            return

        config = getattr(self, "config", None)
        notice_delivery = "public"
        if config and hasattr(config, "get_notice_delivery"):
            notice_delivery = config.get_notice_delivery(source.platform)

        metadata = self._thread_metadata_for_source(source)
        if notice_delivery == "private" and getattr(source, "user_id", None):
            try:
                result = await adapter.send_private_notice(
                    source.chat_id,
                    source.user_id,
                    content,
                    metadata=metadata,
                )
                if getattr(result, "success", False):
                    return
            except Exception:
                logger.debug(
                    "[%s] send_private_notice failed, falling back to public",
                    getattr(source, "platform", "?"),
                    exc_info=True,
                )

        await adapter.send(source.chat_id, content, metadata=metadata)

    def _find_source_by_session_id(self, session_id: str):
        """按 session_id 还原原渠道 SessionSource（desktop relay 用）。

        主路径：遍历 gateway SessionStore._entries（sessions.json 权威索引，
        覆盖全部历史会话；entry.origin 为完整 SessionSource）。
        兜底 1：state.db origin_json（自研最小移植 #58899 格式后写入）。
        兜底 2：state.db chat_id 列重建最小 SessionSource。
        再无 → None（拒绝 relay）。

        边界：SessionStore.prune_old_entries 默认 90 天清理，超龄会话必走兜底。
        """
        # 主路径：SessionStore 内存索引
        store = getattr(self, "session_store", None)
        if store is not None:
            try:
                store._ensure_loaded()
                for entry in store._entries.values():
                    if entry.session_id == session_id and entry.origin is not None:
                        return entry.origin  # 字段名是 origin（session.py:956），不是 source
            except Exception as exc:
                logger.debug("desktop relay: session_store scan failed: %s", exc)
        # 兜底：state.db
        try:
            from gateway.session import SessionSource
            from vermes_state import SessionDB

            db = SessionDB()
            try:
                row = db.get_session(session_id)
            finally:
                db.close()
            if row:
                origin_json = row.get("origin_json")
                if origin_json:
                    return SessionSource.from_dict(json.loads(origin_json))
                if row.get("chat_id"):
                    return SessionSource(
                        platform=Platform(row["source"]),
                        chat_id=row["chat_id"],
                        chat_type=row.get("chat_type") or "dm",
                        thread_id=row.get("thread_id"),
                        user_id=row.get("user_id"),
                    )
        except Exception as exc:
            logger.debug("desktop relay: state.db fallback failed for %s: %s", session_id, exc)
        return None

    async def _handle_message(self, event: MessageEvent) -> Optional[str]:
        """
        Handle an incoming message from any platform.
        
        This is the core message processing pipeline:
        1. Check user authorization
        2. Check for commands (/new, /reset, etc.)
        3. Check for running agent and interrupt if needed
        4. Get or create session
        5. Build context for agent
        6. Run agent conversation
        7. Return response
        """
        source = event.source

        # Internal events (e.g. background-process completion notifications)
        # are system-generated and must skip user authorization.
        is_internal = bool(getattr(event, "internal", False))

        # Fire pre_gateway_dispatch plugin hook for user-originated messages.
        # Plugins receive the MessageEvent and may return a dict influencing flow:
        #   {"action": "skip",    "reason": ...}    -> drop (no reply, plugin handled)
        #   {"action": "rewrite", "text":  ...}     -> replace event.text, continue
        #   {"action": "allow"}   /   None          -> normal dispatch
        # Hook runs BEFORE auth so plugins can handle unauthorized senders
        # (e.g. customer handover ingest) without triggering the pairing flow.
        if not is_internal:
            try:
                from vermes_cli.plugins import invoke_hook as _invoke_hook
                _hook_results = _invoke_hook(
                    "pre_gateway_dispatch",
                    event=event,
                    gateway=self,
                    session_store=self.session_store,
                )
            except Exception as _hook_exc:
                logger.warning("pre_gateway_dispatch invocation failed: %s", _hook_exc)
                _hook_results = []

            for _result in _hook_results:
                if not isinstance(_result, dict):
                    continue
                _action = _result.get("action")
                if _action == "skip":
                    logger.info(
                        "pre_gateway_dispatch skip: reason=%s platform=%s chat=%s",
                        _result.get("reason"),
                        source.platform.value if source.platform else "unknown",
                        source.chat_id or "unknown",
                    )
                    return None
                if _action == "rewrite":
                    _new_text = _result.get("text")
                    if isinstance(_new_text, str):
                        event = dataclasses.replace(event, text=_new_text)
                        source = event.source
                    break
                if _action == "allow":
                    break

        if is_internal:
            pass
        elif source.user_id is None:
            # Messages with no user identity (Telegram service messages,
            # channel forwards, anonymous admin posts, sender_chat) can't
            # be paired, but they can still be authorized via a
            # chat-scoped allowlist (e.g. TELEGRAM_GROUP_ALLOWED_CHATS
            # authorizes every member of the listed chat regardless of
            # sender). Defer to _is_user_authorized so that path runs.
            if not self._is_user_authorized(source):
                logger.debug("Ignoring message with no user_id from %s", source.platform.value)
                return None
        elif not self._is_user_authorized(source):
            logger.warning("Unauthorized user: %s (%s) on %s", source.user_id, source.user_name, source.platform.value)
            # In DMs: offer pairing code. In groups: silently ignore.
            if source.chat_type == "dm" and self._get_unauthorized_dm_behavior(source.platform) == "pair":
                platform_name = source.platform.value if source.platform else "unknown"
                # Rate-limit ALL pairing responses (code or rejection) to
                # prevent spamming the user with repeated messages when
                # multiple DMs arrive in quick succession.
                if self.pairing_store._is_rate_limited(platform_name, source.user_id):
                    return None
                code = self.pairing_store.generate_code(
                    platform_name, source.user_id, source.user_name or ""
                )
                if code:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            f"Hi~ I don't recognize you yet!\n\n"
                            f"Here's your pairing code: `{code}`\n\n"
                            f"Ask the bot owner to run:\n"
                            f"`hermes pairing approve {platform_name} {code}`"
                        )
                else:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        await adapter.send(
                            source.chat_id,
                            "Too many pairing requests right now~ "
                            "Please try again later!"
                        )
                    # Record rate limit so subsequent messages are silently ignored
                    self.pairing_store._record_rate_limit(platform_name, source.user_id)
            return None
        
        # Intercept messages that are responses to a pending /update prompt.
        # The update process (detached) wrote .update_prompt.json; the watcher
        # forwarded it to the user; now the user's reply goes back via
        # .update_response so the update process can continue.
        #
        # IMPORTANT: recognized slash commands must bypass this interception.
        # Otherwise control/session commands like /new or /help get silently
        # consumed as update answers instead of being dispatched normally.
        _quick_key = self._session_key_for_source(source)
        _update_prompts = getattr(self, "_update_prompt_pending", {})
        if _update_prompts.get(_quick_key):
            raw = (event.text or "").strip()
            # Accept /approve and /deny as shorthand for yes/no
            cmd = event.get_command()
            if cmd in {"approve", "yes"}:
                response_text = "y"
            elif cmd in {"deny", "no"}:
                response_text = "n"
            else:
                _recognized_cmd = None
                if cmd:
                    try:
                        from vermes_cli.commands import resolve_command as _resolve_update_cmd
                    except Exception:
                        _resolve_update_cmd = None
                    if _resolve_update_cmd is not None:
                        try:
                            _cmd_def = _resolve_update_cmd(cmd)
                            _recognized_cmd = _cmd_def.name if _cmd_def else None
                        except Exception:
                            _recognized_cmd = None
                if _recognized_cmd:
                    response_text = ""
                else:
                    response_text = raw
            if response_text:
                response_path = _get_run_attr("_hermes_home") / ".update_response"
                prompt_path = _get_run_attr("_hermes_home") / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text(response_text)
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Failed to write update response: %s", e)
                    return f"✗ Failed to send response to update process: {e}"
                _update_prompts.pop(_quick_key, None)
                label = response_text if len(response_text) <= 20 else response_text[:20] + "…"
                return f"✓ Sent `{label}` to the update process."
            # Recognized slash command during a pending update prompt:
            # unblock the detached update subprocess by writing a blank
            # response so ``_gateway_prompt`` returns the prompt's default
            # (typically a safe "n" / skip) and exits cleanly instead of
            # blocking on stdin until the 30-minute watcher timeout.
            # The slash command then falls through to normal dispatch.
            if _recognized_cmd:
                response_path = _get_run_attr("_hermes_home") / ".update_response"
                prompt_path = _get_run_attr("_hermes_home") / ".update_prompt.json"
                try:
                    tmp = response_path.with_suffix(".tmp")
                    tmp.write_text("")
                    tmp.replace(response_path)
                    prompt_path.unlink(missing_ok=True)
                    logger.info(
                        "Recognized /%s during pending update prompt for %s; "
                        "cancelled prompt with default and dispatching command",
                        _recognized_cmd,
                        _quick_key,
                    )
                except OSError as e:
                    logger.warning(
                        "Failed to write cancel response for pending update prompt: %s",
                        e,
                    )
                _update_prompts.pop(_quick_key, None)

        # Intercept messages that are responses to a pending clarify
        # request that is awaiting free-form text (either an open-ended
        # clarify with no choices, or one where the user picked the
        # "Other" button).  The first non-empty user message in the
        # session resolves the clarify and unblocks the agent thread —
        # we do NOT route it to the agent as a new turn.
        try:
            from tools import clarify_gateway as _clarify_mod
            _pending_clarify = _clarify_mod.get_pending_for_session(_quick_key)
        except Exception:
            _pending_clarify = None
        if _pending_clarify is not None:
            _raw_clarify_reply = (event.text or "").strip()
            # Skip slash commands — the user clearly wanted to issue a
            # command, not answer the clarify.  Leave the clarify pending
            # so the user can retry; if it times out, the agent unblocks
            # with an empty response.
            if _raw_clarify_reply and not _raw_clarify_reply.startswith("/"):
                _resolved = _clarify_mod.resolve_gateway_clarify(
                    _pending_clarify.clarify_id, _raw_clarify_reply,
                )
                if _resolved:
                    logger.info(
                        "Gateway intercepted clarify text response (session=%s, id=%s)",
                        _quick_key, _pending_clarify.clarify_id,
                    )
                    # Acknowledge with empty string so adapters that emit
                    # the agent's response don't double-post.  The agent
                    # itself will produce the next user-facing message.
                    return ""

        # Intercept messages that are responses to a pending /reload-mcp
        # (or future) slash-confirm prompt.  Recognized confirm replies are
        # /approve, /always, /cancel (plus short aliases).  Anything else
        # falls through to normal dispatch — a stale pending confirm does
        # NOT block other commands.
        #
        # Important: if a dangerous-command approval is ALSO pending (agent
        # blocked inside tools/approval.py), the tool approval takes
        # precedence — /approve there unblocks the waiting tool thread.
        # Slash-confirm only catches /approve when no tool approval is live.
        from tools import slash_confirm as _slash_confirm_mod
        _pending_confirm = _slash_confirm_mod.get_pending(_quick_key)
        _tool_approval_live = False
        try:
            from tools.approval import has_blocking_approval
            _tool_approval_live = has_blocking_approval(_quick_key)
        except Exception:
            _tool_approval_live = False
        if _pending_confirm and not _tool_approval_live:
            _raw_reply = (event.text or "").strip()
            _cmd_reply = event.get_command()
            _confirm_choice = None
            if _cmd_reply in {"approve", "yes", "ok", "confirm"}:
                _confirm_choice = "once"
            elif _cmd_reply in {"always", "remember"}:
                _confirm_choice = "always"
            elif _cmd_reply in {"cancel", "no", "deny", "nevermind"}:
                _confirm_choice = "cancel"
            elif _raw_reply.lower() in {"approve", "approve once", "once"}:
                _confirm_choice = "once"
            elif _raw_reply.lower() in {"always", "always approve"}:
                _confirm_choice = "always"
            elif _raw_reply.lower() in {"cancel", "nevermind", "no"}:
                _confirm_choice = "cancel"
            if _confirm_choice is not None:
                _resolved = await _slash_confirm_mod.resolve(
                    _quick_key, _pending_confirm.get("confirm_id"), _confirm_choice,
                )
                return _resolved or ""
            # Stale pending + unrelated command: drop the pending state so
            # the confirm doesn't block normal usage indefinitely.  The user
            # clearly moved on.
            _slash_confirm_mod.clear_if_stale(_quick_key)

        # PRIORITY handling when an agent is already running for this session.
        # Default behavior is to interrupt immediately so user text/stop messages
        # are handled with minimal latency.
        #
        # Special case: Telegram/photo bursts often arrive as multiple near-
        # simultaneous updates. Do NOT interrupt for photo-only follow-ups here;
        # let the adapter-level batching/queueing logic absorb them.

        # Staleness eviction: detect leaked locks from hung/crashed handlers.
        # With inactivity-based timeout, active tasks can run for hours, so
        # wall-clock age alone isn't sufficient.  Evict only when the agent
        # has been *idle* beyond the inactivity threshold (or when the agent
        # object has no activity tracker and wall-clock age is extreme).
        _raw_stale_timeout = _get_run_attr("_float_env")("HERMES_AGENT_TIMEOUT", 1800)
        _stale_ts = self._running_agents_ts.get(_quick_key, 0)
        if _quick_key in self._running_agents and _stale_ts:
            _stale_age = time.time() - _stale_ts
            _stale_agent = self._running_agents.get(_quick_key)
            # Never evict the pending sentinel — it was just placed moments
            # ago during the async setup phase before the real agent is
            # created.  Sentinels have no get_activity_summary(), so the
            # idle check below would always evaluate to inf >= timeout and
            # immediately evict them, racing with the setup path.
            _stale_idle = float("inf")  # assume idle if we can't check
            _stale_detail = ""
            if _stale_agent and hasattr(_stale_agent, "get_activity_summary"):
                try:
                    _sa = _stale_agent.get_activity_summary()
                    _stale_idle = _sa.get("seconds_since_activity", float("inf"))
                    _stale_detail = (
                        f" | last_activity={_sa.get('last_activity_desc', 'unknown')} "
                        f"({_stale_idle:.0f}s ago) "
                        f"| iteration={_sa.get('api_call_count', 0)}/{_sa.get('max_iterations', 0)}"
                    )
                except Exception:
                    pass
            # Evict if: agent is idle beyond timeout, OR wall-clock age is
            # extreme (10x timeout or 2h, whichever is larger — catches
            # cases where the agent object was garbage-collected).
            _wall_ttl = max(_raw_stale_timeout * 10, 7200) if _raw_stale_timeout > 0 else float("inf")
            _should_evict = (
                _stale_agent is not _get_run_attr("_AGENT_PENDING_SENTINEL")
                and (
                    (_raw_stale_timeout > 0 and _stale_idle >= _raw_stale_timeout)
                    or _stale_age > _wall_ttl
                )
            )
            if _should_evict:
                logger.warning(
                    "Evicting stale _running_agents entry for %s "
                    "(age: %.0fs, idle: %.0fs, timeout: %.0fs)%s",
                    _quick_key, _stale_age, _stale_idle,
                    _raw_stale_timeout, _stale_detail,
                )
                self._invalidate_session_run_generation(
                    _quick_key,
                    reason="stale_running_agent_eviction",
                )
                self._release_running_agent_state(_quick_key)

        if _quick_key in self._running_agents:
            if event.get_command() == "status":
                return await self._handle_status_command(event)

            # Resolve the command once for all early-intercept checks below.
            from vermes_cli.commands import (
                ACTIVE_SESSION_BYPASS_COMMANDS as _DEDICATED_HANDLERS,
                resolve_command as _resolve_cmd_inner,
            )
            _evt_cmd = event.get_command()
            _cmd_def_inner = _resolve_cmd_inner(_evt_cmd) if _evt_cmd else None

            # Slash command access control on the running-agent fast-path.
            # Mirrors the cold-path gate further below so non-admin users
            # can't bypass gating just because an agent happens to be busy.
            # /status above is intentionally pre-gate so users always see
            # session state. /help and /whoami fall under the always-allowed
            # floor inside _check_slash_access.
            if _evt_cmd and _cmd_def_inner is not None:
                _denied = self._check_slash_access(source, _cmd_def_inner.name)
                if _denied is not None:
                    return _denied

            if _cmd_def_inner and _cmd_def_inner.name == "restart":
                return await self._handle_restart_command(event)

            # /stop must hard-kill the session when an agent is running.
            # A soft interrupt (agent.interrupt()) doesn't help when the agent
            # is truly hung — the executor thread is blocked and never checks
            # _interrupt_requested.  Force-clean _running_agents so the session
            # is unlocked and subsequent messages are processed normally.
            if _cmd_def_inner and _cmd_def_inner.name == "stop":
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=_get_run_attr("_INTERRUPT_REASON_STOP"),
                    invalidation_reason="stop_command",
                )
                logger.info("STOP for session %s — agent interrupted, session lock released", _quick_key)
                return EphemeralReply(t("gateway.stop.stopped"))

            # /reset and /new must bypass the running-agent guard so they
            # actually dispatch as commands instead of being queued as user
            # text (which would be fed back to the agent with the same
            # broken history — #2170).  Interrupt the agent first, then
            # clear the adapter's pending queue so the stale "/reset" text
            # doesn't get re-processed as a user message after the
            # interrupt completes.
            if _cmd_def_inner and _cmd_def_inner.name == "new":
                # Clear any pending messages so the old text doesn't replay
                await self._interrupt_and_clear_session(
                    _quick_key,
                    source,
                    interrupt_reason=_get_run_attr("_INTERRUPT_REASON_RESET"),
                    invalidation_reason="new_command",
                )
                # Clean up the running agent entry so the reset handler
                # doesn't think an agent is still active.
                return await self._handle_reset_command(event)

            # /queue <prompt> — queue without interrupting.
            # Semantics: each /queue invocation produces its own full agent
            # turn, processed in FIFO order after the current run (and any
            # earlier /queue items) finishes.  Messages are NOT merged.
            if event.get_command() in {"queue", "q"}:
                queued_text = event.get_command_args().strip()
                if not queued_text:
                    return "Usage: /queue <prompt>"
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=queued_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    self._enqueue_fifo(_quick_key, queued_event, adapter)
                depth = self._queue_depth(_quick_key, adapter=self.adapters.get(source.platform))
                if depth <= 1:
                    return "Queued for the next turn."
                return f"Queued for the next turn. ({depth} queued)"

            # /steer <prompt> — inject mid-run after the next tool call.
            # Unlike /queue (turn boundary), /steer lands BETWEEN tool-call
            # iterations inside the same agent run, by appending to the
            # last tool result's content. No interrupt, no new user turn,
            # no role-alternation violation.
            if _cmd_def_inner and _cmd_def_inner.name == "steer":
                steer_text = event.get_command_args().strip()
                if not steer_text:
                    return "Usage: /steer <prompt>"
                running_agent = self._running_agents.get(_quick_key)
                if running_agent is _get_run_attr("_AGENT_PENDING_SENTINEL"):
                    # Agent hasn't started yet — queue as turn-boundary fallback.
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        queued_event = MessageEvent(
                            text=steer_text,
                            message_type=MessageType.TEXT,
                            source=event.source,
                            message_id=event.message_id,
                            channel_prompt=event.channel_prompt,
                        )
                        adapter._pending_messages[_quick_key] = queued_event
                    return "Agent still starting — /steer queued for the next turn."
                if running_agent and hasattr(running_agent, "steer"):
                    try:
                        accepted = running_agent.steer(steer_text)
                    except Exception as exc:
                        logger.warning("Steer failed for session %s: %s", _quick_key, exc)
                        return f"⚠️ Steer failed: {exc}"
                    if accepted:
                        preview = steer_text[:60] + ("..." if len(steer_text) > 60 else "")
                        return f"⏩ Steer queued — arrives after the next tool call: '{preview}'"
                    return "Steer rejected (empty payload)."
                # Running agent is missing or lacks steer() — fall back to queue.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    queued_event = MessageEvent(
                        text=steer_text,
                        message_type=MessageType.TEXT,
                        source=event.source,
                        message_id=event.message_id,
                        channel_prompt=event.channel_prompt,
                    )
                    adapter._pending_messages[_quick_key] = queued_event
                return "No active agent — /steer queued for the next turn."

            # /model must not be used while the agent is running.
            if _cmd_def_inner and _cmd_def_inner.name == "model":
                return "Agent is running — wait or /stop first, then switch models."

            # /codex-runtime must not be used while the agent is running.
            # Switching mid-turn would split a turn across two transports.
            if _cmd_def_inner and _cmd_def_inner.name == "codex-runtime":
                return ("Agent is running — wait or /stop first, then "
                        "change runtime.")

            # /approve and /deny must bypass the running-agent interrupt path.
            # The agent thread is blocked on a threading.Event inside
            # tools/approval.py — sending an interrupt won't unblock it.
            # Route directly to the approval handler so the event is signalled.
            if _cmd_def_inner and _cmd_def_inner.name in {"approve", "deny"}:
                if _cmd_def_inner.name == "approve":
                    return await self._handle_approve_command(event)
                return await self._handle_deny_command(event)

            # /agents (/tasks alias) should be query-only and never interrupt.
            if _cmd_def_inner and _cmd_def_inner.name == "agents":
                return await self._handle_agents_command(event)

            # /background must bypass the running-agent guard — it starts a
            # parallel task and must never interrupt the active conversation.
            # /btw is an alias of /background and resolves to the same canonical
            # name, so this branch handles both commands.
            if _cmd_def_inner and _cmd_def_inner.name == "background":
                return await self._handle_background_command(event)

            # /kanban must bypass the guard. It writes to a profile-agnostic
            # DB (kanban.db), not to the running agent's state. In fact
            # /kanban unblock is often the only way to free a worker that
            # has blocked waiting for a peer — letting that be dispatched
            # mid-run is the whole point of the board.
            if _cmd_def_inner and _cmd_def_inner.name == "kanban":
                return await self._handle_kanban_command(event)

            # /goal is safe mid-run for status/pause/clear (inspection and
            # control-plane only — doesn't interrupt the running turn).
            # Setting a new goal text mid-run is rejected with the same
            # "wait or /stop" message as /model so we don't race a second
            # continuation prompt against the current turn.
            if _cmd_def_inner and _cmd_def_inner.name == "goal":
                _goal_arg = (event.get_command_args() or "").strip().lower()
                if not _goal_arg or _goal_arg in {"status", "pause", "resume", "clear", "stop", "done"}:
                    return await self._handle_goal_command(event)
                return "Agent is running — use /goal status / pause / clear mid-run, or /stop before setting a new goal."

            # /subgoal is safe mid-run — it only modifies the goal's
            # subgoals list, which the judge reads at the next turn
            # boundary. No race with the running turn.
            if _cmd_def_inner and _cmd_def_inner.name == "subgoal":
                return await self._handle_subgoal_command(event)

            # Session-level toggles that are safe to run mid-agent —
            # /yolo can unblock a pending approval prompt, /verbose cycles
            # the tool-progress display mode for the ongoing stream.
            # Both modify session state without needing agent interaction
            # and must not be queued (the safety net would discard them).
            # /fast and /reasoning are config-only and take effect next
            # message, so they fall through to the catch-all busy response
            # below — users should wait and set them between turns.
            if _cmd_def_inner and _cmd_def_inner.name in {"yolo", "verbose"}:
                if _cmd_def_inner.name == "yolo":
                    return await self._handle_yolo_command(event)
                if _cmd_def_inner.name == "verbose":
                    return await self._handle_verbose_command(event)
                if _cmd_def_inner.name == "footer":
                    return await self._handle_footer_command(event)

            # Gateway-handled info/control commands with dedicated
            # running-agent handlers.
            if _cmd_def_inner and _cmd_def_inner.name in _DEDICATED_HANDLERS:
                if _cmd_def_inner.name == "help":
                    return await self._handle_help_command(event)
                if _cmd_def_inner.name == "commands":
                    return await self._handle_commands_command(event)
                if _cmd_def_inner.name == "profile":
                    return await self._handle_profile_command(event)
                if _cmd_def_inner.name == "update":
                    return await self._handle_update_command(event)

            # Catch-all: any other recognized slash command reached the
            # running-agent guard. Reject gracefully rather than falling
            # through to interrupt + discard. Without this, commands
            # like /model, /reasoning, /voice, /insights, /title,
            # /resume, /retry, /undo, /compress, /usage,
            # /reload-mcp, /sethome, /reset (all registered as Discord
            # slash commands) would interrupt the agent AND get
            # silently discarded by the slash-command safety net,
            # producing a zero-char response. See #5057, #6252, #10370.
            if _cmd_def_inner:
                return (
                    f"⏳ Agent is running — `/{_cmd_def_inner.name}` can't run "
                    f"mid-turn. Wait for the current response or `/stop` first."
                )

            if event.message_type == MessageType.PHOTO:
                logger.debug("PRIORITY photo follow-up for session %s — queueing without interrupt", _quick_key)
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(adapter._pending_messages, _quick_key, event)
                return None

            _telegram_followup_grace = float(
                os.getenv("HERMES_TELEGRAM_FOLLOWUP_GRACE_SECONDS", "3.0")
            )
            _started_at = self._running_agents_ts.get(_quick_key, 0)
            if (
                source.platform == Platform.TELEGRAM
                and event.message_type == MessageType.TEXT
                and _telegram_followup_grace > 0
                and _started_at
                and (time.time() - _started_at) <= _telegram_followup_grace
            ):
                logger.debug(
                    "Telegram follow-up arrived %.2fs after run start for %s — queueing without interrupt",
                    time.time() - _started_at,
                    _quick_key,
                )
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None

            running_agent = self._running_agents.get(_quick_key)
            if running_agent is _get_run_attr("_AGENT_PENDING_SENTINEL"):
                # Agent is being set up but not ready yet.
                if event.get_command() == "stop":
                    # Force-clean the sentinel so the session is unlocked.
                    self._release_running_agent_state(_quick_key)
                    logger.info("HARD STOP (pending) for session %s — sentinel cleared", _quick_key)
                    return EphemeralReply("⚡ Force-stopped. The agent was still starting — session unlocked.")
                # Queue the message so it will be picked up after the
                # agent starts.
                adapter = self.adapters.get(source.platform)
                if adapter:
                    merge_pending_message_event(
                        adapter._pending_messages,
                        _quick_key,
                        event,
                        merge_text=True,
                    )
                return None
            if self._draining:
                if self._queue_during_drain_enabled():
                    self._queue_or_replace_pending_event(_quick_key, event)
                return (
                    f"⏳ Gateway {self._status_action_gerund()} — queued for the next turn after it comes back."
                    if self._queue_during_drain_enabled()
                    else f"⏳ Gateway is {self._status_action_gerund()} and is not accepting another turn right now."
                )
            if self._busy_input_mode == "queue":
                logger.debug("PRIORITY queue follow-up for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None
            if self._busy_input_mode == "steer":
                # Steer mode: inject text into the running agent mid-run via
                # agent.steer().  Falls back to queue semantics if the payload
                # is empty, the agent lacks steer(), or steer() rejects.
                steer_text = (event.text or "").strip()
                steered = False
                if steer_text and hasattr(running_agent, "steer"):
                    try:
                        steered = bool(running_agent.steer(steer_text))
                    except Exception as exc:
                        logger.warning("PRIORITY steer failed for session %s: %s", _quick_key, exc)
                        steered = False
                if steered:
                    logger.debug("PRIORITY steer for session %s", _quick_key)
                    return None
                logger.debug("PRIORITY steer-fallback-to-queue for session %s", _quick_key)
                self._queue_or_replace_pending_event(_quick_key, event)
                return None
            logger.debug("PRIORITY interrupt for session %s", _quick_key)
            running_agent.interrupt(event.text)
            # NOTE: self._pending_messages was write-only (never consumed).
            # The actual interrupt message is delivered via adapter._pending_messages
            # which is read by _run_agent. Removed to prevent unbounded growth.
            return None

        # Check for commands
        command = event.get_command()

        from vermes_cli.commands import (
            GATEWAY_KNOWN_COMMANDS,
            is_gateway_known_command,
            resolve_command as _resolve_cmd,
        )

        # Resolve aliases to canonical name so dispatch and hook names
        # don't depend on the exact alias the user typed.
        _cmd_def = _resolve_cmd(command) if command else None
        canonical = _cmd_def.name if _cmd_def else command

        # Expand alias quick commands before built-in dispatch so targets like
        # /model openai/gpt-5.5 --provider openrouter reach the /model handler.
        # Preserve built-in precedence; aliases only need early handling when
        # the typed command is not already known.
        if command and _cmd_def is None:
            if isinstance(self.config, dict):
                quick_commands = self.config.get("quick_commands", {}) or {}
            else:
                quick_commands = getattr(self.config, "quick_commands", {}) or {}
            if isinstance(quick_commands, dict) and command in quick_commands:
                qcmd = quick_commands[command]
                if qcmd.get("type") == "alias":
                    target = qcmd.get("target", "").strip()
                    if target:
                        target = target if target.startswith("/") else f"/{target}"
                        target_command = target.lstrip("/")
                        user_args = event.get_command_args().strip()
                        event.text = f"{target} {user_args}".strip()
                        command = target_command.split()[0] if target_command else target_command
                        _cmd_def = _resolve_cmd(command) if command else None
                        canonical = _cmd_def.name if _cmd_def else command

        # Per-platform slash command access control. Only kicks in when the
        # operator has set ``allow_admin_from`` for the source's scope (DM
        # vs group). When unset → backward-compat: every allowed user can
        # run every command. When set → non-admins can run only commands in
        # ``user_allowed_commands`` (plus the always-allowed floor: /help,
        # /whoami). Plain chat is unaffected — only slash commands gate.
        if command and canonical and is_gateway_known_command(canonical):
            _denied = self._check_slash_access(source, canonical)
            if _denied is not None:
                return _denied

        # Fire the ``command:<canonical>`` hook for any recognized slash
        # command — built-in OR plugin-registered. Handlers can return a
        # dict with ``{"decision": "deny" | "handled" | "rewrite", ...}``
        # to intercept dispatch before core handling runs. This replaces
        # the previous fire-and-forget emit(): return values are now
        # honored, but handlers that return nothing behave exactly as
        # before (telemetry-style hooks keep working).
        if command and is_gateway_known_command(canonical):
            raw_args = event.get_command_args().strip()
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "command": canonical,
                "raw_command": command,
                "args": raw_args,
                "raw_args": raw_args,
            }
            try:
                hook_results = await self.hooks.emit_collect(
                    f"command:{canonical}", hook_ctx
                )
            except Exception as _hook_err:
                logger.debug(
                    "command:%s hook dispatch failed (non-fatal): %s",
                    canonical, _hook_err,
                )
                hook_results = []

            for hook_result in hook_results:
                if not isinstance(hook_result, dict):
                    continue
                decision = str(hook_result.get("decision", "")).strip().lower()
                if not decision or decision == "allow":
                    continue
                if decision == "deny":
                    message = hook_result.get("message")
                    if isinstance(message, str) and message:
                        return message
                    return f"Command `/{command}` was blocked by a hook."
                if decision == "handled":
                    message = hook_result.get("message")
                    return message if isinstance(message, str) and message else None
                if decision == "rewrite":
                    new_command = str(
                        hook_result.get("command_name", "")
                    ).strip().lstrip("/")
                    if not new_command:
                        continue
                    new_args = str(hook_result.get("raw_args", "")).strip()
                    event.text = f"/{new_command} {new_args}".strip()
                    command = event.get_command()
                    _cmd_def = _resolve_cmd(command) if command else None
                    canonical = _cmd_def.name if _cmd_def else command
                    break

        if canonical == "new":
            if self._is_telegram_topic_root_lobby(source):
                return self._telegram_topic_root_new_message()
            async def _do_reset():
                return await self._handle_reset_command(event)
            return await self._maybe_confirm_destructive_slash(
                event=event,
                command="new",
                title="/new",
                detail=(
                    "This starts a fresh session and discards the current "
                    "conversation history."
                ),
                execute=_do_reset,
            )

        if canonical == "topic":
            return await self._handle_topic_command(event)
        
        if canonical == "help":
            return await self._handle_help_command(event)

        if canonical == "commands":
            return await self._handle_commands_command(event)
        
        if canonical == "profile":
            return await self._handle_profile_command(event)

        if canonical == "whoami":
            return await self._handle_whoami_command(event)

        if canonical == "status":
            return await self._handle_status_command(event)

        if canonical == "agents":
            return await self._handle_agents_command(event)

        if canonical == "platform":
            return await self._handle_platform_command(event)

        if canonical == "restart":
            return await self._handle_restart_command(event)
        
        if canonical == "stop":
            return await self._handle_stop_command(event)
        
        if canonical == "reasoning":
            return await self._handle_reasoning_command(event)

        if canonical == "fast":
            return await self._handle_fast_command(event)

        if canonical == "verbose":
            return await self._handle_verbose_command(event)

        if canonical == "footer":
            return await self._handle_footer_command(event)

        if canonical == "yolo":
            return await self._handle_yolo_command(event)

        if canonical == "model":
            return await self._handle_model_command(event)

        if canonical == "codex-runtime":
            return await self._handle_codex_runtime_command(event)

        if canonical == "personality":
            return await self._handle_personality_command(event)

        if canonical == "kanban":
            return await self._handle_kanban_command(event)

        if canonical == "retry":
            return await self._handle_retry_command(event)
        
        if canonical == "undo":
            async def _do_undo():
                return await self._handle_undo_command(event)
            return await self._maybe_confirm_destructive_slash(
                event=event,
                command="undo",
                title="/undo",
                detail="This removes the last user/assistant exchange from history.",
                execute=_do_undo,
            )
        
        if canonical == "sethome":
            return await self._handle_set_home_command(event)

        if canonical == "compress":
            return await self._handle_compress_command(event)

        if canonical == "usage":
            return await self._handle_usage_command(event)

        if canonical == "insights":
            return await self._handle_insights_command(event)

        if canonical == "reload-mcp":
            return await self._handle_reload_mcp_command(event)

        if canonical == "reload-skills":
            return await self._handle_reload_skills_command(event)

        if canonical == "bundles":
            return await self._handle_bundles_command(event)

        if canonical == "approve":
            return await self._handle_approve_command(event)

        if canonical == "deny":
            return await self._handle_deny_command(event)

        if canonical == "update":
            return await self._handle_update_command(event)

        if canonical == "debug":
            return await self._handle_debug_command(event)

        if canonical == "emergence":
            return await self._handle_emergence_command(event)

        if canonical == "forget":
            return await self._handle_forget_command(event)

        if canonical == "title":
            return await self._handle_title_command(event)

        if canonical == "resume":
            return await self._handle_resume_command(event)

        if canonical == "branch":
            return await self._handle_branch_command(event)

        if canonical == "rollback":
            return await self._handle_rollback_command(event)

        if canonical == "background":
            return await self._handle_background_command(event)

        if canonical == "steer":
            # No active agent — /steer has no tool call to inject into.
            # Strip the prefix so downstream treats it as a normal user
            # message. If the payload is empty, surface the usage hint.
            steer_payload = event.get_command_args().strip()
            if not steer_payload:
                return "Usage: /steer <prompt>  (no agent is running; sending as a normal message)"
            try:
                event.text = steer_payload
            except Exception:
                pass
            # Do NOT return — fall through to _handle_message_with_agent
            # at the end of this function so the rewritten text is sent
            # to the agent as a regular user turn.

        if canonical == "goal":
            return await self._handle_goal_command(event)

        if canonical == "subgoal":
            return await self._handle_subgoal_command(event)

        if canonical == "voice":
            return await self._handle_voice_command(event)

        if self._draining:
            return f"⏳ Gateway is {self._status_action_gerund()} and is not accepting new work right now."

        # User-defined quick commands (bypass agent loop, no LLM call)
        if command:
            if isinstance(self.config, dict):
                quick_commands = self.config.get("quick_commands", {}) or {}
            else:
                quick_commands = getattr(self.config, "quick_commands", {}) or {}
            if not isinstance(quick_commands, dict):
                quick_commands = {}
            if command in quick_commands:
                qcmd = quick_commands[command]
                if qcmd.get("type") == "exec":
                    exec_cmd = qcmd.get("command", "")
                    if exec_cmd:
                        try:
                            # Sanitize env to prevent credential leakage —
                            # quick commands run in the gateway process which
                            # has all API keys in os.environ.
                            from tools.environments.local import _sanitize_subprocess_env
                            sanitized_env = _sanitize_subprocess_env(os.environ.copy())
                            proc = await asyncio.create_subprocess_shell(
                                exec_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                env=sanitized_env,
                            )
                            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                            output = (stdout or stderr).decode().strip()
                            # Redact any remaining sensitive patterns in output
                            if output:
                                from agent.redact import redact_sensitive_text
                                output = redact_sensitive_text(output)
                            return output if output else "Command returned no output."
                        except asyncio.TimeoutError:
                            return "Quick command timed out (30s)."
                        except Exception as e:
                            return f"Quick command error: {e}"
                    else:
                        return f"Quick command '/{command}' has no command defined."
                elif qcmd.get("type") == "alias":
                    target = qcmd.get("target", "").strip()
                    if target:
                        target = target if target.startswith("/") else f"/{target}"
                        target_command = target.lstrip("/")
                        user_args = event.get_command_args().strip()
                        event.text = f"{target} {user_args}".strip()
                        command = target_command.split()[0] if target_command else target_command
                        # Fall through to normal command dispatch below
                    else:
                        return f"Quick command '/{command}' has no target defined."
                else:
                    return f"Quick command '/{command}' has unsupported type (supported: 'exec', 'alias')."

        # Plugin-registered slash commands
        if command:
            try:
                from vermes_cli.plugins import get_plugin_command_handler
                # Normalize underscores to hyphens so Telegram's underscored
                # autocomplete form matches plugin commands registered with
                # hyphens. See vermes_cli/commands.py:_build_telegram_menu.
                plugin_handler = get_plugin_command_handler(command.replace("_", "-"))
                if plugin_handler:
                    user_args = event.get_command_args().strip()
                    result = plugin_handler(user_args)
                    if asyncio.iscoroutine(result):
                        result = await result
                    return str(result) if result else None
            except Exception as e:
                logger.debug("Plugin command dispatch failed (non-fatal): %s", e)

        # Skill slash commands: /skill-name loads the skill and sends to agent.
        # resolve_skill_command_key() handles the Telegram underscore/hyphen
        # round-trip so /claude_code from Telegram autocomplete still resolves
        # to the claude-code skill.
        if command:
            # Skill bundles take precedence over individual skill commands —
            # /<bundle> loads multiple skills at once. Mirrors CLI dispatch.
            _bundle_handled = False
            try:
                from agent.skill_bundles import (
                    build_bundle_invocation_message,
                    resolve_bundle_command_key,
                )
                bundle_key = resolve_bundle_command_key(command)
                if bundle_key is not None:
                    user_instruction = event.get_command_args().strip()
                    bundle_result = build_bundle_invocation_message(
                        bundle_key, user_instruction, task_id=_quick_key
                    )
                    if bundle_result:
                        msg, _loaded, missing = bundle_result
                        event.text = msg
                        _bundle_handled = True
                        if missing:
                            logger.info(
                                "Bundle %s skipped missing skills: %s",
                                bundle_key, ", ".join(missing),
                            )
                        # Fall through to normal message processing with bundle content
            except Exception as exc:
                logger.debug("Bundle dispatch failed (non-fatal): %s", exc)

        if command and not locals().get("_bundle_handled", False):
            try:
                from agent.skill_commands import (
                    get_skill_commands,
                    build_skill_invocation_message,
                    resolve_skill_command_key,
                )
                skill_cmds = get_skill_commands()
                cmd_key = resolve_skill_command_key(command)
                if cmd_key is not None:
                    # Check per-platform disabled status before executing.
                    # get_skill_commands() only applies the *global* disabled
                    # list at scan time; per-platform overrides need checking
                    # here because the cache is process-global across platforms.
                    _skill_name = skill_cmds[cmd_key].get("name", "")
                    _plat = source.platform.value if source.platform else None
                    if _plat and _skill_name:
                        from agent.skill_utils import get_disabled_skill_names as _get_plat_disabled
                        if _skill_name in _get_plat_disabled(platform=_plat):
                            return (
                                f"The **{_skill_name}** skill is disabled for {_plat}.\n"
                                f"Enable it with: `hermes skills config`"
                            )
                    user_instruction = event.get_command_args().strip()
                    msg = build_skill_invocation_message(
                        cmd_key, user_instruction, task_id=_quick_key
                    )
                    if msg:
                        event.text = msg
                        # Fall through to normal message processing with skill content
                else:
                    # Not an active skill — check if it's a known-but-disabled or
                    # uninstalled skill and give actionable guidance.
                    _unavail_msg = _get_run_attr("_check_unavailable_skill")(command)
                    if _unavail_msg:
                        return _unavail_msg
                    # Genuinely unrecognized /command: not a built-in, not a
                    # plugin, not a skill, not a known-inactive skill. Warn
                    # the user instead of silently forwarding it to the LLM
                    # as free text (which leads to silent-failure behavior
                    # like the model inventing a delegate_task call).
                    # Normalize to hyphenated form before checking known
                    # built-ins (command may be an alias target set by the
                    # quick-command block above, so _cmd_def can be stale).
                    if command.replace("_", "-") not in GATEWAY_KNOWN_COMMANDS:
                        logger.warning(
                            "Unrecognized slash command /%s from %s — "
                            "replying with unknown-command notice",
                            command,
                            source.platform.value if source.platform else "?",
                        )
                        return (
                            f"Unknown command `/{command}`. "
                            f"Type /commands to see what's available, "
                            f"or resend without the leading slash to send "
                            f"as a regular message."
                        )
            except Exception as e:
                logger.debug("Skill command check failed (non-fatal): %s", e)
        
        # Pending exec approvals are handled by /approve and /deny commands above.
        # No bare text matching — "yes" in normal conversation must not trigger
        # execution of a dangerous command.

        if self._is_telegram_topic_root_lobby(source):
            # Debounce the lobby reminder so a user who forgets about
            # topic mode and fires ten prompts doesn't get ten copies.
            if self._should_send_telegram_lobby_reminder(source):
                return self._telegram_topic_root_lobby_message()
            return None

        # ── Claim this session before any await ───────────────────────
        # Between here and _run_agent registering the real AIAgent, there
        # are numerous await points (hooks, vision enrichment, STT,
        # session hygiene compression).  Without this sentinel a second
        # message arriving during any of those yields would pass the
        # "already running" guard and spin up a duplicate agent for the
        # same session — corrupting the transcript.
        self._running_agents[_quick_key] = _get_run_attr("_AGENT_PENDING_SENTINEL")
        self._running_agents_ts[_quick_key] = time.time()
        _run_generation = self._begin_session_run_generation(_quick_key)

        try:
            _agent_result = await self._handle_message_with_agent(event, source, _quick_key, _run_generation)
            # Goal continuation: after the agent returns a final response
            # for this turn, check any standing /goal — the judge will
            # either mark it done, pause it (budget), or enqueue a
            # continuation prompt back through the adapter FIFO so the
            # next turn makes more progress. Wrapped in try/except so a
            # broken judge never breaks normal message handling.
            try:
                _final_text = ""
                if isinstance(_agent_result, dict):
                    _final_text = str(_agent_result.get("final_response") or "")
                elif isinstance(_agent_result, str):
                    _final_text = _agent_result
                # Skip for empty responses (interrupted / errored) — the
                # judge would almost always say "continue" and we'd loop
                # on error. Let the user drive the next turn.
                if _final_text.strip():
                    try:
                        session_entry = self.session_store.get_or_create_session(source)
                    except Exception:
                        session_entry = None
                    if session_entry is not None:
                        await self._post_turn_goal_continuation(
                            session_entry=session_entry,
                            source=source,
                            final_response=_final_text,
                        )
            except Exception as _goal_exc:
                logger.debug("goal continuation hook failed: %s", _goal_exc)
            return _agent_result
        finally:
            # If _run_agent replaced the sentinel with a real agent and
            # then cleaned it up, this is a no-op.  If we exited early
            # (exception, command fallthrough, etc.) the sentinel must
            # not linger or the session would be permanently locked out.
            if self._running_agents.get(_quick_key) is _get_run_attr("_AGENT_PENDING_SENTINEL"):
                self._release_running_agent_state(_quick_key)
            else:
                # Agent path already cleaned _running_agents; make sure
                # the paired metadata dicts are gone too.
                self._running_agents_ts.pop(_quick_key, None)
                if hasattr(self, "_busy_ack_ts"):
                    self._busy_ack_ts.pop(_quick_key, None)

    async def _prepare_inbound_message_text(
        self,
        *,
        event: MessageEvent,
        source: SessionSource,
        history: List[Dict[str, Any]],
    ) -> Optional[str]:
        """Prepare inbound event text for the agent.

        Keep the normal inbound path and the queued follow-up path on the same
        preprocessing pipeline so sender attribution, image enrichment, STT,
        document notes, reply context, and @ references all behave the same.

        Side effect: buffers per-session native image paths when the active
        model supports native vision AND the user has images attached. The
        caller consumes and clears that session-scoped buffer at the
        ``run_conversation`` site to build a multimodal user turn. When the
        list is empty, the ``_enrich_message_with_vision`` text path has
        already run and images are represented in-text.
        """
        history = history or []
        message_text = event.text or ""
        _group_sessions_per_user = getattr(self.config, "group_sessions_per_user", True)
        _thread_sessions_per_user = getattr(self.config, "thread_sessions_per_user", False)
        # Use the same helper every other call site uses so the write key here
        # matches the consume key at the run_conversation site — even if the
        # session store overrides build_session_key's default behavior.
        session_key = self._session_key_for_source(source)
        # Reset only this session's per-call buffer; other sessions may be
        # concurrently preparing multimodal turns on the same runner.
        self._consume_pending_native_image_paths(session_key)

        _is_shared_multi_user = is_shared_multi_user_session(
            source,
            group_sessions_per_user=_group_sessions_per_user,
            thread_sessions_per_user=_thread_sessions_per_user,
        )
        if _is_shared_multi_user and source.user_name:
            message_text = f"[{source.user_name}] {message_text}"

        # Prepend channel context from history backfill (if any).  This
        # happens after sender-prefix so the prefix only applies to the
        # trigger message, not the backfill block.
        if getattr(event, "channel_context", None):
            message_text = f"{event.channel_context}\n\n[New message]\n{message_text}"

        # Declare at outer scope so the audio-file-paths handling block below
        # remains safe when ``event.media_urls`` is empty (no inner block runs).
        audio_file_paths: list[str] = []

        if event.media_urls:
            image_paths = []
            audio_paths = []
            for i, path in enumerate(event.media_urls):
                mtype = event.media_types[i] if i < len(event.media_types) else ""
                if mtype.startswith("image/") or event.message_type == MessageType.PHOTO:
                    image_paths.append(path)
                # MessageType.AUDIO = audio file attachment (e.g. .mp3, .m4a) — never STT
                # MessageType.VOICE = voice message (Opus/OGG) — always STT
                if event.message_type == MessageType.AUDIO:
                    audio_file_paths.append(path)
                elif event.message_type == MessageType.VOICE or (
                    mtype.startswith("audio/")
                    and event.message_type not in {MessageType.AUDIO, MessageType.DOCUMENT}
                ):
                    audio_paths.append(path)

            if image_paths:
                # Decide routing: native (attach pixels) vs text (vision_analyze
                # pre-run + prepend description).  See agent/image_routing.py.
                _img_mode = self._decide_image_input_mode()
                if _img_mode == "native":
                    # Defer attachment to the run_conversation call site.
                    pending_native = getattr(self, "_pending_native_image_paths_by_session", None)
                    if pending_native is None:
                        pending_native = {}
                        self._pending_native_image_paths_by_session = pending_native
                    pending_native[session_key] = list(image_paths)
                    logger.info(
                        "Image routing: native (model supports vision). %d image(s) will be attached inline.",
                        len(image_paths),
                    )
                else:
                    logger.info(
                        "Image routing: text (mode=%s). Pre-analyzing %d image(s) via vision_analyze.",
                        _img_mode, len(image_paths),
                    )
                    message_text = await self._enrich_message_with_vision(
                        message_text,
                        image_paths,
                    )

            if audio_paths:
                message_text = await self._enrich_message_with_transcription(
                    message_text,
                    audio_paths,
                )
                _stt_fail_markers = (
                    "No STT provider",
                    "STT is disabled",
                    "can't listen",
                    "VOICE_TOOLS_OPENAI_KEY",
                )
                if any(marker in message_text for marker in _stt_fail_markers):
                    _stt_adapter = self.adapters.get(source.platform)
                    _stt_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))
                    if _stt_adapter:
                        try:
                            _stt_msg = (
                                "🎤 I received your voice message but can't transcribe it — "
                                "no speech-to-text provider is configured.\n\n"
                                "To enable voice: install faster-whisper "
                                "(`pip install faster-whisper` in the Hermes venv) "
                                "and set `stt.enabled: true` in config.yaml, "
                                "then /restart the gateway."
                            )
                            if self._has_setup_skill():
                                _stt_msg += "\n\nFor full setup instructions, type: `/skill hermes-agent-setup`"
                            await _stt_adapter.send(
                                source.chat_id,
                                _stt_msg,
                                metadata=_stt_meta,
                            )
                        except Exception:
                            pass

        if audio_file_paths:
            from tools.credential_files import to_agent_visible_cache_path as _to_agent_path
            for _apath in audio_file_paths:
                _basename = os.path.basename(_apath)
                _parts = _basename.split("_", 2)
                _display = _parts[2] if len(_parts) >= 3 else _basename
                _display = re.sub(r'[^\w.\- ]', '_', _display)
                _agent_path = _to_agent_path(_apath)
                _note = (
                    f"[The user sent an audio file attachment: '{_display}'. "
                    f"It is saved at: {_agent_path}. "
                    f"Ask the user what they'd like you to do with it, or pass the path to a transcription or media tool.]"
                )
                message_text = f"{_note}\n\n{message_text}"

        if event.media_urls and event.message_type == MessageType.DOCUMENT:
            import mimetypes as _mimetypes
            from tools.credential_files import to_agent_visible_cache_path

            _TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
            for i, path in enumerate(event.media_urls):
                mtype = event.media_types[i] if i < len(event.media_types) else ""
                if mtype in {"", "application/octet-stream"}:
                    _ext = os.path.splitext(path)[1].lower()
                    if _ext in _TEXT_EXTENSIONS:
                        mtype = "text/plain"
                    else:
                        guessed, _ = _mimetypes.guess_type(path)
                        if guessed:
                            mtype = guessed
                if not mtype.startswith(("application/", "text/")):
                    continue

                basename = os.path.basename(path)
                parts = basename.split("_", 2)
                display_name = parts[2] if len(parts) >= 3 else basename
                display_name = re.sub(r'[^\w.\- ]', '_', display_name)

                # Translate host cache path to in-container path if running under Docker backend.
                # This ensures the agent receives a path it can open inside its sandbox, as the
                # cache directories are auto-mounted at /root/.hermes/cache/* by get_cache_directory_mounts().
                agent_path = to_agent_visible_cache_path(path)

                if mtype.startswith("text/"):
                    context_note = (
                        f"[The user sent a text document: '{display_name}'. "
                        f"Its content has been included below. "
                        f"The file is also saved at: {agent_path}]"
                    )
                else:
                    context_note = (
                        f"[The user sent a document: '{display_name}'. "
                        f"The file is saved at: {agent_path}. "
                        f"Ask the user what they'd like you to do with it.]"
                    )
                message_text = f"{context_note}\n\n{message_text}"

        if getattr(event, "reply_to_text", None) and event.reply_to_message_id:
            # Always inject the reply-to pointer — even when the quoted text
            # already appears in history. The prefix isn't deduplication, it's
            # disambiguation: it tells the agent *which* prior message the user
            # is referencing. History can contain the same or similar text
            # multiple times, and without an explicit pointer the agent has to
            # guess (or answer for both subjects). Token overhead is minimal.
            reply_snippet = event.reply_to_text[:500]
            message_text = f'[Replying to: "{reply_snippet}"]\n\n{message_text}'

        if "@" in message_text:
            try:
                from agent.context_references import preprocess_context_references_async
                from agent.model_metadata import get_model_context_length

                _msg_cwd = os.environ.get("TERMINAL_CWD", os.path.expanduser("~"))
                _msg_runtime = _get_run_attr("_resolve_runtime_agent_kwargs")()
                _msg_config_ctx = None
                try:
                    _msg_cfg = _get_run_attr("_load_gateway_config")()
                    _msg_model_cfg = _msg_cfg.get("model", {})
                    if isinstance(_msg_model_cfg, dict):
                        _msg_raw_ctx = _msg_model_cfg.get("context_length")
                        if _msg_raw_ctx is not None:
                            _msg_config_ctx = int(_msg_raw_ctx)
                except Exception:
                    pass
                _msg_ctx_len = get_model_context_length(
                    self._model,
                    base_url=self._base_url or _msg_runtime.get("base_url") or "",
                    api_key=_msg_runtime.get("api_key") or "",
                    config_context_length=_msg_config_ctx,
                )
                _ctx_result = await preprocess_context_references_async(
                    message_text,
                    cwd=_msg_cwd,
                    context_length=_msg_ctx_len,
                    allowed_root=_msg_cwd,
                )
                if _ctx_result.blocked:
                    _adapter = self.adapters.get(source.platform)
                    if _adapter:
                        await _adapter.send(
                            source.chat_id,
                            "\n".join(_ctx_result.warnings) or "Context injection refused.",
                        )
                    return None
                if _ctx_result.expanded:
                    message_text = _ctx_result.message
            except Exception as exc:
                logger.debug("@ context reference expansion failed: %s", exc)

        return message_text

    def _consume_pending_native_image_paths(self, session_key: str) -> List[str]:
        pending_native = getattr(self, "_pending_native_image_paths_by_session", None)
        if not pending_native:
            return []
        return list(pending_native.pop(session_key, []) or [])



    async def _handle_message_with_agent(self, event, source, _quick_key: str, run_generation: int):
        """Inner handler that runs under the _running_agents sentinel guard."""
        _msg_start_time = time.time()
        _platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        _msg_preview = (event.text or "")[:80].replace("\n", " ")
        logger.info(
            "inbound message: platform=%s user=%s chat=%s msg=%r",
            _platform_name, source.user_name or source.user_id or "unknown",
            source.chat_id or "unknown", _msg_preview,
        )

        # Get or create session
        # Topic-mode DMs: rewrite a stale/foreign thread_id to the user's
        # last-active topic so a cross-topic Reply or stripped plain reply
        # doesn't fragment the conversation across sessions.
        recovered = self._recover_telegram_topic_thread_id(source)
        if recovered is not None:
            logger.info(
                "telegram topic recovery: chat=%s user=%s %r -> %s",
                source.chat_id, source.user_id, source.thread_id, recovered,
            )
            source = dataclasses.replace(source, thread_id=recovered)
            try:
                event.source = source
            except Exception:
                pass

        session_entry = self.session_store.get_or_create_session(source)
        session_key = session_entry.session_key
        self._cache_session_source(session_key, source)
        if self._is_telegram_topic_lane(source):
            try:
                binding = self._session_db.get_telegram_topic_binding(
                    chat_id=str(source.chat_id),
                    thread_id=str(source.thread_id),
                ) if self._session_db else None
            except Exception:
                logger.debug("Failed to read Telegram topic binding", exc_info=True)
                binding = None
            if binding:
                bound_session_id = str(binding.get("session_id") or "")
                if bound_session_id and bound_session_id != session_entry.session_id:
                    # Route the override through SessionStore so the session_key
                    # → session_id mapping is persisted to disk and the previous
                    # lane session is ended cleanly. Mutating session_entry in
                    # place here created a split-brain state where the JSON
                    # index pointed at one id but code downstream used another.
                    switched = self.session_store.switch_session(session_key, bound_session_id)
                    if switched is not None:
                        session_entry = switched
            else:
                try:
                    self._record_telegram_topic_binding(source, session_entry)
                except Exception:
                    logger.debug("Failed to record Telegram topic binding", exc_info=True)
        if getattr(session_entry, "was_auto_reset", False):
            # Treat auto-reset as a full conversation boundary — drop every
            # session-scoped transient state so the fresh session does not
            # inherit the previous conversation's model/reasoning overrides
            # or a queued "/model switched" note.
            self._session_model_overrides.pop(session_key, None)
            self._set_session_reasoning_override(session_key, None)
            if hasattr(self, "_pending_model_notes"):
                self._pending_model_notes.pop(session_key, None)
        
        # Emit session:start for new or auto-reset sessions
        _is_new_session = (
            session_entry.created_at == session_entry.updated_at
            or getattr(session_entry, "was_auto_reset", False)
            or getattr(session_entry, "is_fresh_reset", False)
        )
        # Consume the is_fresh_reset flag immediately so it doesn't leak
        # onto subsequent messages in the same session (issue #6508).
        if getattr(session_entry, "is_fresh_reset", False):
            session_entry.is_fresh_reset = False
        if _is_new_session:
            await self.hooks.emit("session:start", {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "session_id": session_entry.session_id,
                "session_key": session_key,
            })
        
        # Build session context
        context = build_session_context(source, self.config, session_entry)
        
        # Set session context variables for tools (task-local, concurrency-safe)
        _session_env_tokens = self._set_session_env(context)
        
        # Read privacy.redact_pii from config (re-read per message)
        _redact_pii = False
        try:
            _pcfg = _get_run_attr("_load_gateway_config")()
            _redact_pii = bool((_pcfg.get("privacy") or {}).get("redact_pii", False))
        except Exception:
            pass

        # Build the context prompt to inject
        context_prompt = build_session_context_prompt(context, redact_pii=_redact_pii)
        
        # If the previous session expired and was auto-reset, prepend a notice
        # so the agent knows this is a fresh conversation (not an intentional /reset).
        if getattr(session_entry, 'was_auto_reset', False):
            reset_reason = getattr(session_entry, 'auto_reset_reason', None) or 'idle'
            if reset_reason == "suspended":
                context_note = "[System note: The user's previous session was stopped and suspended. This is a fresh conversation with no prior context.]"
            elif reset_reason == "daily":
                context_note = "[System note: The user's session was automatically reset by the daily schedule. This is a fresh conversation with no prior context.]"
            else:
                context_note = "[System note: The user's previous session expired due to inactivity. This is a fresh conversation with no prior context.]"
            context_prompt = context_note + "\n\n" + context_prompt

            # Send a user-facing notification explaining the reset, unless:
            # - notifications are disabled in config
            # - the platform is excluded (e.g. api_server, webhook)
            # - the expired session had no activity (nothing was cleared)
            try:
                policy = self.session_store.config.get_reset_policy(
                    platform=source.platform,
                    session_type=getattr(source, 'chat_type', 'dm'),
                )
                platform_name = source.platform.value if source.platform else ""
                had_activity = getattr(session_entry, 'reset_had_activity', False)
                # Suspended sessions always notify (they were explicitly stopped
                # or crashed mid-operation) — skip the policy check.
                should_notify = reset_reason == "suspended" or (
                    policy.notify
                    and had_activity
                    and platform_name not in policy.notify_exclude_platforms
                )
                if should_notify:
                    adapter = self.adapters.get(source.platform)
                    if adapter:
                        if reset_reason == "suspended":
                            reason_text = "previous session was stopped or interrupted"
                        elif reset_reason == "daily":
                            reason_text = f"daily schedule at {policy.at_hour}:00"
                        else:
                            hours = policy.idle_minutes // 60
                            mins = policy.idle_minutes % 60
                            duration = f"{hours}h" if not mins else f"{hours}h {mins}m" if hours else f"{mins}m"
                            reason_text = f"inactive for {duration}"
                        notice = (
                            f"◐ Session automatically reset ({reason_text}). "
                            f"Conversation history cleared.\n"
                            f"Use /resume to browse and restore a previous session.\n"
                            f"Adjust reset timing in config.yaml under session_reset."
                        )
                        try:
                            session_info = self._format_session_info()
                            if session_info:
                                notice = f"{notice}\n\n{session_info}"
                        except Exception:
                            pass
                        await adapter.send(
                            source.chat_id, notice,
                            metadata=self._thread_metadata_for_source(source),
                        )
            except Exception as e:
                logger.debug("Auto-reset notification failed (non-fatal): %s", e)

            session_entry.was_auto_reset = False
            session_entry.auto_reset_reason = None

        # Auto-load skill(s) for topic/channel bindings (Telegram DM Topics,
        # Discord channel_skill_bindings).  Supports a single name or ordered list.
        # Only inject on NEW sessions — ongoing conversations already have the
        # skill content in their conversation history from the first message.
        _auto = getattr(event, "auto_skill", None)
        if _is_new_session and _auto:
            _skill_names = [_auto] if isinstance(_auto, str) else list(_auto)
            try:
                from agent.skill_commands import _load_skill_payload, _build_skill_message
                _combined_parts: list[str] = []
                _loaded_names: list[str] = []
                for _sname in _skill_names:
                    _loaded = _load_skill_payload(_sname, task_id=_quick_key)
                    if _loaded:
                        _loaded_skill, _skill_dir, _display_name = _loaded
                        _note = (
                            f'[IMPORTANT: The "{_display_name}" skill is auto-loaded. '
                            f"Follow its instructions for this session.]"
                        )
                        _part = _build_skill_message(_loaded_skill, _skill_dir, _note)
                        if _part:
                            _combined_parts.append(_part)
                            _loaded_names.append(_sname)
                    else:
                        logger.warning("[Gateway] Auto-skill '%s' not found", _sname)
                if _combined_parts:
                    # Append the user's original text after all skill payloads
                    _combined_parts.append(event.text)
                    event.text = "\n\n".join(_combined_parts)
                    logger.info(
                        "[Gateway] Auto-loaded skill(s) %s for session %s",
                        _loaded_names, session_key,
                    )
            except Exception as e:
                logger.warning("[Gateway] Failed to auto-load skill(s) %s: %s", _skill_names, e)

        # Load conversation history from transcript
        history = self.session_store.load_transcript(session_entry.session_id)
        
        # -----------------------------------------------------------------
        # Session hygiene: auto-compress pathologically large transcripts
        #
        # Long-lived gateway sessions can accumulate enough history that
        # every new message rehydrates an oversized transcript, causing
        # repeated truncation/context failures.  Detect this early and
        # compress proactively — before the agent even starts.  (#628)
        #
        # Token source priority:
        # 1. Actual API-reported prompt_tokens from the last turn
        #    (stored in session_entry.last_prompt_tokens)
        # 2. Rough char-based estimate (str(msg)//4). Overestimates
        #    by 30-50% on code/JSON-heavy sessions, but that just
        #    means hygiene fires a bit early — safe and harmless.
        # -----------------------------------------------------------------
        if history and len(history) >= 4:
            from agent.model_metadata import (
                estimate_messages_tokens_rough,
                get_model_context_length,
            )

            # Read model + compression config from config.yaml.
            # NOTE: hygiene threshold is intentionally HIGHER than the agent's
            # own compressor (0.85 vs 0.50).  Hygiene is a safety net for
            # sessions that grew too large between turns — it fires pre-agent
            # to prevent API failures.  The agent's own compressor handles
            # normal context management during its tool loop with accurate
            # real token counts.  Having hygiene at 0.50 caused premature
            # compression on every turn in long gateway sessions.
            _hyg_model = "anthropic/claude-sonnet-4.6"
            _hyg_threshold_pct = 0.85
            _hyg_compression_enabled = True
            _hyg_hard_msg_limit = 400
            _hyg_config_context_length = None
            _hyg_provider = None
            _hyg_base_url = None
            _hyg_api_key = None
            _hyg_data = {}
            try:
                _hyg_data = _get_run_attr("_load_gateway_config")()
                if _hyg_data:
                    # Resolve model name (same logic as run_sync)
                    _model_cfg = _hyg_data.get("model", {})
                    if isinstance(_model_cfg, str):
                        _hyg_model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        _hyg_model = _model_cfg.get("default") or _model_cfg.get("model") or _hyg_model
                        # Read explicit context_length override from model config
                        # (same as run_agent.py lines 995-1005)
                        _raw_ctx = _model_cfg.get("context_length")
                        if _raw_ctx is not None:
                            try:
                                _hyg_config_context_length = int(_raw_ctx)
                            except (TypeError, ValueError):
                                pass
                        # Read provider for accurate context detection
                        _hyg_provider = _model_cfg.get("provider") or None
                        _hyg_base_url = _model_cfg.get("base_url") or None

                    # Read compression settings — only use enabled flag.
                    # The threshold is intentionally separate from the agent's
                    # compression.threshold (hygiene runs higher).
                    _comp_cfg = _hyg_data.get("compression", {})
                    if isinstance(_comp_cfg, dict):
                        _hyg_compression_enabled = str(
                            _comp_cfg.get("enabled", True)
                        ).lower() in {"true", "1", "yes"}
                        _raw_hard_limit = _comp_cfg.get("hygiene_hard_message_limit")
                        if _raw_hard_limit is not None:
                            try:
                                _parsed = int(_raw_hard_limit)
                                if _parsed > 0:
                                    _hyg_hard_msg_limit = _parsed
                            except (TypeError, ValueError):
                                pass

                try:
                    _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(
                        source=source,
                        session_key=session_key,
                        user_config=_hyg_data if isinstance(_hyg_data, dict) else None,
                    )
                    _hyg_provider = _hyg_runtime.get("provider") or _hyg_provider
                    _hyg_base_url = _hyg_runtime.get("base_url") or _hyg_base_url
                    _hyg_api_key = _hyg_runtime.get("api_key") or _hyg_api_key
                except Exception:
                    pass

                # Check custom_providers per-model context_length
                # (same fallback as run_agent.py lines 1171-1189).
                # Must run after runtime resolution so _hyg_base_url is set.
                if _hyg_config_context_length is None and _hyg_base_url:
                    try:
                        try:
                            from vermes_cli.config import get_compatible_custom_providers as _gw_gcp
                            _hyg_custom_providers = _gw_gcp(_hyg_data)
                        except Exception:
                            _hyg_custom_providers = _hyg_data.get("custom_providers")
                            if not isinstance(_hyg_custom_providers, list):
                                _hyg_custom_providers = []
                        for _cp in _hyg_custom_providers:
                            if not isinstance(_cp, dict):
                                continue
                            _cp_url = (_cp.get("base_url") or "").rstrip("/")
                            if _cp_url and _cp_url == _hyg_base_url.rstrip("/"):
                                _cp_models = _cp.get("models", {})
                                if isinstance(_cp_models, dict):
                                    _cp_model_cfg = _cp_models.get(_hyg_model, {})
                                    if isinstance(_cp_model_cfg, dict):
                                        _cp_ctx = _cp_model_cfg.get("context_length")
                                        if _cp_ctx is not None:
                                            _hyg_config_context_length = int(_cp_ctx)
                                break
                    except (TypeError, ValueError):
                        pass
            except Exception:
                pass

            if _hyg_compression_enabled:
                _hyg_context_length = get_model_context_length(
                    _hyg_model,
                    base_url=_hyg_base_url or "",
                    api_key=_hyg_api_key or "",
                    config_context_length=_hyg_config_context_length,
                    provider=_hyg_provider or "",
                )
                _compress_token_threshold = int(
                    _hyg_context_length * _hyg_threshold_pct
                )
                _warn_token_threshold = int(_hyg_context_length * 0.95)

                _msg_count = len(history)

                # Prefer actual API-reported tokens from the last turn
                # (stored in session entry) over the rough char-based estimate.
                _stored_tokens = session_entry.last_prompt_tokens
                if _stored_tokens > 0:
                    _approx_tokens = _stored_tokens
                    _token_source = "actual"
                else:
                    _approx_tokens = estimate_messages_tokens_rough(history)
                    _token_source = "estimated"
                    # Note: rough estimates overestimate by 30-50% for code/JSON-heavy
                    # sessions, but that just means hygiene fires a bit early — which
                    # is safe and harmless.  The 85% threshold already provides ample
                    # headroom (agent's own compressor runs at 50%).  A previous 1.4x
                    # multiplier tried to compensate by inflating the threshold, but
                    # 85% * 1.4 = 119% of context — which exceeds the model's limit
                    # and prevented hygiene from ever firing for ~200K models (GLM-5).

                # Hard safety valve: force compression if message count is
                # extreme, regardless of token estimates.  This breaks the
                # death spiral where API disconnects prevent token data
                # collection, which prevents compression, which causes more
                # disconnects.  400 messages is well above normal sessions
                # but catches runaway growth before it becomes unrecoverable.
                # Threshold is configurable via
                # compression.hygiene_hard_message_limit.
                # (#2153)
                _HARD_MSG_LIMIT = _hyg_hard_msg_limit
                _needs_compress = (
                    _approx_tokens >= _compress_token_threshold
                    or _msg_count >= _HARD_MSG_LIMIT
                )

                if _needs_compress:
                    logger.info(
                        "Session hygiene: %s messages, ~%s tokens (%s) — auto-compressing "
                        "(threshold: %s%% of %s = %s tokens)",
                        _msg_count, f"{_approx_tokens:,}", _token_source,
                        int(_hyg_threshold_pct * 100),
                        f"{_hyg_context_length:,}",
                        f"{_compress_token_threshold:,}",
                    )

                    _hyg_meta = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))

                    try:
                        from run_agent import AIAgent

                        _hyg_model, _hyg_runtime = self._resolve_session_agent_runtime(
                            source=source,
                            session_key=session_key,
                            user_config=_hyg_data if isinstance(_hyg_data, dict) else None,
                        )
                        if _hyg_runtime.get("api_key"):
                            _hyg_msgs = [
                                {"role": m.get("role"), "content": m.get("content")}
                                for m in history
                                if m.get("role") in {"user", "assistant"}
                                and m.get("content")
                            ]

                            if len(_hyg_msgs) >= 4:
                                _hyg_agent = AIAgent(
                                    **_hyg_runtime,
                                    model=_hyg_model,
                                    max_iterations=4,
                                    quiet_mode=True,
                                    skip_memory=True,
                                    enabled_toolsets=["memory"],
                                    session_id=session_entry.session_id,
                                )
                                try:
                                    # The hygiene agent rotates the session
                                    # forward to a continuation id that becomes
                                    # the gateway session's live row. It must
                                    # never finalize on close() (today it has no
                                    # session_db so close() no-ops, but this
                                    # guards a future where one is wired in).
                                    _hyg_agent._end_session_on_close = False
                                    _hyg_agent._print_fn = lambda *a, **kw: None

                                    loop = asyncio.get_running_loop()
                                    _compressed, _ = await loop.run_in_executor(
                                        None,
                                        lambda: _hyg_agent._compress_context(
                                            _hyg_msgs, "",
                                            approx_tokens=_approx_tokens,
                                        ),
                                    )

                                    # _compress_context ends the old session and creates
                                    # a new session_id.  Write compressed messages into
                                    # the NEW session so the old transcript stays intact
                                    # and searchable via session_search.
                                    _hyg_new_sid = _hyg_agent.session_id
                                    _hyg_rotated = _hyg_new_sid != session_entry.session_id
                                    _hyg_in_place = bool(
                                        getattr(_hyg_agent, "compression_in_place", False)
                                    )
                                    if _hyg_rotated:
                                        session_entry.session_id = _hyg_new_sid
                                        self.session_store._save()

                                    # Only rewrite the transcript when rotation produced
                                    # a NEW session id OR in-place compaction succeeded.
                                    # The danger this guards against (mirrors the
                                    # /compress fix #44794/#39704): the hygiene agent is
                                    # built WITHOUT a session_db, so _compress_context
                                    # cannot rotate — if it also wasn't in-place, the
                                    # session_id is unchanged for a FAILURE reason, and an
                                    # unconditional rewrite_transcript() would DELETE the
                                    # original messages and replace them with only the
                                    # compressed summary (permanent data loss, #21301).
                                    if _hyg_rotated or _hyg_in_place:
                                        self.session_store.rewrite_transcript(
                                            session_entry.session_id, _compressed
                                        )
                                        # Reset stored token count — transcript rewritten
                                        session_entry.last_prompt_tokens = 0
                                        history = _compressed
                                        _new_count = len(_compressed)
                                        _new_tokens = estimate_messages_tokens_rough(
                                            _compressed
                                        )
                                    else:
                                        # No rewrite happened — transcript preserved
                                        # unchanged, so the post-compression counts equal
                                        # the pre-compression ones.
                                        _new_count = _msg_count
                                        _new_tokens = _approx_tokens
                                        logger.warning(
                                            "Gateway hygiene compression for session %s "
                                            "did not rotate or compact in place "
                                            "(no session_db on the hygiene agent) — "
                                            "preserving the original transcript instead "
                                            "of overwriting it with the summary (#21301).",
                                            session_entry.session_id,
                                        )

                                    logger.info(
                                        "Session hygiene: compressed %s → %s msgs, "
                                        "~%s → ~%s tokens",
                                        _msg_count, _new_count,
                                        f"{_approx_tokens:,}", f"{_new_tokens:,}",
                                    )

                                    if _new_tokens >= _warn_token_threshold:
                                        logger.warning(
                                            "Session hygiene: still ~%s tokens after "
                                            "compression",
                                            f"{_new_tokens:,}",
                                        )

                                    # If summary generation failed, the
                                    # compressor aborts entirely and returns
                                    # messages unchanged — nothing is dropped.
                                    # Surface a visible warning to the gateway
                                    # user — agent.log alone is invisible on
                                    # TG/Discord/etc. — so they know the chat
                                    # is "frozen" at the current size and can
                                    # /compress to retry or /reset to start
                                    # fresh.
                                    _comp = getattr(_hyg_agent, "context_compressor", None)
                                    if _comp is not None and getattr(_comp, "_last_compress_aborted", False):
                                        _err = getattr(_comp, "_last_summary_error", None) or "unknown error"
                                        _warn_msg = (
                                            "⚠️ Context compression aborted "
                                            f"({_err}). No messages were dropped — "
                                            "conversation is unchanged. Run /compress "
                                            "to retry, /reset for a clean session, or "
                                            "check your auxiliary.compression model "
                                            "configuration."
                                        )
                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _warn_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
                                            logger.warning(
                                                "Failed to deliver compression-failure warning to user: %s",
                                                _werr,
                                            )
                                    # Separately: if the user's CONFIGURED aux
                                    # model failed and we recovered by falling
                                    # back to the main model, tell them — a
                                    # misconfigured auxiliary.compression.model
                                    # is something only they can fix, and
                                    # silent recovery would hide it.
                                    elif _comp is not None and getattr(_comp, "_last_aux_model_failure_model", None):
                                        _aux_model = getattr(_comp, "_last_aux_model_failure_model", "")
                                        _aux_err = getattr(_comp, "_last_aux_model_failure_error", None) or "unknown error"
                                        _aux_msg = (
                                            f"ℹ️ Configured compression model `{_aux_model}` "
                                            f"failed ({_aux_err}). Recovered using your main "
                                            "model — context is intact — but you may want to "
                                            "check `auxiliary.compression.model` in config.yaml."
                                        )
                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _aux_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
                                            logger.warning(
                                                "Failed to deliver aux-model-fallback notice to user: %s",
                                                _werr,
                                            )
                                finally:
                                    # Evict the cached agent so the next turn
                                    # rebuilds its system prompt from current
                                    # SOUL.md, memory, and skills.
                                    self._evict_cached_agent(session_key)
                                    self._cleanup_agent_resources(_hyg_agent)

                    except Exception as e:
                        logger.warning(
                            "Session hygiene auto-compress failed: %s", e
                        )

        # First-message onboarding -- only on the very first interaction ever
        if not history and not self.session_store.has_any_sessions():
            context_prompt += (
                "\n\n[System note: This is the user's very first message ever. "
                "Briefly introduce yourself and mention that /help shows available commands. "
                "Keep the introduction concise -- one or two sentences max.]"
            )
        
        # One-time prompt if no home channel is set for this platform
        # Skip for webhooks - they deliver directly to configured targets (github_comment, etc.)
        if not history and source.platform and source.platform != Platform.LOCAL and source.platform != Platform.WEBHOOK:
            platform_name = source.platform.value
            env_key = _home_target_env_var(platform_name)
            if not os.getenv(env_key):
                # Slack dispatches all Hermes commands through a single
                # parent slash command `/hermes`; bare `/sethome` is not
                # registered and would fail with "app did not respond".
                sethome_cmd = (
                    "/hermes sethome"
                    if source.platform == Platform.SLACK
                    else "/sethome"
                )
                notice = (
                    f"📬 No home channel is set for {platform_name.title()}. "
                    f"A home channel is where Hermes delivers cron job results "
                    f"and cross-platform messages.\n\n"
                    f"Type {sethome_cmd} to make this chat your home channel, "
                    f"or ignore to skip."
                )
                await self._deliver_platform_notice(source, notice)
        
        # -----------------------------------------------------------------
        # Voice channel awareness — inject current voice channel state
        # into context so the agent knows who is in the channel and who
        # is speaking, without needing a separate tool call.
        # -----------------------------------------------------------------
        if source.platform == Platform.DISCORD:
            adapter = self.adapters.get(Platform.DISCORD)
            guild_id = self._get_guild_id(event)
            if guild_id and adapter and hasattr(adapter, "get_voice_channel_context"):
                vc_context = adapter.get_voice_channel_context(guild_id)
                if vc_context:
                    context_prompt += f"\n\n{vc_context}"

        # -----------------------------------------------------------------
        # Auto-analyze images sent by the user
        #
        # If the user attached image(s), we run the vision tool eagerly so
        # the conversation model always receives a text description.  The
        # local file path is also included so the model can re-examine the
        # image later with a more targeted question via vision_analyze.
        #
        # We filter to image paths only (by media_type) so that non-image
        # attachments (documents, audio, etc.) are not sent to the vision
        # tool even when they appear in the same message.
        # -----------------------------------------------------------------
        message_text = await self._prepare_inbound_message_text(
            event=event,
            source=source,
            history=history,
        )
        if message_text is None:
            return

        # Bind this gateway run generation to the adapter's active-session
        # event so deferred post-delivery callbacks can be released by the
        # same run that registered them.
        self._bind_adapter_run_generation(
            self.adapters.get(source.platform),
            session_key,
            run_generation,
        )

        try:
            # Emit agent:start hook
            hook_ctx = {
                "platform": source.platform.value if source.platform else "",
                "user_id": source.user_id,
                "chat_id": source.chat_id or "",
                "session_id": session_entry.session_id,
                "message": message_text[:500],
            }
            await self.hooks.emit("agent:start", hook_ctx)

            # Run the agent
            agent_result = await self._run_agent(
                message=message_text,
                context_prompt=context_prompt,
                history=history,
                source=source,
                session_id=session_entry.session_id,
                session_key=session_key,
                run_generation=run_generation,
                event_message_id=self._reply_anchor_for_event(event),
                channel_prompt=event.channel_prompt,
            )

            # Stop persistent typing indicator now that the agent is done
            try:
                _typing_adapter = self.adapters.get(source.platform)
                if _typing_adapter and hasattr(_typing_adapter, "stop_typing"):
                    await _typing_adapter.stop_typing(source.chat_id)
            except Exception:
                pass

            if not self._is_session_run_current(_quick_key, run_generation):
                logger.info(
                    "Discarding stale agent result for %s — generation %d is no longer current",
                    _quick_key or "?",
                    run_generation,
                )
                _stale_adapter = self.adapters.get(source.platform)
                if getattr(type(_stale_adapter), "pop_post_delivery_callback", None) is not None:
                    _stale_adapter.pop_post_delivery_callback(
                        _quick_key,
                        generation=run_generation,
                    )
                elif _stale_adapter and hasattr(_stale_adapter, "_post_delivery_callbacks"):
                    _stale_adapter._post_delivery_callbacks.pop(_quick_key, None)
                return None

            response = agent_result.get("final_response") or ""

            # Convert the agent's internal "(empty)" sentinel into a
            # user-friendly message.  "(empty)" means the model failed to
            # produce visible content after exhausting all retries (nudge,
            # prefill, empty-retry, fallback).  Sending the raw sentinel
            # looks like a bug; a short explanation is more helpful.
            if response == "(empty)":
                response = (
                    "⚠️ The model returned no response after processing tool "
                    "results. This can happen with some models — try again or "
                    "rephrase your question."
                )
            agent_messages = agent_result.get("messages", [])
            _response_time = time.time() - _msg_start_time
            _api_calls = agent_result.get("api_calls", 0)
            _resp_len = len(response)
            logger.info(
                "response ready: platform=%s chat=%s time=%.1fs api_calls=%d response=%d chars",
                _platform_name, source.chat_id or "unknown",
                _response_time, _api_calls, _resp_len,
            )

            # Successful turn — clear any stuck-loop counter for this session.
            # This ensures the counter only accumulates across CONSECUTIVE
            # restarts where the session was active (never completed).
            #
            # Also clear the resume_pending flag (set by drain-timeout
            # shutdown) — the turn ran to completion, so recovery
            # succeeded and subsequent messages should no longer receive
            # the restart-interruption system note.
            if session_key and _get_run_attr("_should_clear_resume_pending_after_turn")(agent_result):
                self._clear_restart_failure_count(session_key)
                try:
                    self.session_store.clear_resume_pending(session_key)
                except Exception as _e:
                    logger.debug(
                        "clear_resume_pending failed for %s: %s",
                        session_key, _e,
                    )

            # Normalize empty responses: surface errors, partial failures, and
            # the case where agent did work but returned no text. Fix for #18765.
            response = _get_run_attr("_normalize_empty_agent_response")(
                agent_result, response, history_len=len(history),
            )
            response = _get_run_attr("_sanitize_gateway_final_response")(source.platform, response)

            # If the agent's session_id changed during compression, update
            # session_entry so transcript writes below go to the right session.
            if agent_result.get("session_id") and agent_result["session_id"] != session_entry.session_id:
                session_entry.session_id = agent_result["session_id"]

            # Prepend reasoning/thinking if display is enabled (per-platform)
            try:
                from gateway.display_config import resolve_display_setting as _rds
                _show_reasoning_effective = _rds(
                    _get_run_attr("_load_gateway_config")(),
                    _platform_config_key(source.platform),
                    "show_reasoning",
                    getattr(self, "_show_reasoning", False),
                )
            except Exception:
                _show_reasoning_effective = getattr(self, "_show_reasoning", False)
            if _show_reasoning_effective and response:
                last_reasoning = agent_result.get("last_reasoning")
                if last_reasoning:
                    # Collapse long reasoning to keep messages readable
                    lines = last_reasoning.strip().splitlines()
                    if len(lines) > 15:
                        display_reasoning = "\n".join(lines[:15])
                        display_reasoning += f"\n_... ({len(lines) - 15} more lines)_"
                    else:
                        display_reasoning = last_reasoning.strip()
                    response = f"💭 **Reasoning:**\n```\n{display_reasoning}\n```\n\n{response}"

            # Runtime-metadata footer — only on the FINAL message of the turn.
            # Off by default (display.runtime_footer.enabled=false).  When
            # streaming already delivered the body, we can't mutate the sent
            # text, so we fire a separate trailing send below.
            _footer_line = ""
            try:
                from gateway.runtime_footer import build_footer_line as _bfl
                _footer_line = _bfl(
                    user_config=_get_run_attr("_load_gateway_config")(),
                    platform_key=_platform_config_key(source.platform),
                    model=agent_result.get("model"),
                    context_tokens=agent_result.get("last_prompt_tokens", 0) or 0,
                    context_length=agent_result.get("context_length") or None,
                    cwd=os.environ.get("TERMINAL_CWD", ""),
                )
            except Exception as _footer_err:
                logger.debug("runtime_footer build failed: %s", _footer_err)
                _footer_line = ""
            if _footer_line and response and not agent_result.get("already_sent"):
                response = f"{response}\n\n{_footer_line}"

            # Emit agent:end hook
            await self.hooks.emit("agent:end", {
                **hook_ctx,
                "response": (response or "")[:500],
            })
            
            # Check for pending process watchers (check_interval on background processes)
            try:
                from tools.process_registry import process_registry
                while process_registry.pending_watchers:
                    watcher = process_registry.pending_watchers.pop(0)
                    asyncio.create_task(self._run_process_watcher(watcher))
            except Exception as e:
                logger.error("Process watcher setup error: %s", e)

            # Drain watch pattern notifications that arrived during the agent run.
            # Watch events and completions share the same queue; completions are
            # already handled by the per-process watcher task above, so we only
            # inject watch-type events here.
            try:
                from tools.process_registry import process_registry as _pr
                _watch_events = []
                while not _pr.completion_queue.empty():
                    evt = _pr.completion_queue.get_nowait()
                    evt_type = evt.get("type", "completion")
                    if evt_type in {"watch_match", "watch_disabled"}:
                        _watch_events.append(evt)
                    # else: completion events are handled by the watcher task
                for evt in _watch_events:
                    synth_text = _get_run_attr("_format_gateway_process_notification")(evt)
                    if synth_text:
                        try:
                            await self._inject_watch_notification(synth_text, evt)
                        except Exception as e2:
                            logger.error("Watch notification injection error: %s", e2)
            except Exception as e:
                logger.debug("Watch queue drain error: %s", e)

            # NOTE: Dangerous command approvals are now handled inline by the
            # blocking gateway approval mechanism in tools/approval.py.  The agent
            # thread blocks until the user responds with /approve or /deny, so by
            # the time we reach here the approval has already been resolved.  The
            # old post-loop pop_pending + approval_hint code was removed in favour
            # of the blocking approach that mirrors CLI's synchronous input().
            
            # Save the full conversation to the transcript, including tool calls.
            # This preserves the complete agent loop (tool_calls, tool results,
            # intermediate reasoning) so sessions can be resumed with full context
            # and transcripts are useful for debugging and training data.
            #
            # IMPORTANT: For context-overflow failures (compression exhausted,
            # generic 400 on large sessions) we must NOT persist the user's
            # message — doing so would grow the session further and cause the
            # same failure on the next attempt, an infinite loop. (#1630, #9893)
            #
            # Transient failures (429, timeout, connection error, provider 5xx)
            # are different: the session is not oversized, and silently dropping
            # the user message causes severe context loss on retry — the agent
            # forgets what was just asked.  Persist the user turn so the
            # conversation is preserved. (#7100)
            agent_failed_early = bool(agent_result.get("failed"))
            _err_str_for_classify = str(agent_result.get("error", "")).lower()
            # Use specific multi-word phrases (not bare "exceed" or "token")
            # to avoid false positives on transient errors like "rate limit
            # exceeded" or "invalid auth token". Matches run_agent.py's
            # own context-length classifier.
            is_context_overflow_failure = agent_failed_early and (
                bool(agent_result.get("compression_exhausted"))
                or any(p in _err_str_for_classify for p in (
                    "context length", "context size", "context window",
                    "maximum context", "token limit", "too many tokens",
                    "reduce the length", "exceeds the limit",
                    "request entity too large", "prompt is too long",
                    "payload too large", "input is too long",
                ))
                or ("400" in _err_str_for_classify and len(history) > 50)
            )
            if is_context_overflow_failure:
                logger.info(
                    "Skipping transcript persistence for context-overflow "
                    "failure in session %s to prevent session growth loop.",
                    session_entry.session_id,
                )
            elif agent_failed_early:
                logger.info(
                    "Transient agent failure in session %s — persisting user "
                    "message so conversation context is preserved on retry.",
                    session_entry.session_id,
                )

            # When compression is exhausted, the session is permanently too
            # large to process.  Auto-reset it so the next message starts
            # fresh instead of replaying the same oversized context in an
            # infinite fail loop.  (#9893)
            if agent_result.get("compression_exhausted") and session_entry and session_key:
                logger.info(
                    "Auto-resetting session %s after compression exhaustion.",
                    session_entry.session_id,
                )
                self.session_store.reset_session(session_key)
                self._evict_cached_agent(session_key)
                self._session_model_overrides.pop(session_key, None)
                self._set_session_reasoning_override(session_key, None)
                if hasattr(self, "_pending_model_notes"):
                    self._pending_model_notes.pop(session_key, None)
                response = (response or "") + (
                    "\n\n🔄 Session auto-reset — the conversation exceeded the "
                    "maximum context size and could not be compressed further. "
                    "Your next message will start a fresh session."
                )

            ts = datetime.now().isoformat()
            
            # If this is a fresh session (no history), write the full tool
            # definitions as the first entry so the transcript is self-describing
            # -- the same list of dicts sent as tools=[...] in the API request.
            if is_context_overflow_failure:
                pass  # Skip all transcript writes — don't grow a broken session
            elif not history:
                tool_defs = agent_result.get("tools", [])
                self.session_store.append_to_transcript(
                    session_entry.session_id,
                    {
                        "role": "session_meta",
                        "tools": tool_defs or [],
                        "model": _get_run_attr("_resolve_gateway_model")(),
                        "platform": source.platform.value if source.platform else "",
                        "timestamp": ts,
                    }
                )
            
            # Find only the NEW messages from this turn (skip history we loaded).
            # Use the filtered history length (history_offset) that was actually
            # passed to the agent, not len(history) which includes session_meta
            # entries that were stripped before the agent saw them.
            if is_context_overflow_failure:
                pass  # handled above — skip all transcript writes
            elif agent_failed_early:
                # Transient failure (429/timeout/5xx): persist only the user
                # message so the next message can load a transcript that
                # reflects what was said.  Skip the assistant error text since
                # it's a gateway-generated hint, not model output. (#7100)
                _user_entry = {"role": "user", "content": message_text, "timestamp": ts}
                if event.message_id:
                    _user_entry["message_id"] = str(event.message_id)
                self.session_store.append_to_transcript(
                    session_entry.session_id,
                    _user_entry,
                )
            else:
                history_len = agent_result.get("history_offset", len(history))
                new_messages = agent_messages[history_len:] if len(agent_messages) > history_len else []

                # If no new messages found (edge case), fall back to simple user/assistant
                if not new_messages:
                    _user_entry = {"role": "user", "content": message_text, "timestamp": ts}
                    if event.message_id:
                        _user_entry["message_id"] = str(event.message_id)
                    self.session_store.append_to_transcript(
                        session_entry.session_id,
                        _user_entry,
                    )
                    if response:
                        self.session_store.append_to_transcript(
                            session_entry.session_id,
                            {"role": "assistant", "content": response, "timestamp": ts}
                        )
                else:
                    # The agent already persisted these messages to SQLite via
                    # _flush_messages_to_session_db(), so skip the DB write here
                    # to prevent the duplicate-write bug (#860).  We still write
                    # to JSONL for backward compatibility and as a backup.
                    agent_persisted = self._session_db is not None
                    # Attach the inbound platform message_id to the first user
                    # entry written this turn so platform-level quote-resolution
                    # (e.g. Yuanbao QuoteContextMiddleware's transcript fallback)
                    # can find earlier @bot messages by their original message_id.
                    _user_msg_id_attached = False
                    for msg in new_messages:
                        # Skip system messages (they're rebuilt each run)
                        if msg.get("role") == "system":
                            continue
                        # Add timestamp to each message for debugging
                        entry = {**msg, "timestamp": ts}
                        if (
                            not _user_msg_id_attached
                            and msg.get("role") == "user"
                            and event.message_id
                            and "message_id" not in entry
                        ):
                            entry["message_id"] = str(event.message_id)
                            _user_msg_id_attached = True
                        self.session_store.append_to_transcript(
                            session_entry.session_id, entry,
                            skip_db=agent_persisted,
                        )
            
            # Token counts and model are now persisted by the agent directly.
            # Keep only last_prompt_tokens here for context-window tracking and
            # compression decisions.
            self.session_store.update_session(
                session_entry.session_key,
                last_prompt_tokens=agent_result.get("last_prompt_tokens", 0),
            )

            # Auto voice reply: send TTS audio before the text response
            _already_sent = bool(agent_result.get("already_sent"))
            if self._should_send_voice_reply(event, response, agent_messages, already_sent=_already_sent):
                await self._send_voice_reply(event, response)

            # If streaming already delivered the response, extract and
            # deliver any MEDIA: files before returning None.  Streaming
            # sends raw text chunks that include MEDIA: tags — the normal
            # post-processing in _process_message_background is skipped
            # when already_sent is True, so media files would never be
            # delivered without this.
            #
            # Never skip when the agent failed — the error message is new
            # content the user hasn't seen (streaming only sent earlier
            # partial output before the failure).  Without this guard,
            # users see the agent "stop responding without explanation."
            if agent_result.get("already_sent") and not agent_result.get("failed"):
                if response:
                    _media_adapter = self.adapters.get(source.platform)
                    if _media_adapter:
                        await self._deliver_media_from_response(
                            response, event, _media_adapter,
                        )
                # Streaming already delivered the body text, but the footer was
                # intentionally held back (see the `not already_sent` gate above).
                # Send it now as a small trailing message so Telegram/Discord/etc.
                # still surface the runtime metadata on the final reply.
                if _footer_line:
                    try:
                        _foot_adapter = self.adapters.get(source.platform)
                        if _foot_adapter:
                            await _foot_adapter.send(
                                source.chat_id,
                                _footer_line,
                                metadata=self._thread_metadata_for_source(source, self._reply_anchor_for_event(event)),
                            )
                    except Exception as _e:
                        logger.debug("trailing footer send failed: %s", _e)
                return None

            return response
            
        except Exception as e:
            # Stop typing indicator on error too
            try:
                _err_adapter = self.adapters.get(source.platform)
                if _err_adapter and hasattr(_err_adapter, "stop_typing"):
                    await _err_adapter.stop_typing(source.chat_id)
            except Exception:
                pass
            logger.exception("Agent error in session %s", session_key)
            error_type = type(e).__name__
            error_detail = str(e)[:300] if str(e) else "no details available"
            status_hint = ""
            status_code = getattr(e, "status_code", None)
            _hist_len = len(history) if 'history' in locals() else 0
            if status_code == 401:
                status_hint = " Check your API key or run `claude /login` to refresh OAuth credentials."
            elif status_code == 402:
                status_hint = " Your API balance or quota is exhausted. Check your provider dashboard."
            elif status_code == 429:
                # Check if this is a plan usage limit (resets on a schedule) vs a transient rate limit
                _err_body = getattr(e, "response", None)
                _err_json = {}
                try:
                    if _err_body is not None:
                        _err_json = _err_body.json().get("error", {})
                        if not isinstance(_err_json, dict):
                            _err_json = {}
                except Exception:
                    pass
                if _err_json.get("type") == "usage_limit_reached":
                    _resets_in = _err_json.get("resets_in_seconds")
                    if _resets_in and _resets_in > 0:
                        import math
                        _hours = math.ceil(_resets_in / 3600)
                        status_hint = f" Your plan's usage limit has been reached. It resets in ~{_hours}h."
                    else:
                        status_hint = " Your plan's usage limit has been reached. Please wait until it resets."
                else:
                    status_hint = " You are being rate-limited. Please wait a moment and try again."
            elif status_code == 529:
                status_hint = " The API is temporarily overloaded. Please try again shortly."
            elif status_code in {400, 500}:
                # 400 with a large session is context overflow.
                # 500 with a large session often means the payload is too large
                # for the API to process — treat it the same way.
                if _hist_len > 50:
                    return (
                        "⚠️ Session too large for the model's context window.\n"
                        "Use /compact to compress the conversation, or "
                        "/reset to start fresh."
                    )
                elif status_code == 400:
                    status_hint = " The request was rejected by the API."
            return (
                f"Sorry, I encountered an error ({error_type}).\n"
                f"{error_detail}\n"
                f"{status_hint}"
                "Try again or use /reset to start a fresh session."
            )
        finally:
            # Restore session context variables to their pre-handler state
            self._clear_session_env(_session_env_tokens)
