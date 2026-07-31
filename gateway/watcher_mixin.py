"""WatcherMixin — background watcher methods for GatewayRunner.

Extracted from gateway/run.py as part of Phase 1 mixin decomposition.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType


logger = logging.getLogger(__name__)


from gateway._run_attr import _get_run_attr  # W2: shared, was duplicated in 4 mixins


class WatcherMixin:
    """Background watcher methods extracted from GatewayRunner.

        Provides update notification, platform reconnect, handoff watching,
        and process output monitoring.
        """

    async def _handoff_watcher(self, interval: float = 2.0) -> None:
        """Background task that processes pending CLI→gateway session handoffs.

        Polls ``state.db`` for sessions in ``handoff_state='pending'`` and,
        for each one:

        1. Atomically claims it (pending → running).
        2. Resolves the destination platform's configured home channel.
        3. Re-binds the gateway's session_key for that home channel to the
           CLI's existing session_id via ``session_store.switch_session`` so
           the full role-aware transcript replays on the next agent turn.
        4. Forges a synthetic ``MessageEvent`` (``internal=True``) with a
           handoff-notice text and dispatches through the normal gateway
           message pipeline so the agent runs and replies on the platform.
        5. Marks the row ``completed`` (or ``failed`` with ``handoff_error``).

        The CLI process is poll-blocked on the row's terminal state and
        prints the result to the user.
        """
        # Initial delay so the gateway is fully connected to its platforms
        # before we try to dispatch handoffs through them.
        await asyncio.sleep(5)
        while self._running:
            try:
                if self._session_db is None:
                    await asyncio.sleep(interval)
                    continue
                pending = self._session_db.list_pending_handoffs()
                for row in pending:
                    await self._process_handoff_row(row)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Handoff watcher tick error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)

    async def _process_handoff_row(self, row: dict) -> None:
        """Process a single pending handoff/relay row.

        Extracted from the ``_handoff_watcher`` sleep-loop so the per-row
        state machine (and the P1-3 Outbound Intent Ledger wiring) can be
        exercised directly by integration tests without spinning the loop.

        The watcher loop now just iterates ``list_pending_handoffs()`` and
        ``await self._process_handoff_row(row)`` — behaviour is unchanged.
        """
        session_id = row.get("id")
        if not session_id:
            return
        # 桌面 relay 记录（relay_source='desktop'）与既有 CLI→渠道
        # handoff 语义分流，互不干扰。
        is_desktop_relay = row.get("relay_source") == "desktop"
        # P1-3: Outbound Intent Ledger 主键。legacy handoff 无此列→None，
        # 下方 update 调用会安全 no-op（返回 False）。
        delivery_id = row.get("relay_delivery_id")
        # 超时护栏：过期的 relay 直接标 failed，不消费
        if is_desktop_relay:
            expire_at = row.get("relay_expire_at") or 0
            if expire_at and time.time() > expire_at:
                if self._session_db.claim_handoff(session_id):
                    self._session_db.fail_handoff(
                        session_id, "relay expired before gateway pickup"
                    )
                    if delivery_id:
                        self._session_db.update_outbound_intent(
                            delivery_id, status="expired",
                            error="relay expired before gateway pickup",
                        )
                    self._session_db.clear_desktop_relay(session_id)
                return
        if not self._session_db.claim_handoff(session_id):
            # Another tick or another gateway already claimed it.
            return
        # P1-3: gateway 已 claim，即将投递——标 claimed（不再“黑盒 pending”）
        if delivery_id:
            self._session_db.update_outbound_intent(delivery_id, status="claimed")
        try:
            if is_desktop_relay:
                await self._process_desktop_relay(row)
            else:
                await self._process_handoff(row)
            self._session_db.complete_handoff(session_id)
            # P1-3: 投递管线跑完（消息已走 adapter.send 发回原渠道）→ sent
            if delivery_id:
                self._session_db.update_outbound_intent(delivery_id, status="sent")
        except Exception as exc:
            logger.warning(
                "%s for session %s failed: %s",
                "Desktop relay" if is_desktop_relay else "Handoff",
                session_id, exc, exc_info=True,
            )
            self._session_db.fail_handoff(session_id, str(exc))
            # P1-3: failed=适配器已 claim 但报错。不自动重试，
            # 防重复发送（对齐 OpenSquilla outbox unknown 语义）。
            if delivery_id:
                self._session_db.update_outbound_intent(
                    delivery_id, status="failed", error=str(exc)
                )
        finally:
            if is_desktop_relay:
                self._session_db.clear_desktop_relay(session_id)

    async def _process_desktop_relay(self, row: dict) -> None:
        """Relay a desktop-originated user message into a channel session.

        步骤 3（send-from-desktop 桥）：桌面控制台经 web 后端写入 pending relay
        信号，gateway 在此消费——还原该 session 的原渠道 SessionSource，构造
        internal MessageEvent 走正常 `_handle_message` 管线：agent 运行、回复经
        该渠道 adapter.send 发回原渠道用户、user/assistant 消息由管线自身落
        state.db（**本方法不直接 append_message，避免双写**）、记忆逐轮摄入。

        绝不经 web 进程 chat.py。任何失败抛异常由 watcher 标 failed。
        """
        session_id = row.get("id")
        relay_text = (row.get("relay_text") or "").strip()
        if not relay_text:
            raise RuntimeError("desktop relay has empty relay_text")
        if (row.get("source") or "").lower() == "web":
            # 端点已拒绝，双保险：web 会话不 relay（会绕回 web 进程形成环路）
            raise RuntimeError("refusing to relay web-source session")

        source = self._find_source_by_session_id(session_id)
        if source is None:
            raise RuntimeError(
                "session source not resolvable (channel not connected yet? "
                "have the channel send/receive one message first)"
            )

        adapter = self.adapters.get(source.platform)
        if adapter is None:
            raise RuntimeError(
                f"platform {getattr(source.platform, 'value', source.platform)} "
                "not connected — cannot deliver reply to the original channel"
            )

        event = MessageEvent(
            text=relay_text,
            message_type=MessageType.TEXT,
            source=source,
            internal=True,
        )
        logger.info(
            "Desktop relay: injecting message into %s chat=%s (session %s)",
            getattr(source.platform, "value", source.platform),
            source.chat_id,
            session_id,
        )
        await self._handle_message(event)

    async def _platform_reconnect_watcher(self) -> None:
        """Background task that periodically retries connecting failed platforms.

        Uses exponential backoff: 30s → 60s → 120s → 240s → 300s (cap).
        Retryable failures keep retrying at the backoff cap indefinitely
        — but if a platform fails ``_PAUSE_AFTER_FAILURES`` times in a row
        without ever succeeding, it is *paused*: kept in the retry queue
        but no longer hammered.  The user surfaces it with ``/platform list``
        and resumes it with ``/platform resume <name>``.  Non-retryable
        failures (bad auth, etc.) still drop out of the queue immediately.
        """
        _BACKOFF_CAP = 300  # 5 minutes max between retries
        _PAUSE_AFTER_FAILURES = 10  # circuit-breaker threshold

        await asyncio.sleep(10)  # initial delay — let startup finish
        while self._running:
            if not self._failed_platforms:
                # Nothing to reconnect — sleep and check again
                for _ in range(30):
                    if not self._running:
                        return
                    await asyncio.sleep(1)
                continue

            now = time.monotonic()
            for platform in list(self._failed_platforms.keys()):
                if not self._running:
                    return
                info = self._failed_platforms[platform]
                # Skip paused platforms entirely — they need explicit
                # /platform resume to come back.
                if info.get("paused"):
                    continue
                if now < info["next_retry"]:
                    continue  # not time yet

                platform_config = info["config"]
                attempt = info["attempts"] + 1
                logger.info(
                    "Reconnecting %s (attempt %d)...",
                    platform.value, attempt,
                )

                result = await self._connect_one(platform, platform_config)
                if result["ok"]:
                    logger.info("✓ %s reconnected successfully", platform.value)
                else:
                    err_msg = result.get("error", "unknown")
                    # Check if non-retryable
                    adapter = self.adapters.get(platform)
                    if adapter and adapter.has_fatal_error and not adapter.fatal_error_retryable:
                        logger.warning(
                            "Reconnect %s: non-retryable error (%s), removing from retry queue",
                            platform.value, err_msg,
                        )
                        del self._failed_platforms[platform]
                    else:
                        backoff = min(30 * (2 ** (attempt - 1)), _BACKOFF_CAP)
                        info["attempts"] = attempt
                        info["next_retry"] = time.monotonic() + backoff
                        logger.info(
                            "Reconnect %s failed, next retry in %ds",
                            platform.value, backoff,
                        )
                        if attempt >= _PAUSE_AFTER_FAILURES:
                            self._pause_failed_platform(platform, reason=err_msg)

            # Check every 10 seconds for platforms that need reconnection
            for _ in range(10):
                if not self._running:
                    return
                await asyncio.sleep(1)

    def _schedule_update_notification_watch(self) -> None:
        """Ensure a background task is watching for update completion."""
        existing_task = getattr(self, "_update_notification_task", None)
        if existing_task and not existing_task.done():
            return

        try:
            self._update_notification_task = asyncio.create_task(
                self._watch_update_progress()
            )
        except RuntimeError:
            logger.debug("Skipping update notification watcher: no running event loop")

    async def _watch_update_progress(
        self,
        poll_interval: float = 2.0,
        stream_interval: float = 4.0,
        timeout: float = 1800.0,
    ) -> None:
        """Watch ``vermes update --gateway``, streaming output + forwarding prompts.

        Polls ``.update_output.txt`` for new content and sends chunks to the
        user periodically.  Detects ``.update_prompt.json`` (written by the
        update process when it needs user input) and forwards the prompt to
        the messenger.  The user's next message is intercepted by
        ``_handle_message`` and written to ``.update_response``.
        """
        pending_path = _get_run_attr("_vermes_home") / ".update_pending.json"
        claimed_path = _get_run_attr("_vermes_home") / ".update_pending.claimed.json"
        output_path = _get_run_attr("_vermes_home") / ".update_output.txt"
        exit_code_path = _get_run_attr("_vermes_home") / ".update_exit_code"
        prompt_path = _get_run_attr("_vermes_home") / ".update_prompt.json"

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        # Resolve the adapter and chat_id for sending messages
        adapter = None
        chat_id = None
        session_key = None
        metadata = None
        for path in (claimed_path, pending_path):
            if path.exists():
                try:
                    pending = json.loads(path.read_text())
                    platform_str = pending.get("platform")
                    chat_id = pending.get("chat_id")
                    session_key = pending.get("session_key")
                    thread_id = pending.get("thread_id")
                    metadata = {"thread_id": thread_id} if thread_id else None
                    if platform_str and chat_id:
                        platform = Platform(platform_str)
                        adapter = self.adapters.get(platform)
                        # Fallback session key if not stored (old pending files)
                        if not session_key:
                            session_key = f"{platform_str}:{chat_id}"
                    break
                except Exception:
                    pass

        if not adapter or not chat_id:
            logger.warning("Update watcher: cannot resolve adapter/chat_id, falling back to completion-only")
            # Fall back to old behavior: wait for exit code and send final notification
            while (pending_path.exists() or claimed_path.exists()) and loop.time() < deadline:
                if exit_code_path.exists():
                    await self._send_update_notification()
                    return
                await asyncio.sleep(poll_interval)
            if (pending_path.exists() or claimed_path.exists()) and not exit_code_path.exists():
                exit_code_path.write_text("124")
                await self._send_update_notification()
            return

        def _strip_ansi(text: str) -> str:
            return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)

        bytes_sent = 0
        last_stream_time = loop.time()
        buffer = ""

        async def _flush_buffer() -> None:
            """Send buffered output to the user."""
            nonlocal buffer, last_stream_time
            if not buffer.strip():
                buffer = ""
                return
            # Chunk to fit message limits (Telegram: 4096, others: generous)
            clean = _strip_ansi(buffer).strip()
            buffer = ""
            last_stream_time = loop.time()
            if not clean:
                return
            # Split into chunks if too long
            max_chunk = 3500
            chunks = [clean[i:i + max_chunk] for i in range(0, len(clean), max_chunk)]
            for chunk in chunks:
                try:
                    await adapter.send(chat_id, f"```\n{chunk}\n```", metadata=metadata)
                except Exception as e:
                    logger.debug("Update stream send failed: %s", e)

        while loop.time() < deadline:
            # Check for completion
            if exit_code_path.exists():
                # Read any remaining output
                if output_path.exists():
                    try:
                        content = output_path.read_text()
                        if len(content) > bytes_sent:
                            buffer += content[bytes_sent:]
                            bytes_sent = len(content)
                    except OSError:
                        pass
                await _flush_buffer()

                # Send final status
                try:
                    exit_code_raw = exit_code_path.read_text().strip() or "1"
                    exit_code = int(exit_code_raw)
                    if exit_code == 0:
                        await adapter.send(chat_id, "✅ Vermes update finished.", metadata=metadata)
                    else:
                        await adapter.send(
                            chat_id,
                            "❌ Vermes update failed (exit code {}).".format(exit_code),
                            metadata=metadata,
                        )
                    logger.info("Update finished (exit=%s), notified %s", exit_code, session_key)
                except Exception as e:
                    logger.warning("Update final notification failed: %s", e)

                # Cleanup
                for p in (pending_path, claimed_path, output_path,
                          exit_code_path, prompt_path):
                    p.unlink(missing_ok=True)
                (_get_run_attr("_vermes_home") / ".update_response").unlink(missing_ok=True)
                self._update_prompt_pending.pop(session_key, None)
                return

            # Check for new output
            if output_path.exists():
                try:
                    content = output_path.read_text()
                    if len(content) > bytes_sent:
                        buffer += content[bytes_sent:]
                        bytes_sent = len(content)
                except OSError:
                    pass

            # Flush buffer periodically
            if buffer.strip() and (loop.time() - last_stream_time) >= stream_interval:
                await _flush_buffer()

            # Check for prompts — only forward if we haven't already sent
            # one that's still awaiting a response.  Without this guard the
            # watcher would re-read the same .update_prompt.json every poll
            # cycle and spam the user with duplicate prompt messages.
            if (prompt_path.exists() and session_key
                    and not self._update_prompt_pending.get(session_key)):
                try:
                    prompt_data = json.loads(prompt_path.read_text())
                    prompt_text = prompt_data.get("prompt", "")
                    default = prompt_data.get("default", "")
                    if prompt_text:
                        # Flush any buffered output first so the user sees
                        # context before the prompt
                        await _flush_buffer()
                        # Try platform-native buttons first (Discord, Telegram)
                        sent_buttons = False
                        if getattr(type(adapter), "send_update_prompt", None) is not None:
                            try:
                                await adapter.send_update_prompt(
                                    chat_id=chat_id,
                                    prompt=prompt_text,
                                    default=default,
                                    session_key=session_key,
                                    metadata=metadata,
                                )
                                sent_buttons = True
                            except Exception as btn_err:
                                logger.debug("Button-based update prompt failed: %s", btn_err)
                        if not sent_buttons:
                            default_hint = f" (default: {default})" if default else ""
                            await adapter.send(
                                chat_id,
                                f"⚕ **Update needs your input:**\n\n"
                                f"{prompt_text}{default_hint}\n\n"
                                f"Reply `/approve` (yes) or `/deny` (no), "
                                f"or type your answer directly.",
                                metadata=metadata,
                            )
                        # Keep the prompt marker on disk until the user
                        # answers. If the gateway restarts mid-prompt, the
                        # next watcher can recover by re-forwarding it from
                        # disk. Duplicate sends in the same process are
                        # still suppressed by _update_prompt_pending.
                        self._update_prompt_pending[session_key] = True
                        # .update_response to continue — it doesn't re-check
                        logger.info("Forwarded update prompt to %s: %s", session_key, prompt_text[:80])
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug("Failed to read update prompt: %s", e)

            await asyncio.sleep(poll_interval)

        # Timeout
        if not exit_code_path.exists():
            logger.warning("Update watcher timed out after %.0fs", timeout)
            exit_code_path.write_text("124")
            await _flush_buffer()
            try:
                await adapter.send(
                    chat_id,
                    "❌ Vermes update timed out after 30 minutes.",
                    metadata=metadata,
                )
            except Exception:
                pass
            for p in (pending_path, claimed_path, output_path,
                      exit_code_path, prompt_path):
                p.unlink(missing_ok=True)
            (_get_run_attr("_vermes_home") / ".update_response").unlink(missing_ok=True)
            self._update_prompt_pending.pop(session_key, None)

    async def _send_update_notification(self) -> bool:
        """If an update finished, notify the user.

        Returns False when the update is still running so a caller can retry
        later. Returns True after a definitive send/skip decision.

        This is the legacy notification path used when the streaming watcher
        cannot resolve the adapter (e.g. after a gateway restart where the
        platform hasn't reconnected yet).
        """
        pending_path = _get_run_attr("_vermes_home") / ".update_pending.json"
        claimed_path = _get_run_attr("_vermes_home") / ".update_pending.claimed.json"
        output_path = _get_run_attr("_vermes_home") / ".update_output.txt"
        exit_code_path = _get_run_attr("_vermes_home") / ".update_exit_code"

        if not pending_path.exists() and not claimed_path.exists():
            return False

        cleanup = True
        active_pending_path = claimed_path
        try:
            if pending_path.exists():
                try:
                    pending_path.replace(claimed_path)
                except FileNotFoundError:
                    if not claimed_path.exists():
                        return True
            elif not claimed_path.exists():
                return True

            pending = json.loads(claimed_path.read_text())
            platform_str = pending.get("platform")
            chat_id = pending.get("chat_id")
            thread_id = pending.get("thread_id")

            if not exit_code_path.exists():
                logger.info("Update notification deferred: update still running")
                cleanup = False
                active_pending_path = pending_path
                claimed_path.replace(pending_path)
                return False

            exit_code_raw = exit_code_path.read_text().strip() or "1"
            exit_code = int(exit_code_raw)

            # Read the captured update output
            output = ""
            if output_path.exists():
                output = output_path.read_text()

            # Resolve adapter
            platform = Platform(platform_str)
            adapter = self.adapters.get(platform)

            if adapter and chat_id:
                metadata = {"thread_id": thread_id} if thread_id else None
                # Strip ANSI escape codes for clean display
                output = re.sub(r'\x1b\[[0-9;]*m', '', output).strip()
                if output:
                    if len(output) > 3500:
                        output = "…" + output[-3500:]
                    if exit_code == 0:
                        msg = f"✅ Vermes update finished.\n\n```\n{output}\n```"
                    else:
                        msg = f"❌ Vermes update failed.\n\n```\n{output}\n```"
                elif exit_code == 0:
                    msg = "✅ Vermes update finished successfully."
                else:
                    msg = "❌ Vermes update failed. Check the gateway logs or run `vermes update` manually for details."
                await adapter.send(chat_id, msg, metadata=metadata)
                logger.info(
                    "Sent post-update notification to %s:%s (exit=%s)",
                    platform_str,
                    chat_id,
                    exit_code,
                )
        except Exception as e:
            logger.warning("Post-update notification failed: %s", e)
        finally:
            if cleanup:
                active_pending_path.unlink(missing_ok=True)
                claimed_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
                exit_code_path.unlink(missing_ok=True)

        return True

    async def _inject_watch_notification(self, synth_text: str, evt: dict) -> None:
        """Inject a watch-pattern notification as a synthetic message event.

        Routing must come from the queued watch event itself, not from whatever
        foreground message happened to be active when the queue was drained.
        """
        source = self._build_process_event_source(evt)
        if not source:
            logger.warning(
                "Dropping watch notification with no routing metadata for process %s",
                evt.get("session_id", "unknown"),
            )
            return
        platform_name = source.platform.value if hasattr(source.platform, "value") else str(source.platform)
        adapter = None
        for p, a in self.adapters.items():
            if p.value == platform_name:
                adapter = a
                break
        if not adapter:
            return
        try:
            synth_event = MessageEvent(
                text=synth_text,
                message_type=MessageType.TEXT,
                source=source,
                internal=True,
                message_id=str(evt.get("message_id") or "").strip() or None,
            )
            logger.info(
                "Watch pattern notification — injecting for %s chat=%s thread=%s",
                platform_name,
                source.chat_id,
                source.thread_id,
            )
            await adapter.handle_message(synth_event)
        except Exception as e:
            logger.error("Watch notification injection error: %s", e)

    async def _run_process_watcher(self, watcher: dict) -> None:
        """
        Periodically check a background process and push updates to the user.

        Runs as an asyncio task. Stays silent when nothing changed.
        Auto-removes when the process exits or is killed.

        Notification mode (from ``display.background_process_notifications``):
          - ``all``    — running-output updates + final message
          - ``result`` — final completion message only
          - ``error``  — final message only when exit code != 0
          - ``off``    — no messages at all
        """
        from tools.process_registry import process_registry

        session_id = watcher["session_id"]
        interval = watcher["check_interval"]
        session_key = watcher.get("session_key", "")
        platform_name = watcher.get("platform", "")
        chat_id = watcher.get("chat_id", "")
        thread_id = watcher.get("thread_id", "")
        user_id = watcher.get("user_id", "")
        user_name = watcher.get("user_name", "")
        message_id = str(watcher.get("message_id") or "").strip() or None
        agent_notify = watcher.get("notify_on_complete", False)
        notify_mode = self._load_background_notifications_mode()

        logger.debug("Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",
                      session_id, interval, notify_mode, agent_notify)

        if notify_mode == "off" and not agent_notify:
            # Still wait for the process to exit so we can log it, but don't
            # push any messages to the user.
            while True:
                await asyncio.sleep(interval)
                session = process_registry.get(session_id)
                if session is None or session.exited:
                    break
            logger.debug("Process watcher ended (silent): %s", session_id)
            return

        last_output_len = 0
        while True:
            await asyncio.sleep(interval)

            session = process_registry.get(session_id)
            if session is None:
                break

            current_output_len = len(session.output_buffer)
            has_new_output = current_output_len > last_output_len
            last_output_len = current_output_len

            if session.exited:
                # --- Agent-triggered completion: inject synthetic message ---
                # Skip if the agent already consumed the result via wait/poll/log
                from tools.process_registry import process_registry as _pr_check
                if agent_notify and not _pr_check.is_completion_consumed(session_id):
                    from tools.ansi_strip import strip_ansi
                    _raw = strip_ansi(session.output_buffer) if session.output_buffer else ""
                    # Truncate at line boundaries so notifications never start
                    # mid-line (fixes #23284). Keep the last ~2000 chars but
                    # snap to the nearest preceding newline, then prepend a
                    # truncation marker when output was cut.
                    _LIMIT = 2000
                    if len(_raw) > _LIMIT:
                        _tail = _raw[-_LIMIT:]
                        _nl = _tail.find("\n")
                        _tail = _tail[_nl + 1:] if _nl != -1 else _tail
                        _out = f"[… output truncated — showing last {len(_tail)} chars]\n{_tail}"
                    else:
                        _out = _raw
                    synth_text = (
                        f"[IMPORTANT: Background process {session_id} completed "
                        f"(exit code {session.exit_code}).\n"
                        f"Command: {session.command}\n"
                        f"Output:\n{_out}]"
                    )
                    source = self._build_process_event_source({
                        "session_id": session_id,
                        "session_key": session_key,
                        "platform": platform_name,
                        "chat_id": chat_id,
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "user_name": user_name,
                    })
                    if not source:
                        logger.warning(
                            "Dropping completion notification with no routing metadata for process %s",
                            session_id,
                        )
                        break

                    adapter = None
                    for p, a in self.adapters.items():
                        if p == source.platform:
                            adapter = a
                            break
                    if adapter and source.chat_id:
                        try:
                            synth_event = MessageEvent(
                                text=synth_text,
                                message_type=MessageType.TEXT,
                                source=source,
                                internal=True,
                                message_id=message_id,
                            )
                            logger.info(
                                "Process %s finished — injecting agent notification for session %s chat=%s thread=%s",
                                session_id,
                                session_key,
                                source.chat_id,
                                source.thread_id,
                            )
                            await adapter.handle_message(synth_event)
                        except Exception as e:
                            logger.error("Agent notify injection error: %s", e)
                    break

                # --- Normal text-only notification ---
                # Decide whether to notify based on mode
                should_notify = (
                    notify_mode in {"all", "result"}
                    or (notify_mode == "error" and session.exit_code not in {0, None})
                )
                if should_notify:
                    new_output = session.output_buffer[-1000:] if session.output_buffer else ""
                    message_text = (
                        f"[Background process {session_id} finished with exit code {session.exit_code}~ "
                        f"Here's the final output:\n{new_output}]"
                    )
                    adapter = None
                    for p, a in self.adapters.items():
                        if p.value == platform_name:
                            adapter = a
                            break
                    if adapter and chat_id:
                        try:
                            send_meta = {"thread_id": thread_id} if thread_id else None
                            await adapter.send(chat_id, message_text, metadata=send_meta)
                        except Exception as e:
                            logger.error("Watcher delivery error: %s", e)
                break

            elif has_new_output and notify_mode == "all" and not agent_notify:
                # New output available -- deliver status update (only in "all" mode)
                # Skip periodic updates for agent_notify watchers (they only care about completion)
                new_output = session.output_buffer[-500:] if session.output_buffer else ""
                message_text = (
                    f"[Background process {session_id} is still running~ "
                    f"New output:\n{new_output}]"
                )
                adapter = None
                for p, a in self.adapters.items():
                    if p.value == platform_name:
                        adapter = a
                        break
                if adapter and chat_id:
                    try:
                        send_meta = {"thread_id": thread_id} if thread_id else None
                        await adapter.send(chat_id, message_text, metadata=send_meta)
                    except Exception as e:
                        logger.error("Watcher delivery error: %s", e)

        logger.debug("Process watcher ended: %s", session_id)

