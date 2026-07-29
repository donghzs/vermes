"""Conversation/session lifecycle command handlers.

Extracted from gateway/slash_commands_mixin.py (W1).
"""
from gateway.slash_handlers._common import *  # noqa: F401,F403


class SessionCommandsMixin:
    """Conversation/session lifecycle command handlers."""

    async def _handle_reset_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        """Handle /new or /reset command."""
        source = event.source

        # Get existing session key
        session_key = self._session_key_for_source(source)
        self._invalidate_session_run_generation(session_key, reason="session_reset")

        # Snapshot the old entry so on_session_finalize can report the
        # expiring session id before reset_session() rotates it.
        old_entry = self.session_store._entries.get(session_key)

        # Close tool resources on the old agent (terminal sandboxes, browser
        # daemons, background processes) before evicting from cache.
        # Guard with getattr because test fixtures may skip __init__.
        _cache_lock = getattr(self, "_agent_cache_lock", None)
        if _cache_lock is not None:
            with _cache_lock:
                _cached = self._agent_cache.get(session_key)
                _old_agent = _cached[0] if isinstance(_cached, tuple) else _cached if _cached else None
            if _old_agent is not None:
                self._cleanup_agent_resources(_old_agent)
        self._evict_cached_agent(session_key)

        # Discard any /queue overflow for this session — /new is a
        # conversation-boundary operation, queued follow-ups from the
        # previous conversation must not bleed into the new one.
        _qe = getattr(self, "_queued_events", None)
        if _qe is not None:
            _qe.pop(session_key, None)

        try:
            from tools.env_passthrough import clear_env_passthrough
            clear_env_passthrough()
        except Exception:
            pass

        try:
            from tools.credential_files import clear_credential_files
            clear_credential_files()
        except Exception:
            pass

        # Reset the session
        new_entry = self.session_store.reset_session(session_key)

        # Clear any session-scoped model/reasoning overrides so the next agent
        # picks up configured defaults instead of previous session switches.
        self._session_model_overrides.pop(session_key, None)
        self._set_session_reasoning_override(session_key, None)
        if hasattr(self, "_pending_model_notes"):
            self._pending_model_notes.pop(session_key, None)

        # Clear session-scoped dangerous-command approvals and /yolo state.
        # /new is a conversation-boundary operation — approval state from the
        # previous conversation must not survive the reset.
        self._clear_session_boundary_security_state(session_key)

        # Fire plugin on_session_finalize hook (session boundary)
        try:
            from vermes_cli.plugins import invoke_hook as _invoke_hook
            _old_sid = old_entry.session_id if old_entry else None
            _invoke_hook("on_session_finalize", session_id=_old_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        # Emit session:end hook (session is ending)
        await self.hooks.emit("session:end", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # Emit session:reset hook
        await self.hooks.emit("session:reset", {
            "platform": source.platform.value if source.platform else "",
            "user_id": source.user_id,
            "session_key": session_key,
        })

        # Resolve session config info to surface to the user
        try:
            session_info = self._format_session_info()
        except Exception:
            session_info = ""

        if new_entry:
            header = self._telegram_topic_new_header(source) or t("gateway.reset.header_default")
        else:
            # No existing session, just create one
            new_entry = self.session_store.get_or_create_session(source, force_new=True)
            header = self._telegram_topic_new_header(source) or t("gateway.reset.header_new")

        # Set session title if provided with /new <title>
        _title_arg = event.get_command_args().strip()
        _title_note = ""
        if _title_arg and self._session_db and new_entry:
            from vermes_state import SessionDB
            try:
                sanitized = SessionDB.sanitize_title(_title_arg)
            except ValueError as e:
                sanitized = None
                _title_note = t("gateway.reset.title_rejected", error=str(e))
            if sanitized:
                try:
                    self._session_db.set_session_title(new_entry.session_id, sanitized)
                    header = t("gateway.reset.header_titled", title=sanitized)
                except ValueError as e:
                    _title_note = t("gateway.reset.title_error_untitled", error=str(e))
                except Exception:
                    pass
            elif not _title_note:
                # sanitize_title returned empty (whitespace-only / unprintable)
                _title_note = t("gateway.reset.title_empty_untitled")
        header = header + _title_note

        # When /new runs inside a Telegram DM topic lane, rewrite the
        # (chat_id, thread_id) → session_id binding so the next message
        # uses the freshly-created session. Without this, the binding
        # still points at the old session and the binding-lookup at the
        # top of _handle_message_with_agent would switch right back.
        if self._is_telegram_topic_lane(source) and new_entry is not None:
            try:
                self._record_telegram_topic_binding(source, new_entry)
            except Exception:
                logger.debug("Failed to rebind Telegram topic after /new", exc_info=True)

        # Fire plugin on_session_reset hook (new session guaranteed to exist)
        try:
            from vermes_cli.plugins import invoke_hook as _invoke_hook
            _new_sid = new_entry.session_id if new_entry else None
            _invoke_hook("on_session_reset", session_id=_new_sid,
                         platform=source.platform.value if source.platform else "")
        except Exception:
            pass

        # Append a random tip to the reset message
        try:
            from vermes_cli.tips import get_random_tip
            _tip_line = t("gateway.reset.tip", tip=get_random_tip())
        except Exception:
            _tip_line = ""

        if session_info:
            return EphemeralReply(f"{header}\n\n{session_info}{_tip_line}")
        return EphemeralReply(f"{header}{_tip_line}")

    async def _handle_retry_command(self, event: MessageEvent) -> str:
        """Handle /retry command - re-send the last user message."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        # Find the last user message
        last_user_msg = None
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_msg = history[i].get("content", "")
                last_user_idx = i
                break

        if not last_user_msg:
            return t("gateway.retry.no_previous")

        # Truncate history to before the last user message and persist
        truncated = history[:last_user_idx]
        self.session_store.rewrite_transcript(session_entry.session_id, truncated)
        # Reset stored token count — transcript was truncated
        session_entry.last_prompt_tokens = 0

        # Re-send by creating a fake text event with the old message
        retry_event = MessageEvent(
            text=last_user_msg,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=event.raw_message,
            channel_prompt=event.channel_prompt,
        )

        # Let the normal message handler process it
        return await self._handle_message(retry_event)

    async def _handle_undo_command(self, event: MessageEvent) -> str:
        """Handle /undo command - remove the last user/assistant exchange."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        # Find the last user message and remove everything from it onward
        last_user_idx = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break

        if last_user_idx is None:
            return t("gateway.undo.nothing")

        removed_msg = history[last_user_idx].get("content", "")
        removed_count = len(history) - last_user_idx
        self.session_store.rewrite_transcript(session_entry.session_id, history[:last_user_idx])
        # Reset stored token count — transcript was truncated
        session_entry.last_prompt_tokens = 0

        preview = removed_msg[:40] + "..." if len(removed_msg) > 40 else removed_msg
        return t("gateway.undo.removed", count=removed_count, preview=preview)

    async def _handle_rollback_command(self, event: MessageEvent) -> str:
        """Handle /rollback command — list or restore filesystem checkpoints."""
        from tools.checkpoint_manager import CheckpointManager, format_checkpoint_list

        # Read checkpoint config from config.yaml
        cp_cfg = {}
        try:
            import yaml as _y
            _cfg_path = _get_hermes_home() / "config.yaml"
            if _cfg_path.exists():
                with open(_cfg_path, encoding="utf-8") as _f:
                    _data = _y.safe_load(_f) or {}
                cp_cfg = _data.get("checkpoints", {})
                if isinstance(cp_cfg, bool):
                    cp_cfg = {"enabled": cp_cfg}
        except Exception:
            pass

        if not cp_cfg.get("enabled", False):
            return t("gateway.rollback.not_enabled")

        mgr = CheckpointManager(
            enabled=True,
            max_snapshots=cp_cfg.get("max_snapshots", 50),
            max_total_size_mb=cp_cfg.get("max_total_size_mb", 500),
            max_file_size_mb=cp_cfg.get("max_file_size_mb", 10),
        )

        cwd = os.getenv("TERMINAL_CWD", str(Path.home()))
        arg = event.get_command_args().strip()

        if not arg:
            checkpoints = mgr.list_checkpoints(cwd)
            return format_checkpoint_list(checkpoints, cwd)

        # Restore by number or hash
        checkpoints = mgr.list_checkpoints(cwd)
        if not checkpoints:
            return t("gateway.rollback.none_found", cwd=cwd)

        target_hash = None
        try:
            idx = int(arg) - 1
            if 0 <= idx < len(checkpoints):
                target_hash = checkpoints[idx]["hash"]
            else:
                return t("gateway.rollback.invalid_number", max=len(checkpoints))
        except ValueError:
            target_hash = arg

        result = mgr.restore(cwd, target_hash)
        if result["success"]:
            return t(
                "gateway.rollback.restored",
                hash=result["restored_to"],
                reason=result["reason"],
            )
        return t("gateway.rollback.restore_failed", error=result["error"])

    async def _handle_title_command(self, event: MessageEvent) -> str:
        """Handle /title command — set or show the current session's title."""
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        session_id = session_entry.session_id

        if not self._session_db:
            from vermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        # Ensure session exists in SQLite DB (it may only exist in session_store
        # if this is the first command in a new session)
        existing_title = self._session_db.get_session_title(session_id)
        if existing_title is None:
            # Session doesn't exist in DB yet — create it
            try:
                self._session_db.create_session(
                    session_id=session_id,
                    source=source.platform.value if source.platform else "unknown",
                    user_id=source.user_id,
                )
            except Exception:
                pass  # Session might already exist, ignore errors

        title_arg = event.get_command_args().strip()
        if title_arg:
            # Sanitize the title before setting
            try:
                sanitized = self._session_db.sanitize_title(title_arg)
            except ValueError as e:
                return t("gateway.shared.warn_passthrough", error=e)
            if not sanitized:
                return t("gateway.title.empty_after_clean")
            # Set the title
            try:
                if self._session_db.set_session_title(session_id, sanitized):
                    return t("gateway.title.set_to", title=sanitized)
                else:
                    return t("gateway.title.not_found")
            except ValueError as e:
                return t("gateway.shared.warn_passthrough", error=e)
        else:
            # Show the current title and session ID
            title = self._session_db.get_session_title(session_id)
            if title:
                return t("gateway.title.current_with_title", session_id=session_id, title=title)
            else:
                return t("gateway.title.current_no_title", session_id=session_id)

    async def _handle_resume_command(self, event: MessageEvent) -> str:
        """Handle /resume command — switch to a previously-named session."""
        if not self._session_db:
            from vermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        source = event.source
        session_key = self._session_key_for_source(source)
        name = event.get_command_args().strip()

        if not name:
            # List recent titled sessions for this user/platform
            try:
                user_source = source.platform.value if source.platform else None
                sessions = self._session_db.list_sessions_rich(
                    source=user_source, limit=10
                )
                titled = [s for s in sessions if s.get("title")]
                if not titled:
                    return t("gateway.resume.no_named_sessions")
                lines = [t("gateway.resume.list_header")]
                for s in titled[:10]:
                    title = s["title"]
                    preview = s.get("preview", "")[:40]
                    preview_part = t("gateway.resume.list_preview_suffix", preview=preview) if preview else ""
                    lines.append(t("gateway.resume.list_item", title=title, preview_part=preview_part))
                lines.append(t("gateway.resume.list_footer"))
                return "\n".join(lines)
            except Exception as e:
                logger.debug("Failed to list titled sessions: %s", e)
                return t("gateway.resume.list_failed", error=e)

        # Resolve the name to a session ID.
        target_id = self._session_db.resolve_session_by_title(name)
        if not target_id:
            return t("gateway.resume.not_found", name=name)
        # Compression creates child continuations that hold the live transcript.
        # Follow that chain so gateway /resume matches CLI behavior (#15000).
        try:
            target_id = self._session_db.resolve_resume_session_id(target_id)
        except Exception as e:
            logger.debug("Failed to resolve resume continuation for %s: %s", target_id, e)

        # Check if already on that session
        current_entry = self.session_store.get_or_create_session(source)
        if current_entry.session_id == target_id:
            return t("gateway.resume.already_on", name=name)

        # Clear any running agent for this session key
        self._release_running_agent_state(session_key)

        # Switch the session entry to point at the old session
        new_entry = self.session_store.switch_session(session_key, target_id)
        if not new_entry:
            return t("gateway.resume.switch_failed")
        self._clear_session_boundary_security_state(session_key)

        # Evict any cached agent for this session so the next message
        # rebuilds with the correct session_id end-to-end — mirrors
        # /branch and /reset. Without this, the cached AIAgent (and its
        # memory provider, which cached `_session_id` during initialize())
        # keeps writing into the wrong session's record. See #6672.
        self._evict_cached_agent(session_key)

        # Get the title for confirmation
        title = self._session_db.get_session_title(target_id) or name

        # Count messages for context
        history = self.session_store.load_transcript(target_id)
        msg_count = len([m for m in history if m.get("role") == "user"]) if history else 0
        if not msg_count:
            return t("gateway.resume.resumed_no_count", title=title)
        if msg_count == 1:
            return t("gateway.resume.resumed_one", title=title, count=msg_count)
        return t("gateway.resume.resumed_many", title=title, count=msg_count)

    async def _handle_branch_command(self, event: MessageEvent) -> str:
        """Handle /branch [name] — fork the current session into a new independent copy.

        Copies conversation history to a new session so the user can explore
        a different approach without losing the original.
        Inspired by Claude Code's /branch command.
        """
        import uuid as _uuid

        if not self._session_db:
            from vermes_state import format_session_db_unavailable
            return format_session_db_unavailable(prefix=t("gateway.shared.session_db_unavailable_prefix"))

        source = event.source
        session_key = self._session_key_for_source(source)

        # Load the current session and its transcript
        current_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(current_entry.session_id)
        if not history:
            return t("gateway.branch.no_conversation")

        branch_name = event.get_command_args().strip()

        # Generate the new session ID
        from datetime import datetime as _dt
        now = _dt.now()
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        short_uuid = _uuid.uuid4().hex[:6]
        new_session_id = f"{timestamp_str}_{short_uuid}"

        # Determine branch title
        if branch_name:
            branch_title = branch_name
        else:
            current_title = self._session_db.get_session_title(current_entry.session_id)
            base = current_title or "branch"
            branch_title = self._session_db.get_next_title_in_lineage(base)

        parent_session_id = current_entry.session_id

        # Create the new session with parent link
        try:
            self._session_db.create_session(
                session_id=new_session_id,
                source=source.platform.value if source.platform else "gateway",
                model=(self.config.get("model", {}) or {}).get("default") if isinstance(self.config, dict) else None,
                parent_session_id=parent_session_id,
            )
        except Exception as e:
            logger.error("Failed to create branch session: %s", e)
            return t("gateway.branch.create_failed", error=e)

        # Copy conversation history to the new session
        for msg in history:
            try:
                self._session_db.append_message(
                    session_id=new_session_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    tool_name=msg.get("tool_name") or msg.get("name"),
                    tool_calls=msg.get("tool_calls"),
                    tool_call_id=msg.get("tool_call_id"),
                    finish_reason=msg.get("finish_reason"),
                    reasoning=msg.get("reasoning"),
                    reasoning_content=msg.get("reasoning_content"),
                    reasoning_details=msg.get("reasoning_details"),
                    codex_reasoning_items=msg.get("codex_reasoning_items"),
                    codex_message_items=msg.get("codex_message_items"),
                )
            except Exception:
                pass  # Best-effort copy

        # Set title
        try:
            self._session_db.set_session_title(new_session_id, branch_title)
        except Exception:
            pass

        # Switch the session store entry to the new session
        new_entry = self.session_store.switch_session(session_key, new_session_id)
        if not new_entry:
            return t("gateway.branch.switch_failed")
        self._clear_session_boundary_security_state(session_key)

        # Evict any cached agent for this session
        self._evict_cached_agent(session_key)

        msg_count = len([m for m in history if m.get("role") == "user"])
        key = "gateway.branch.branched_one" if msg_count == 1 else "gateway.branch.branched_many"
        return t(key, title=branch_title, count=msg_count, parent=parent_session_id, new=new_session_id)

    async def _handle_compress_command(self, event: MessageEvent) -> str:
        """Handle /compress command -- manually compress conversation context.

        Accepts an optional focus topic: ``/compress <focus>`` guides the
        summariser to preserve information related to *focus* while being
        more aggressive about discarding everything else.
        """
        source = event.source
        session_entry = self.session_store.get_or_create_session(source)
        history = self.session_store.load_transcript(session_entry.session_id)

        if not history or len(history) < 4:
            return t("gateway.compress.not_enough")

        # Extract optional focus topic from command args
        focus_topic = (event.get_command_args() or "").strip() or None

        try:
            from run_agent import AIAgent
            from agent.manual_compression_feedback import summarize_manual_compression
            from agent.model_metadata import estimate_request_tokens_rough

            session_key = self._session_key_for_source(source)
            model, runtime_kwargs = self._resolve_session_agent_runtime(
                source=source,
                session_key=session_key,
            )
            if not runtime_kwargs.get("api_key"):
                return t("gateway.compress.no_provider")

            msgs = [
                {"role": m.get("role"), "content": m.get("content")}
                for m in history
                if m.get("role") in {"user", "assistant"} and m.get("content")
            ]

            tmp_agent = AIAgent(
                **runtime_kwargs,
                model=model,
                max_iterations=4,
                quiet_mode=True,
                skip_memory=True,
                enabled_toolsets=["memory"],
                session_id=session_entry.session_id,
            )
            try:
                tmp_agent._print_fn = lambda *a, **kw: None

                # Estimate with system prompt + tool schemas included so the
                # figure reflects real request pressure, not a transcript-only
                # underestimate (#6217). Must be computed after tmp_agent is
                # built so _cached_system_prompt/tools are populated.
                _sys_prompt = getattr(tmp_agent, "_cached_system_prompt", "") or ""
                _tools = getattr(tmp_agent, "tools", None) or None
                approx_tokens = estimate_request_tokens_rough(
                    msgs, system_prompt=_sys_prompt, tools=_tools
                )

                compressor = tmp_agent.context_compressor
                if not compressor.has_content_to_compress(msgs):
                    return t("gateway.compress.nothing_to_do")

                loop = asyncio.get_running_loop()
                compressed, _ = await loop.run_in_executor(
                    None,
                    lambda: tmp_agent._compress_context(msgs, "", approx_tokens=approx_tokens, focus_topic=focus_topic, force=True)
                )

                # _compress_context already calls end_session() on the old session
                # (preserving its full transcript in SQLite) and creates a new
                # session_id for the continuation.  Write the compressed messages
                # into the NEW session so the original history stays searchable.
                new_session_id = tmp_agent.session_id
                if new_session_id != session_entry.session_id:
                    session_entry.session_id = new_session_id
                    self.session_store._save()

                self.session_store.rewrite_transcript(new_session_id, compressed)
                # Reset stored token count — transcript changed, old value is stale
                self.session_store.update_session(
                    session_entry.session_key, last_prompt_tokens=0
                )
                new_tokens = estimate_request_tokens_rough(
                    compressed, system_prompt=_sys_prompt, tools=_tools
                )
                summary = summarize_manual_compression(
                    msgs,
                    compressed,
                    approx_tokens,
                    new_tokens,
                )
                # Detect summary-generation failure so we can surface a
                # visible warning to the user even on the manual /compress
                # path (otherwise the failure is silently logged).
                # _last_compress_aborted means the aux LLM returned no
                # usable summary and the compressor preserved messages
                # unchanged (no drop, no placeholder).  force=True was
                # passed above so any active cooldown is bypassed.
                _summary_aborted = bool(getattr(compressor, "_last_compress_aborted", False))
                _summary_err = getattr(compressor, "_last_summary_error", None)
                # Separately: did the user's CONFIGURED aux model fail
                # and we recovered via main?  Surface that as an info
                # note so they can fix their config.
                _aux_fail_model = getattr(compressor, "_last_aux_model_failure_model", None)
                _aux_fail_err = getattr(compressor, "_last_aux_model_failure_error", None)
            finally:
                # Evict cached agent so next turn rebuilds system prompt
                # from current files (SOUL.md, memory, etc.).
                self._evict_cached_agent(session_key)
                self._cleanup_agent_resources(tmp_agent)
            lines = [f"🗜️ {summary['headline']}"]
            if focus_topic:
                lines.append(t("gateway.compress.focus_line", topic=focus_topic))
            lines.append(summary["token_line"])
            if summary["note"]:
                lines.append(summary["note"])
            if _summary_aborted:
                lines.append(
                    t(
                        "gateway.compress.aborted",
                        error=(_summary_err or "unknown error"),
                    )
                )
            elif _aux_fail_model:
                lines.append(
                    t(
                        "gateway.compress.aux_failed",
                        model=_aux_fail_model,
                        error=(_aux_fail_err or "unknown error"),
                    )
                )
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Manual compress failed: %s", e)
            return t("gateway.compress.failed", error=e)

    async def _handle_forget_command(self, event: MessageEvent) -> str:
        """Handle /forget — clear session recall context for fresh start."""
        try:
            from agent.handoff_store import forget_session, deactivate_handoff

            session_id = getattr(self, '_current_session_id', '') or event.session_id or ''
            if not session_id:
                # Try to get from session manager
                try:
                    session_id = self._session_mgr.current_session_id or ''
                except Exception:
                    pass

            if session_id:
                result = forget_session(session_id)
                if result.get("forgotten"):
                    return f"✅ 已清除会话记忆。下轮对话将从零开始。"
                else:
                    # Fallback: just deactivate
                    ok = deactivate_handoff(session_id)
                    if ok:
                        return f"✅ 已停用会话交接。下轮对话将从零开始。"
                    return f"会话 {session_id} 无可清除的记忆。"
            else:
                return "⚠️ 无法确定当前会话 ID。请直接在桌面端使用 /forget。"
        except Exception as e:
            return f"清除记忆失败: {e}"

