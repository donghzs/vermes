"""Skills/MCP/approval/emergence command handlers.

Extracted from gateway/slash_commands_mixin.py (W1).
"""
from gateway.slash_handlers._common import *  # noqa: F401,F403


class CapabilityCommandsMixin:
    """Skills/MCP/approval/emergence command handlers."""

    async def _handle_reload_mcp_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /reload-mcp — reconnect MCP servers and rebuild the cached agent.

        Reloading MCP tools invalidates the provider prompt cache for the
        active session (tool schemas are baked into the system prompt).  The
        next message re-sends full input tokens, which is expensive on
        long-context or high-reasoning models.

        To surface that cost, the command routes through the slash-confirm
        primitive: users get an Approve Once / Always Approve / Cancel
        prompt before the reload actually runs.  "Always Approve" persists
        ``approvals.mcp_reload_confirm: false`` so the prompt is silenced
        for subsequent reloads in any session.

        Users can also skip the confirm by flipping the config key directly.
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        # Read the gate fresh from disk so a prior "always" click takes
        # effect on the next invocation without restarting the gateway.
        user_config = self._read_user_config()
        approvals = user_config.get("approvals") if isinstance(user_config, dict) else None
        confirm_required = True
        if isinstance(approvals, dict):
            confirm_required = bool(approvals.get("mcp_reload_confirm", True))

        if not confirm_required:
            return await self._execute_mcp_reload(event)

        # Route through slash-confirm.  The primitive sends the prompt and
        # stores the resume handler; the button/text response triggers
        # ``_resolve_slash_confirm`` which invokes the handler with the
        # chosen outcome.
        async def _on_confirm(choice: str) -> Optional[str]:
            if choice == "cancel":
                return t("gateway.reload_mcp.cancelled")
            if choice == "always":
                # Persist the opt-out and run the reload.
                try:
                    from cli import save_config_value
                    save_config_value("approvals.mcp_reload_confirm", False)
                    logger.info(
                        "User opted out of /reload-mcp confirmation (session=%s)",
                        session_key,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist mcp_reload_confirm=false: %s", exc)
            # once / always → run the reload
            result = await self._execute_mcp_reload(event)
            if choice == "always":
                return f"{result}\n\n" + t("gateway.reload_mcp.always_followup")
            return result

        prompt_message = t("gateway.reload_mcp.confirm_prompt")
        return await self._request_slash_confirm(
            event=event,
            command="reload-mcp",
            title="/reload-mcp",
            message=prompt_message,
            handler=_on_confirm,
        )

    async def _handle_reload_skills_command(self, event: MessageEvent) -> str:
        """Handle /reload-skills — rescan skills dir, queue a note for next turn.

        Skills don't need to be in the system prompt for the model to use
        them (they're invoked via ``/skill-name``, ``skills_list``, or
        ``skill_view`` at runtime), so this does NOT clear the prompt cache
        — prefix caching stays intact.

        If any skills were added or removed, a one-shot note is queued on
        ``self._pending_skills_reload_notes[session_key]``. The gateway
        prepends it to the NEXT user message in this session (see the
        consumer at ~L11025 in ``_run_agent_turn``), then clears it. Nothing
        is written to the session transcript out-of-band, so message
        alternation is preserved.
        """
        loop = asyncio.get_running_loop()
        try:
            from agent.skill_commands import reload_skills

            result = await loop.run_in_executor(None, reload_skills)
            added = result.get("added", [])      # [{"name", "description"}, ...]
            removed = result.get("removed", [])  # [{"name", "description"}, ...]
            total = result.get("total", 0)

            # Let each connected adapter refresh any platform-side state
            # that cached the skill list at startup. Today that's the
            # Discord /skill autocomplete (registered once per connect);
            # without this call, new skills stay invisible in the
            # dropdown and deleted skills error out when clicked. Other
            # adapters that don't override refresh_skill_group (Telegram's
            # BotCommand menu, Slack subcommand map, etc.) are silently
            # skipped — the in-process reload above is enough for them.
            for adapter in list(self.adapters.values()):
                refresh = getattr(adapter, "refresh_skill_group", None)
                if not callable(refresh):
                    continue
                try:
                    maybe = refresh()
                    if inspect.isawaitable(maybe):
                        await maybe
                except Exception as exc:
                    logger.warning(
                        "Adapter %s refresh_skill_group raised: %s",
                        getattr(adapter, "name", adapter), exc,
                    )

            lines = [t("gateway.reload_skills.header")]
            if not added and not removed:
                lines.append(t("gateway.reload_skills.no_new"))
                lines.append(t("gateway.reload_skills.total", count=total))
                return "\n".join(lines)

            def _fmt_line(item: dict) -> str:
                nm = item.get("name", "")
                desc = item.get("description", "")
                if desc:
                    return t("gateway.reload_skills.item_with_desc", name=nm, desc=desc)
                return t("gateway.reload_skills.item_no_desc", name=nm)

            if added:
                lines.append(t("gateway.reload_skills.added_header"))
                for item in added:
                    lines.append(_fmt_line(item))
            if removed:
                lines.append(t("gateway.reload_skills.removed_header"))
                for item in removed:
                    lines.append(_fmt_line(item))
            lines.append(t("gateway.reload_skills.total", count=total))

            # Queue the one-shot note for the next user turn in this session.
            # Format matches how the system prompt renders pre-existing
            # skills (``    - name: description``) so the model reads the
            # diff in the same shape as its original skill catalog.
            sections = ["[USER INITIATED SKILLS RELOAD:"]
            if added:
                sections.append("")
                sections.append("新增技能：")
                for item in added:
                    sections.append(_fmt_line(item))
            if removed:
                sections.append("")
                sections.append("移除技能：")
                for item in removed:
                    sections.append(_fmt_line(item))
            sections.append("")
            sections.append("使用 skills_list 查看更新后的技能列表。]")
            note = "\n".join(sections)

            session_key = self._session_key_for_source(event.source)
            if not hasattr(self, "_pending_skills_reload_notes"):
                self._pending_skills_reload_notes = {}
            if session_key:
                self._pending_skills_reload_notes[session_key] = note

            return "\n".join(lines)

        except Exception as e:
            logger.warning("Skills reload failed: %s", e)
            return t("gateway.reload_skills.failed", error=e)

    async def _handle_bundles_command(self, event: MessageEvent) -> str:
        """Handle /bundles — list installed skill bundles.

        Mirrors the CLI ``/bundles`` handler. Returns a single text
        message suitable for any gateway adapter; bundles are loaded by
        invoking the bundle's own ``/<slug>`` command, not by this one.
        """
        try:
            from agent.skill_bundles import list_bundles, _bundles_dir
        except Exception as exc:
            logger.warning("Bundles command unavailable: %s", exc)
            return f"Bundles subsystem unavailable: {exc}"

        bundles = list_bundles()
        if not bundles:
            return (
                "No skill bundles installed.\n"
                "Create one on the host with:\n"
                "  `hermes bundles create <name> --skill <s1> --skill <s2>`\n"
                f"Directory: `{_bundles_dir()}`"
            )

        lines = [f"**Skill Bundles** ({len(bundles)} installed):", ""]
        for info in bundles:
            skill_count = len(info.get("skills", []))
            desc = info.get("description") or f"Load {skill_count} skills"
            lines.append(
                f"• `/{info['slug']}` — {desc} _({skill_count} skills)_"
            )
            for s in info.get("skills", []):
                lines.append(f"    · {s}")
        lines.append("")
        lines.append("Invoke a bundle with `/<slug>` to load all its skills.")
        return "\n".join(lines)

    async def _handle_approve_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /approve command — unblock waiting agent thread(s).

        The agent thread(s) are blocked inside tools/approval.py waiting for
        the user to respond.  This handler signals the event so the agent
        resumes and the terminal_tool executes the command inline — the same
        flow as the CLI's synchronous input() approval.

        Supports multiple concurrent approvals (parallel subagents,
        execute_code).  ``/approve`` resolves the oldest pending command;
        ``/approve all`` resolves every pending command at once.

        Usage:
            /approve              — approve oldest pending command once
            /approve all          — approve ALL pending commands at once
            /approve session      — approve oldest + remember for session
            /approve all session  — approve all + remember for session
            /approve always       — approve oldest + remember permanently
            /approve all always   — approve all + remember permanently
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        from tools.approval import (
            resolve_gateway_approval, has_blocking_approval,
        )

        if not has_blocking_approval(session_key):
            if session_key in self._pending_approvals:
                self._pending_approvals.pop(session_key)
                return t("gateway.approval_expired")
            return t("gateway.approve.no_pending")

        # Parse args: support "all", "all session", "all always", "session", "always"
        args = event.get_command_args().strip().lower().split()
        resolve_all = "all" in args
        remaining = [a for a in args if a != "all"]

        if any(a in {"always", "permanent", "permanently"} for a in remaining):
            choice = "always"
        elif any(a in {"session", "ses"} for a in remaining):
            choice = "session"
        else:
            choice = "once"

        count = resolve_gateway_approval(session_key, choice, resolve_all=resolve_all)
        if not count:
            return t("gateway.approve.no_pending")

        # Resume typing indicator — agent is about to continue processing.
        _adapter = self.adapters.get(source.platform)
        if _adapter:
            _adapter.resume_typing_for_chat(source.chat_id)

        logger.info("User approved %d dangerous command(s) via /approve (%s)", count, choice)
        plural = "plural" if count > 1 else "singular"
        return t(f"gateway.approve.{choice}_{plural}", count=count)

    async def _handle_deny_command(self, event: MessageEvent) -> str:
        """Handle /deny command — reject pending dangerous command(s).

        Signals blocked agent thread(s) with a 'deny' result so they receive
        a definitive BLOCKED message, same as the CLI deny flow.

        ``/deny`` denies the oldest; ``/deny all`` denies everything.
        """
        source = event.source
        session_key = self._session_key_for_source(source)

        from tools.approval import (
            resolve_gateway_approval, has_blocking_approval,
        )

        if not has_blocking_approval(session_key):
            if session_key in self._pending_approvals:
                self._pending_approvals.pop(session_key)
                return t("gateway.deny.stale")
            return t("gateway.deny.no_pending")

        args = event.get_command_args().strip().lower()
        resolve_all = "all" in args

        count = resolve_gateway_approval(session_key, "deny", resolve_all=resolve_all)
        if not count:
            return t("gateway.deny.no_pending")

        # Resume typing indicator — agent continues (with BLOCKED result).
        _adapter = self.adapters.get(source.platform)
        if _adapter:
            _adapter.resume_typing_for_chat(source.chat_id)

        logger.info("User denied %d dangerous command(s) via /deny", count)
        if count > 1:
            return t("gateway.deny.denied_plural", count=count)
        return t("gateway.deny.denied_singular")

    async def _handle_emergence_command(self, event: MessageEvent) -> str:
        """Handle /emergence — show emergence system status."""
        try:
            from agent.evolution_manager import get_self_model_db
            db_path = str(get_self_model_db())

            lines = ["🧠 涌现系统状态", ""]

            # Richness
            try:
                from agent.memory_recall import compute_richness
                r = compute_richness(db_path)
                tier_map = {"cold_start": "冷启动", "building": "积累中", "learning": "学习中", "fluent": "熟练"}
                lines.append(f"记忆丰富度: {tier_map.get(r.tier, r.tier)} ({r.score:.3f})")
                lines.append(f"  事件密度: {r.raw_event_density:.3f} | 簇密度: {r.cluster_density:.3f}")
                lines.append(f"  会话密度: {r.session_density:.3f} | 交接密度: {r.handoff_density:.3f}")
            except Exception as e:
                lines.append(f"记忆丰富度: 不可用 ({e})")
            lines.append("")

            # Clusters
            try:
                import sqlite3 as _sql
                conn = _sql.connect(db_path)
                conn.row_factory = _sql.Row
                c = conn.cursor()
                c.execute("SELECT status, COUNT(*) as cnt FROM clusters GROUP BY status")
                rows = c.fetchall()
                c.execute("SELECT COUNT(*) FROM raw_events")
                total = c.fetchone()[0]
                conn.close()
                if rows:
                    cluster_str = ", ".join(f"{r['status']}={r['cnt']}" for r in rows)
                    lines.append(f"簇: {cluster_str} (总事件: {total})")
                else:
                    lines.append(f"簇: 无 (总事件: {total})")
            except Exception as e:
                lines.append(f"簇: 不可用 ({e})")
            lines.append("")

            # Capabilities
            try:
                from agent.capability_registry import get_capability_report
                caps = get_capability_report()
                active = caps.get("active", [])
                installed = caps.get("installed", [])
                if active:
                    lines.append(f"已激活能力: {', '.join(active)}")
                else:
                    lines.append("已激活能力: 无")
                if installed:
                    lines.append(f"已安装能力: {', '.join(installed)}")
            except Exception:
                lines.append("能力注册表: 不可用")
            lines.append("")

            # Health
            try:
                from agent.raw_event import _LAST_EMERGENCE_OK, _EMERGENCE_STALE_THRESHOLD
                from datetime import datetime as _dt
                if _get_run_attr("_LAST_EMERGENCE_OK") is not None:
                    stale_h = (_dt.now() - _get_run_attr("_LAST_EMERGENCE_OK")).total_seconds() / 3600
                    healthy = (_dt.now() - _get_run_attr("_LAST_EMERGENCE_OK")) < _get_run_attr("_EMERGENCE_STALE_THRESHOLD")
                    status_text = f"✅ 正常 (上次成功: {stale_h:.1f}h前)" if healthy else f"⚠️ 静默 {stale_h:.1f}h"
                else:
                    status_text = "❄️ 冷启动中"
                lines.append(f"链路状态: {status_text}")
            except Exception:
                lines.append("链路状态: 不可用")

            # Pending skills
            try:
                from agent.skill_extractor import SkillExtractor
                extractor = SkillExtractor(db_path)
                pending = extractor.list_skills(status="pending")
                if pending:
                    lines.append("")
                    lines.append(f"📋 待确认技能 ({len(pending)}):")
                    for s in pending[:5]:
                        lines.append(f"  • {s.name}: {s.description[:60]}")
                    if len(pending) > 5:
                        lines.append(f"  ...还有 {len(pending) - 5} 个")
            except Exception:
                pass

            return "\n".join(lines)
        except Exception as e:
            return f"涌现系统不可用: {e}"

