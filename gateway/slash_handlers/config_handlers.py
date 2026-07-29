"""Agent/model/runtime toggle command handlers.

Extracted from gateway/slash_commands_mixin.py (W1).
"""
from gateway.slash_handlers._common import *  # noqa: F401,F403


class ConfigCommandsMixin:
    """Agent/model/runtime toggle command handlers."""

    async def _handle_model_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /model command — switch model for this session.

        Supports:
          /model                              — interactive picker (Telegram/Discord) or text list
          /model <name>                       — switch for this session only
          /model <name> --global              — switch and persist to config.yaml
          /model <name> --provider <provider> — switch provider + model
          /model --provider <provider>        — switch to provider, auto-detect model
        """
        import yaml
        from vermes_cli.model_switch import (
            switch_model as _switch_model, parse_model_flags,
            list_authenticated_providers,
            list_picker_providers,
        )
        from vermes_cli.providers import get_label

        raw_args = event.get_command_args().strip()

        # Parse --provider and --global flags
        model_input, explicit_provider, persist_global = parse_model_flags(raw_args)

        # Read current model/provider from config
        current_model = ""
        current_provider = "openrouter"
        current_base_url = ""
        current_api_key = ""
        user_provs = None
        custom_provs = None
        config_path = _get_vermes_home() / "config.yaml"
        try:
            cfg = _load_gateway_config()
            if cfg:
                model_cfg = cfg.get("model", {})
                if isinstance(model_cfg, dict):
                    current_model = model_cfg.get("default", "")
                    current_provider = model_cfg.get("provider", current_provider)
                    current_base_url = model_cfg.get("base_url", "")
                user_provs = cfg.get("providers")
                try:
                    from vermes_cli.config import get_compatible_custom_providers
                    custom_provs = get_compatible_custom_providers(cfg)
                except Exception:
                    custom_provs = cfg.get("custom_providers")
        except Exception:
            pass

        # Check for session override
        source = event.source
        session_key = self._session_key_for_source(source)
        override = self._session_model_overrides.get(session_key, {})
        if override:
            current_model = override.get("model", current_model)
            current_provider = override.get("provider", current_provider)
            current_base_url = override.get("base_url", current_base_url)
            current_api_key = override.get("api_key", current_api_key)

        # No args: show interactive picker (Telegram/Discord) or text list
        if not model_input and not explicit_provider:
            # Try interactive picker if the platform supports it
            adapter = self.adapters.get(source.platform)
            has_picker = (
                adapter is not None
                and getattr(type(adapter), "send_model_picker", None) is not None
            )

            if has_picker:
                try:
                    providers = list_picker_providers(
                        current_provider=current_provider,
                        current_base_url=current_base_url,
                        current_model=current_model,
                        user_providers=user_provs,
                        custom_providers=custom_provs,
                        max_models=50,
                    )
                except Exception:
                    providers = []

                if providers:
                    # Build a callback closure for when the user picks a model.
                    # Captures self + locals needed for the switch logic.
                    _self = self
                    _session_key = session_key
                    _cur_model = current_model
                    _cur_provider = current_provider
                    _cur_base_url = current_base_url
                    _cur_api_key = current_api_key

                    async def _on_model_selected(
                        _chat_id: str, model_id: str, provider_slug: str
                    ) -> str:
                        """Perform the model switch and return confirmation text."""
                        result = _switch_model(
                            raw_input=model_id,
                            current_provider=_cur_provider,
                            current_model=_cur_model,
                            current_base_url=_cur_base_url,
                            current_api_key=_cur_api_key,
                            is_global=False,
                            explicit_provider=provider_slug,
                            user_providers=user_provs,
                            custom_providers=custom_provs,
                        )
                        if not result.success:
                            return t("gateway.model.error_prefix", error=result.error_message)

                        # Update cached agent in-place
                        cached_entry = None
                        _cache_lock = getattr(_self, "_agent_cache_lock", None)
                        _cache = getattr(_self, "_agent_cache", None)
                        if _cache_lock and _cache is not None:
                            with _cache_lock:
                                cached_entry = _cache.get(_session_key)
                        if cached_entry and cached_entry[0] is not None:
                            try:
                                cached_entry[0].switch_model(
                                    new_model=result.new_model,
                                    new_provider=result.target_provider,
                                    api_key=result.api_key,
                                    base_url=result.base_url,
                                    api_mode=result.api_mode,
                                )
                            except Exception as exc:
                                logger.warning("Picker model switch failed for cached agent: %s", exc)

                        # Store model note + session override
                        if not hasattr(_self, "_pending_model_notes"):
                            _self._pending_model_notes = {}
                        _self._pending_model_notes[_session_key] = (
                            f"[Note: model was just switched from {_cur_model} to {result.new_model} "
                            f"via {result.provider_label or result.target_provider}. "
                            f"Adjust your self-identification accordingly.]"
                        )
                        _self._session_model_overrides[_session_key] = {
                            "model": result.new_model,
                            "provider": result.target_provider,
                            "api_key": result.api_key,
                            "base_url": result.base_url,
                            "api_mode": result.api_mode,
                        }

                        # Evict cached agent so the next turn creates a fresh
                        # agent from the override rather than relying on the
                        # stale cache signature to trigger a rebuild.
                        _self._evict_cached_agent(_session_key)

                        # Build confirmation text
                        plabel = result.provider_label or result.target_provider
                        lines = [t("gateway.model.switched", model=result.new_model)]
                        lines.append(t("gateway.model.provider_label", provider=plabel))
                        mi = result.model_info
                        from vermes_cli.model_switch import resolve_display_context_length
                        _sw_config_ctx = None
                        try:
                            _sw_cfg = _load_gateway_config()
                            _sw_model_cfg = _sw_cfg.get("model", {})
                            if isinstance(_sw_model_cfg, dict):
                                _sw_raw = _sw_model_cfg.get("context_length")
                                if _sw_raw is not None:
                                    _sw_config_ctx = int(_sw_raw)
                        except Exception:
                            pass
                        ctx = resolve_display_context_length(
                            result.new_model,
                            result.target_provider,
                            base_url=result.base_url or current_base_url or "",
                            api_key=result.api_key or current_api_key or "",
                            model_info=mi,
                            custom_providers=custom_provs,
                            config_context_length=_sw_config_ctx,
                        )
                        if ctx:
                            lines.append(t("gateway.model.context_label", tokens=f"{ctx:,}"))
                        if mi:
                            if mi.max_output:
                                lines.append(t("gateway.model.max_output_label", tokens=f"{mi.max_output:,}"))
                            if mi.has_cost_data():
                                lines.append(t("gateway.model.cost_label", cost=mi.format_cost()))
                            lines.append(t("gateway.model.capabilities_label", capabilities=mi.format_capabilities()))
                        lines.append(t("gateway.model.session_only_hint"))
                        return "\n".join(lines)

                    metadata = self._thread_metadata_for_source(source, self._reply_anchor_for_event(event))
                    result = await adapter.send_model_picker(
                        chat_id=source.chat_id,
                        providers=providers,
                        current_model=current_model,
                        current_provider=current_provider,
                        session_key=session_key,
                        on_model_selected=_on_model_selected,
                        metadata=metadata,
                    )
                    if result.success:
                        return None  # Picker sent — adapter handles the response

            # Fallback: text list (for platforms without picker or if picker failed)
            provider_label = get_label(current_provider)
            lines = [t("gateway.model.current_label", model=current_model or "unknown", provider=provider_label), ""]

            try:
                providers = list_authenticated_providers(
                    current_provider=current_provider,
                    current_base_url=current_base_url,
                    current_model=current_model,
                    user_providers=user_provs,
                    custom_providers=custom_provs,
                    max_models=5,
                )
                for p in providers:
                    tag = t("gateway.model.current_tag") if p["is_current"] else ""
                    lines.append(f"**{p['name']}** `--provider {p['slug']}`{tag}:")
                    if p["models"]:
                        model_strs = ", ".join(f"`{m}`" for m in p["models"])
                        extra = t("gateway.model.more_models_suffix", count=p["total_models"] - len(p["models"])) if p["total_models"] > len(p["models"]) else ""
                        lines.append(f"  {model_strs}{extra}")
                    elif p.get("api_url"):
                        lines.append(f"  `{p['api_url']}`")
                    lines.append("")
            except Exception:
                pass

            lines.append(t("gateway.model.usage_switch_model"))
            lines.append(t("gateway.model.usage_switch_provider"))
            lines.append(t("gateway.model.usage_persist"))
            return "\n".join(lines)

        # Perform the switch
        result = _switch_model(
            raw_input=model_input,
            current_provider=current_provider,
            current_model=current_model,
            current_base_url=current_base_url,
            current_api_key=current_api_key,
            is_global=persist_global,
            explicit_provider=explicit_provider,
            user_providers=user_provs,
            custom_providers=custom_provs,
        )

        if not result.success:
            return t("gateway.model.error_prefix", error=result.error_message)

        # If there's a cached agent, update it in-place
        cached_entry = None
        _cache_lock = getattr(self, "_agent_cache_lock", None)
        _cache = getattr(self, "_agent_cache", None)
        if _cache_lock and _cache is not None:
            with _cache_lock:
                cached_entry = _cache.get(session_key)

        if cached_entry and cached_entry[0] is not None:
            try:
                cached_entry[0].switch_model(
                    new_model=result.new_model,
                    new_provider=result.target_provider,
                    api_key=result.api_key,
                    base_url=result.base_url,
                    api_mode=result.api_mode,
                )
            except Exception as exc:
                logger.warning("In-place model switch failed for cached agent: %s", exc)

        # Store a note to prepend to the next user message so the model
        # knows about the switch (avoids system messages mid-history).
        if not hasattr(self, "_pending_model_notes"):
            self._pending_model_notes = {}
        self._pending_model_notes[session_key] = (
            f"[Note: model was just switched from {current_model} to {result.new_model} "
            f"via {result.provider_label or result.target_provider}. "
            f"Adjust your self-identification accordingly.]"
        )

        # Store session override so next agent creation uses the new model
        self._session_model_overrides[session_key] = {
            "model": result.new_model,
            "provider": result.target_provider,
            "api_key": result.api_key,
            "base_url": result.base_url,
            "api_mode": result.api_mode,
        }

        # Evict cached agent so the next turn creates a fresh agent from the
        # override rather than relying on cache signature mismatch detection.
        self._evict_cached_agent(session_key)

        # Persist to config if --global
        if persist_global:
            try:
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        cfg = yaml.safe_load(f) or {}
                else:
                    cfg = {}
                model_cfg = cfg.setdefault("model", {})
                model_cfg["default"] = result.new_model
                model_cfg["provider"] = result.target_provider
                if result.base_url:
                    model_cfg["base_url"] = result.base_url
                from vermes_cli.config import save_config
                save_config(cfg)
            except Exception as e:
                logger.warning("Failed to persist model switch: %s", e)

        # Build confirmation message with full metadata
        provider_label = result.provider_label or result.target_provider
        lines = [t("gateway.model.switched", model=result.new_model)]
        lines.append(t("gateway.model.provider_label", provider=provider_label))

        # Context: always resolve via the provider-aware chain so Codex OAuth,
        # Copilot, and Nous-enforced caps win over the raw models.dev entry.
        mi = result.model_info
        from vermes_cli.model_switch import resolve_display_context_length
        _sw2_config_ctx = None
        try:
            _sw2_cfg = _load_gateway_config()
            _sw2_model_cfg = _sw2_cfg.get("model", {})
            if isinstance(_sw2_model_cfg, dict):
                _sw2_raw = _sw2_model_cfg.get("context_length")
                if _sw2_raw is not None:
                    _sw2_config_ctx = int(_sw2_raw)
        except Exception:
            pass
        ctx = resolve_display_context_length(
            result.new_model,
            result.target_provider,
            base_url=result.base_url or current_base_url or "",
            api_key=result.api_key or current_api_key or "",
            model_info=mi,
            custom_providers=custom_provs,
            config_context_length=_sw2_config_ctx,
        )
        if ctx:
            lines.append(t("gateway.model.context_label", tokens=f"{ctx:,}"))
        if mi:
            if mi.max_output:
                lines.append(t("gateway.model.max_output_label", tokens=f"{mi.max_output:,}"))
            if mi.has_cost_data():
                lines.append(t("gateway.model.cost_label", cost=mi.format_cost()))
            lines.append(t("gateway.model.capabilities_label", capabilities=mi.format_capabilities()))

        # Cache notice
        cache_enabled = (
            (base_url_host_matches(result.base_url or "", "openrouter.ai") and "claude" in result.new_model.lower())
            or result.api_mode == "anthropic_messages"
        )
        if cache_enabled:
            lines.append(t("gateway.model.prompt_caching_enabled"))

        if result.warning_message:
            lines.append(t("gateway.model.warning_prefix", warning=result.warning_message))

        if persist_global:
            lines.append(t("gateway.model.saved_global"))
        else:
            lines.append(t("gateway.model.session_only_hint"))

        return "\n".join(lines)

    async def _handle_codex_runtime_command(self, event: MessageEvent) -> str:
        """Handle /codex-runtime command in the gateway.

        Same surface as the CLI handler in cli.py:
            /codex-runtime                  — show current state
            /codex-runtime auto             — Vermes default runtime
            /codex-runtime codex_app_server — codex subprocess runtime
            /codex-runtime on / off         — synonyms

        On change, the cached agent for this session is evicted so the next
        message creates a fresh AIAgent with the new api_mode wired in
        (avoids prompt-cache invalidation mid-session)."""
        from vermes_cli import codex_runtime_switch as crs

        raw_args = event.get_command_args().strip() if event else ""
        new_value, errors = crs.parse_args(raw_args)
        if errors:
            return "❌ " + "\n❌ ".join(errors)

        # Load + persist via the same helpers used for /model and /yolo
        try:
            from vermes_cli.config import load_config, save_config
        except Exception as exc:
            return f"❌ Could not load config: {exc}"
        cfg = load_config()

        result = crs.apply(
            cfg,
            new_value,
            persist_callback=(save_config if new_value is not None else None),
        )

        # On a real change, evict the cached agent so the new runtime takes
        # effect on the next message rather than waiting for cache TTL.
        if result.success and new_value is not None and result.requires_new_session:
            try:
                session_key = self._session_key_for_source(event.source)
                self._evict_cached_agent(session_key)
            except Exception:
                logger.debug("could not evict cached agent after codex-runtime change",
                             exc_info=True)

        prefix = "✓" if result.success else "✗"
        return f"{prefix} {result.message}"

    async def _handle_personality_command(self, event: MessageEvent) -> str:
        """Handle /personality command - list or set a personality."""
        from vermes_constants import display_vermes_home

        args = event.get_command_args().strip().lower()
        config_path = _get_vermes_home() / 'config.yaml'

        try:
            config = _load_gateway_config()
            personalities = cfg_get(config, "agent", "personalities", default={})
        except Exception:
            config = {}
            personalities = {}

        if not personalities:
            return t("gateway.personality.none_configured", path=display_vermes_home())

        if not args:
            lines = [t("gateway.personality.header")]
            lines.append(t("gateway.personality.none_option"))
            for name, prompt in personalities.items():
                if isinstance(prompt, dict):
                    preview = prompt.get("description") or prompt.get("system_prompt", "")[:50]
                else:
                    preview = prompt[:50] + "..." if len(prompt) > 50 else prompt
                lines.append(t("gateway.personality.item", name=name, preview=preview))
            lines.append(t("gateway.personality.usage"))
            return "\n".join(lines)

        def _resolve_prompt(value):
            if isinstance(value, dict):
                parts = [value.get("system_prompt", "")]
                if value.get("tone"):
                    parts.append(f'Tone: {value["tone"]}')
                if value.get("style"):
                    parts.append(f'Style: {value["style"]}')
                return "\n".join(p for p in parts if p)
            return str(value)

        if args in {"none", "default", "neutral"}:
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = ""
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return t("gateway.personality.save_failed", error=str(e))
            self._ephemeral_system_prompt = ""
            return t("gateway.personality.cleared")
        elif args in personalities:
            new_prompt = _resolve_prompt(personalities[args])

            # Write to config.yaml, same pattern as CLI save_config_value.
            try:
                if "agent" not in config or not isinstance(config.get("agent"), dict):
                    config["agent"] = {}
                config["agent"]["system_prompt"] = new_prompt
                atomic_yaml_write(config_path, config)
            except Exception as e:
                return t("gateway.personality.save_failed", error=str(e))

            # Update in-memory so it takes effect on the very next message.
            self._ephemeral_system_prompt = new_prompt

            return t("gateway.personality.set_to", name=args)

        available = "`none`, " + ", ".join(f"`{n}`" for n in personalities)
        return t("gateway.personality.unknown", name=args, available=available)

    async def _handle_reasoning_command(self, event: MessageEvent) -> str:
        """Handle /reasoning command — manage reasoning effort and display toggle.

        Usage:
            /reasoning                       Show current effort level and display state
            /reasoning <level>               Set reasoning effort for this session only
            /reasoning <level> --global      Persist reasoning effort to config.yaml
            /reasoning reset                 Clear this session's reasoning override
            /reasoning show|on               Show model reasoning in responses
            /reasoning hide|off              Hide model reasoning from responses
        """
        import yaml

        raw_args = event.get_command_args().strip()
        args, persist_global = self._parse_reasoning_command_args(raw_args)
        config_path = _get_vermes_home() / "config.yaml"
        session_key = self._session_key_for_source(event.source)
        self._show_reasoning = self._load_show_reasoning()
        self._reasoning_config = self._resolve_session_reasoning_config(
            source=event.source,
            session_key=session_key,
        )

        def _save_config_key(key_path: str, value):
            """Save a dot-separated key to config.yaml."""
            try:
                user_config = {}
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        user_config = yaml.safe_load(f) or {}
                keys = key_path.split(".")
                current = user_config
                for k in keys[:-1]:
                    if k not in current or not isinstance(current[k], dict):
                        current[k] = {}
                    current = current[k]
                current[keys[-1]] = value
                atomic_yaml_write(config_path, user_config)
                return True
            except Exception as e:
                logger.error("Failed to save config key %s: %s", key_path, e)
                return False

        if not raw_args:
            # Show current state
            rc = self._reasoning_config
            if rc is None:
                level = t("gateway.reasoning.level_default")
            elif rc.get("enabled") is False:
                level = t("gateway.reasoning.level_disabled")
            else:
                level = rc.get("effort", "medium")
            display_state = (
                t("gateway.reasoning.display_on")
                if self._show_reasoning
                else t("gateway.reasoning.display_off")
            )
            has_session_override = session_key in (getattr(self, "_session_reasoning_overrides", {}) or {})
            scope = (
                t("gateway.reasoning.scope_session")
                if has_session_override
                else t("gateway.reasoning.scope_global")
            )
            return t(
                "gateway.reasoning.status",
                level=level,
                scope=scope,
                display=display_state,
            )

        # Display toggle (per-platform)
        platform_key = _platform_config_key(event.source.platform)
        if args in {"show", "on"}:
            self._show_reasoning = True
            _save_config_key(f"display.platforms.{platform_key}.show_reasoning", True)
            return t("gateway.reasoning.display_set_on", platform=platform_key)

        if args in {"hide", "off"}:
            self._show_reasoning = False
            _save_config_key(f"display.platforms.{platform_key}.show_reasoning", False)
            return t("gateway.reasoning.display_set_off", platform=platform_key)

        # Effort level change
        effort = args.strip()
        if effort == "reset":
            if persist_global:
                return t("gateway.reasoning.reset_global_unsupported")
            self._set_session_reasoning_override(session_key, None)
            self._reasoning_config = self._load_reasoning_config()
            self._evict_cached_agent(session_key)
            return t("gateway.reasoning.reset_done")
        if effort == "none":
            parsed = {"enabled": False}
        elif effort in {"minimal", "low", "medium", "high", "xhigh"}:
            parsed = {"enabled": True, "effort": effort}
        else:
            return t(
                "gateway.reasoning.unknown_arg",
                arg=effort or raw_args.lower(),
            )

        self._reasoning_config = parsed
        if persist_global:
            if _save_config_key("agent.reasoning_effort", effort):
                self._set_session_reasoning_override(session_key, None)
                self._evict_cached_agent(session_key)
                return t("gateway.reasoning.set_global", effort=effort)
            self._set_session_reasoning_override(session_key, parsed)
            self._evict_cached_agent(session_key)
            return t("gateway.reasoning.set_global_save_failed", effort=effort)

        self._set_session_reasoning_override(session_key, parsed)
        self._evict_cached_agent(session_key)
        return t("gateway.reasoning.set_session", effort=effort)

    async def _handle_fast_command(self, event: MessageEvent) -> str:
        """Handle /fast — mirror the CLI Priority Processing toggle in gateway chats."""
        import yaml
        from vermes_cli.models import model_supports_fast_mode

        args = event.get_command_args().strip().lower()
        config_path = _get_vermes_home() / "config.yaml"
        self._service_tier = self._load_service_tier()

        user_config = _load_gateway_config()
        model = _resolve_gateway_model(user_config)
        if not model_supports_fast_mode(model):
            return t("gateway.fast.not_supported")

        def _save_config_key(key_path: str, value):
            """Save a dot-separated key to config.yaml."""
            try:
                user_config = {}
                if config_path.exists():
                    with open(config_path, encoding="utf-8") as f:
                        user_config = yaml.safe_load(f) or {}
                keys = key_path.split(".")
                current = user_config
                for k in keys[:-1]:
                    if k not in current or not isinstance(current[k], dict):
                        current[k] = {}
                    current = current[k]
                current[keys[-1]] = value
                atomic_yaml_write(config_path, user_config)
                return True
            except Exception as e:
                logger.error("Failed to save config key %s: %s", key_path, e)
                return False

        if not args or args == "status":
            status = t("gateway.fast.status_fast") if self._service_tier == "priority" else t("gateway.fast.status_normal")
            return t("gateway.fast.status", mode=status)

        if args in {"fast", "on"}:
            self._service_tier = "priority"
            saved_value = "fast"
            label = t("gateway.fast.label_fast")
        elif args in {"normal", "off"}:
            self._service_tier = None
            saved_value = "normal"
            label = t("gateway.fast.label_normal")
        else:
            return t("gateway.fast.unknown_arg", arg=args)

        if _save_config_key("agent.service_tier", saved_value):
            return t("gateway.fast.saved", label=label)
        return t("gateway.fast.session_only", label=label)

    async def _handle_background_command(self, event: MessageEvent) -> str:
        """Handle /background <prompt> — run a prompt in a separate background session.

        Spawns a new AIAgent in a background thread with its own session.
        When it completes, sends the result back to the same chat without
        modifying the active session's conversation history.
        """
        prompt = event.get_command_args().strip()
        if not prompt:
            return t("gateway.background.usage")

        source = event.source
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{os.urandom(3).hex()}"

        event_message_id = self._reply_anchor_for_event(event)

        # Forward image/audio attachments so the background agent can see them.
        media_urls = list(event.media_urls) if event.media_urls else []
        media_types = list(event.media_types) if event.media_types else []

        # Fire-and-forget the background task
        _task = asyncio.create_task(
            self._run_background_task(
                prompt,
                source,
                task_id,
                event_message_id=event_message_id,
                media_urls=media_urls,
                media_types=media_types,
            )
        )
        self._background_tasks.add(_task)
        _task.add_done_callback(self._background_tasks.discard)

        preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
        return t("gateway.background.started", preview=preview, task_id=task_id)

    async def _handle_yolo_command(self, event: MessageEvent) -> Union[str, EphemeralReply]:
        """Handle /yolo — toggle dangerous command approval bypass for this session only."""
        from tools.approval import (
            disable_session_yolo,
            enable_session_yolo,
            is_session_yolo_enabled,
        )

        session_key = self._session_key_for_source(event.source)
        current = is_session_yolo_enabled(session_key)
        if current:
            disable_session_yolo(session_key)
            return EphemeralReply(t("gateway.yolo.disabled"))
        else:
            enable_session_yolo(session_key)
            return EphemeralReply(t("gateway.yolo.enabled"))

    async def _handle_verbose_command(self, event: MessageEvent) -> str:
        """Handle /verbose command — cycle tool progress display mode.

        Gated by ``display.tool_progress_command`` in config.yaml (default off).
        When enabled, cycles the tool progress mode through off → new → all →
        verbose → off for the *current platform*.  The setting is saved to
        ``display.platforms.<platform>.tool_progress`` so each channel can
        have its own verbosity level independently.
        """

        config_path = _get_vermes_home() / "config.yaml"
        platform_key = _platform_config_key(event.source.platform)

        # --- check config gate ------------------------------------------------
        try:
            user_config = _load_gateway_config()
            gate_enabled = is_truthy_value(
                cfg_get(user_config, "display", "tool_progress_command"),
                default=False,
            )
        except Exception:
            gate_enabled = False

        if not gate_enabled:
            return t("gateway.verbose.not_enabled")

        # --- cycle mode (per-platform) ----------------------------------------
        cycle = ["off", "new", "all", "verbose"]
        descriptions = {
            "off": t("gateway.verbose.mode_off"),
            "new": t("gateway.verbose.mode_new"),
            "all": t("gateway.verbose.mode_all"),
            "verbose": t("gateway.verbose.mode_verbose"),
        }

        # Read current effective mode for this platform via the resolver
        from gateway.display_config import resolve_display_setting
        current = resolve_display_setting(user_config, platform_key, "tool_progress", "all")
        if current not in cycle:
            current = "all"
        idx = (cycle.index(current) + 1) % len(cycle)
        new_mode = cycle[idx]

        # Save to display.platforms.<platform>.tool_progress
        try:
            if "display" not in user_config or not isinstance(user_config.get("display"), dict):
                user_config["display"] = {}
            display = user_config["display"]
            if "platforms" not in display or not isinstance(display.get("platforms"), dict):
                display["platforms"] = {}
            if platform_key not in display["platforms"] or not isinstance(display["platforms"].get(platform_key), dict):
                display["platforms"][platform_key] = {}
            display["platforms"][platform_key]["tool_progress"] = new_mode
            atomic_yaml_write(config_path, user_config)
            return (
                f"{descriptions[new_mode]}\n"
                + t("gateway.verbose.saved_suffix", platform=platform_key)
            )
        except Exception as e:
            logger.warning("Failed to save tool_progress mode: %s", e)
            return f"{descriptions[new_mode]}\n" + t("gateway.verbose.save_failed", error=e)

    async def _handle_footer_command(self, event: MessageEvent) -> str:
        """Handle /footer command — toggle the runtime-metadata footer.

        Usage:
            /footer           → toggle on/off
            /footer on        → enable globally
            /footer off       → disable globally
            /footer status    → show current state + fields

        The footer is saved to ``display.runtime_footer.enabled`` (global).
        Per-platform overrides under ``display.platforms.<platform>.runtime_footer``
        are respected but not modified here — edit config.yaml directly for
        per-platform control.
        """
        from gateway.runtime_footer import resolve_footer_config

        config_path = _get_vermes_home() / "config.yaml"
        platform_key = _platform_config_key(event.source.platform)

        # --- parse argument -------------------------------------------------
        arg = ""
        try:
            text = (getattr(event, "message", None) or "").strip()
            if text.startswith("/"):
                parts = text.split(None, 1)
                if len(parts) > 1:
                    arg = parts[1].strip().lower()
        except Exception:
            arg = ""

        # --- load config ----------------------------------------------------
        try:
            user_config: dict = _load_gateway_config()
        except Exception as e:
            return t("gateway.config_read_failed", error=e)

        effective = resolve_footer_config(user_config, platform_key)

        if arg in {"status", "?"}:
            state = t("gateway.footer.state_on") if effective["enabled"] else t("gateway.footer.state_off")
            fields = ", ".join(effective.get("fields") or [])
            return t(
                "gateway.footer.status",
                state=state,
                fields=fields,
                platform=platform_key,
            )

        if arg in {"on", "enable", "true", "1"}:
            new_state = True
        elif arg in {"off", "disable", "false", "0"}:
            new_state = False
        elif arg == "":
            new_state = not effective["enabled"]
        else:
            return t("gateway.footer.usage")

        # --- write global flag ---------------------------------------------
        try:
            if not isinstance(user_config.get("display"), dict):
                user_config["display"] = {}
            display = user_config["display"]
            if not isinstance(display.get("runtime_footer"), dict):
                display["runtime_footer"] = {}
            display["runtime_footer"]["enabled"] = new_state
            atomic_yaml_write(config_path, user_config)
        except Exception as e:
            logger.warning("Failed to save runtime_footer.enabled: %s", e)
            return t("gateway.config_save_failed", error=e)

        state = t("gateway.footer.state_on") if new_state else t("gateway.footer.state_off")
        example = ""
        if new_state:
            # Show a preview using current agent state if available.
            from gateway.runtime_footer import format_runtime_footer
            preview = format_runtime_footer(
                model=_resolve_gateway_model(user_config) or None,
                context_tokens=0,
                context_length=None,
                fields=effective.get("fields") or ["model", "context_pct", "cwd"],
            )
            if preview:
                example = t("gateway.footer.example_line", preview=preview)
        return t("gateway.footer.saved", state=state, example=example)

    async def _handle_set_home_command(self, event: MessageEvent) -> str:
        """Handle /sethome command -- set the current chat as the platform's home channel."""
        source = event.source
        platform_name = source.platform.value if source.platform else "unknown"
        chat_id = source.chat_id
        chat_name = source.chat_name or chat_id

        env_key = _home_target_env_var(platform_name)
        thread_env_key = _home_thread_env_var(platform_name)
        thread_id = source.thread_id

        # Save to .env so it persists across restarts
        try:
            from vermes_cli.config import save_env_value
            save_env_value(env_key, str(chat_id))
            # Keep thread/topic routing explicit and clear stale values when
            # /sethome is run from the parent chat instead of a thread.
            save_env_value(thread_env_key, str(thread_id or ""))
        except Exception as e:
            return t("gateway.set_home.save_failed", error=e)

        # Keep the running gateway config in sync too. The pre-restart
        # notification path reads self.config before the process reloads env.
        if source.platform:
            platform_config = self.config.platforms.setdefault(
                source.platform,
                PlatformConfig(enabled=True),
            )
            platform_config.home_channel = HomeChannel(
                platform=source.platform,
                chat_id=str(chat_id),
                name=chat_name,
                thread_id=str(thread_id) if thread_id else None,
            )

        return t("gateway.set_home.success", name=chat_name, chat_id=chat_id)

