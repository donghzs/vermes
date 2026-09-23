"""System-prompt assembly for :class:`AIAgent`.

The agent's system prompt is built once per session and reused across all
turns — only context compression triggers a rebuild.  This keeps the
upstream prefix cache warm.  See ``Vermes-agent-dev``'s
``references/system-prompt-invariant.md`` for the invariants and
``references/self-improvement-loop.md`` for how the background-review
fork inherits the cached prompt verbatim.

Three tiers are joined with ``\\n\\n``:

* ``stable``   — identity (SOUL.md or DEFAULT_AGENT_IDENTITY), tool
  guidance, computer-use guidance, nous subscription block, tool-use
  enforcement guidance + per-model operational guidance, skills prompt,
  alibaba model-name workaround, environment hints, platform hints.
* ``context``  — caller-supplied ``system_message`` plus context files
  (AGENTS.md / .cursorrules / etc.) discovered under ``TERMINAL_CWD``.
* ``volatile`` — memory snapshot, USER.md profile, external memory
  provider block, timestamp/session/model/provider line.

Pure helpers that read the agent's state.  AIAgent keeps thin forwarders.
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

import json
import os
from typing import Any, Dict, List, Optional

from agent.prompt_builder import (
    PLATFORM_HINTS,
    TOOL_USE_ENFORCEMENT_MODELS,
    TOOL_USE_ENFORCEMENT_EXCLUDED_MODELS,
)

# Phase 1: Prompt Processor loader (YAML-based, hot-reloadable)
from agent.prompt_processor_loader import (
    load_all_processors,
    get_generation as _processor_generation,
)


def _resolve_section(name: str) -> tuple[str, str, str]:
    """S2.2 注入统一入口：按 name 解析一块 prompt 段，返回 (content, source, content_hash)。

    来源优先级（A4）：user processor > builtin YAML > plugin 段 > `computer_use` 惰性兜底。
    S2.4 已退役 `_PROCESSOR_FALLBACK` 硬编码 map；YAML 缺失 → 可见占位 + warning。
    P3 策略层：`disabled_section_ids()` 命中 → **不注入**（返回 `("", "disabled", "")`，
    不塞 `[disabled]` 标记，防污染 A/B）。`computer_use` 保留显式惰性分支（§9b.1）。
    注意：`load_all_processors()` 是事实层（不过滤）；plugin < builtin < user。

    `content_hash`：processor 在场时用 `governance.hash`（parse 时已算好
    `compute_manifest_hash` canonical 值）；惰性兜底用 sha256(content)。
    source 用于诊断（doctor / 排障），不进 prompt。

    这是 S2 walking skeleton 的唯一注入入口（工单 §5 S2.2–S2.4 + P3）。
    """
    from agent.prompt_processor_loader import disabled_section_ids

    # 策略旁路（装配侧唯一点）：命中即不注入，且不往 prompt 塞任何标记。
    if name in disabled_section_ids():
        return "", "disabled", ""
    try:
        for p in load_all_processors():
            if p.effective_id == name or p.name == name:
                if p.metadata.get("source") == "plugin":
                    source = "plugin"
                elif p.builtin:
                    source = "builtin"
                else:
                    source = "user"
                # render_content 负责 plugin_callable / mustache，并强制 max_chars（L-014）
                return p.render_content(), source, p.content_hash
    except Exception as e:
        logger.debug("processor load failed for %s: %s", name, e)
    # §9b.1 哨兵坑：computer_use 的 map 值曾是 None（惰性导入哨兵，不是「无兜底」）。
    # 退役 map 后必须保留显式惰性分支，否则 YAML 缺失时 guidance 静默消失。
    if name == "computer_use":
        from agent.prompt_builder import COMPUTER_USE_GUIDANCE
        return COMPUTER_USE_GUIDANCE, "fallback-lazy", _sha256_of(COMPUTER_USE_GUIDANCE)
    logger.warning("No processor or fallback for: %s", name)
    # 极小可见兜底（Hermes 2026-09-23）：空串会让 editing_guardrails 等安全段静默消失。
    # 不必是原常量全文——但异常必须在 prompt 里露出来，而不是空。
    missing = (
        f"# [prompt-section missing: {name}]\n"
        f"# Expected guidance failed to load. Restore "
        f"vermes_cli/processors/{name}.yaml or reinstall Vermes. "
        f"This placeholder is intentionally visible."
    )
    return missing, "missing", _sha256_of(missing)


def _sha256_of(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _proc_or_default(name: str) -> str:
    """薄包装：返回 `_resolve_section(name)[0]`（兼容/测试用）。

    S2.3 起注入点一律走 `_resolve_section`；S2.4 退役硬编码 map 后，
    除 `computer_use` 惰性兜底外，YAML 缺失返回 `""` 并打 warning。
    """
    content, _source, _h = _resolve_section(name)
    return content


def _ra():
    """Lazy reference to the ``run_agent`` module.

    Helpers like ``load_soul_md``, ``build_environment_hints``,
    ``build_context_files_prompt``, ``build_nous_subscription_prompt``,
    ``build_skills_system_prompt`` and ``get_toolset_for_tool`` are
    imported into ``run_agent``'s namespace.  Many tests
    ``patch("run_agent.load_soul_md", ...)``; if we imported them
    directly here those patches would not reach us.  Looking them up
    through ``run_agent`` on every call preserves the patch contract.
    """
    import run_agent
    return run_agent


def _resolve_platform_hint(agent: Any, platform_key: str, default_hint: str) -> str:
    """Apply a per-platform prompt-hint override to the default hint.

    Reads ``agent._platform_hint_overrides`` (populated from
    ``config.yaml`` ``platform_hints`` by ``agent_init``) and resolves the
    effective hint for *platform_key*:

      * ``replace`` — substitute the default hint entirely.
      * ``append``  — keep the default and append the extra text.
      * a bare string value — treated as ``append`` (convenience shorthand).

    Precedence: ``replace`` wins over ``append`` if both are present.
    Defensive: any malformed entry falls back to the unmodified default.
    """
    if not platform_key:
        return default_hint
    overrides = getattr(agent, "_platform_hint_overrides", None)
    if not isinstance(overrides, dict) or not overrides:
        return default_hint
    spec = overrides.get(platform_key)
    if spec is None:
        return default_hint

    # Shorthand: a bare string is treated as append text.
    if isinstance(spec, str):
        extra = spec.strip()
        return f"{default_hint}\n\n{extra}".strip() if extra else default_hint

    if not isinstance(spec, dict):
        return default_hint

    replace_text = spec.get("replace")
    if isinstance(replace_text, str) and replace_text.strip():
        base = replace_text.strip()
    else:
        base = default_hint

    append_text = spec.get("append")
    if isinstance(append_text, str) and append_text.strip():
        return f"{base}\n\n{append_text.strip()}".strip()
    return base


def _get_processor(name: str) -> Optional[str]:
    """Get a prompt processor's content by name.
    
    Returns None if processors aren't loaded or the named processor
    isn't found. Callers should fall back to the hardcoded constant.
    """
    try:
        procs = load_all_processors()
        for p in procs:
            if p.name == name:
                return p.render_content()
    except Exception as e:
        logger.debug("processor load failed for %s: %s", name, e)
    return None


def _get_injectable_processors(agent: Any) -> List[str]:
    """Evaluate all processors' triggers and return injectable content list.
    
    Currently unused — individual _get_processor calls are used instead
    to preserve the existing code structure and fallback behavior.
    This function exists for future bulk-injection optimization.
    """
    try:
        procs = load_all_processors()
        return [p.render_content() for p in procs if p.should_inject(agent)]
    except Exception:
        return []


def build_system_prompt_parts(agent: Any, system_message: Optional[str] = None) -> Dict[str, str]:
    """Assemble the system prompt as three ordered parts.

    Returns a dict with three keys:
      * ``stable``   — identity, tool guidance, skills prompt,
        environment hints, platform hints, model-family operational
        guidance.
      * ``context``  — context files (AGENTS.md, .cursorrules, etc.)
        and caller-supplied system_message.
      * ``volatile`` — memory snapshot, user profile, external
        memory provider block, timestamp line.

    Joined into a single string by :func:`build_system_prompt` and
    cached on ``agent._cached_system_prompt`` for the lifetime of the
    AIAgent.  Vermes never re-renders parts of this string mid-
    session — that's the only way to keep upstream prompt caches
    warm across turns.
    """
    # Local import to avoid pulling model_tools at module load.  Tests
    # patch ``run_agent.get_toolset_for_tool`` and similar helpers, so
    # we resolve through ``_ra()`` to honor those patches.
    _r = _ra()

    # ── Stable tier ────────────────────────────────────────────────
    stable_parts: List[str] = []

    # Try SOUL.md as primary identity unless the caller explicitly skipped it.
    # Some execution modes (cron) still want VERMES_HOME persona while keeping
    # cwd project instructions disabled.
    _soul_loaded = False
    if agent.load_soul_identity or not agent.skip_context_files:
        _soul_content = _r.load_soul_md()
        if _soul_content:
            stable_parts.append(_soul_content)
            _soul_loaded = True

    if not _soul_loaded:
        # S2.2 walking skeleton：identity 走统一注入入口（YAML processor）。
        # 与 `_proc_or_default("identity")` 字节等价；额外暴露 source/content_hash 供诊断。
        stable_parts.append(_resolve_section("identity")[0])

    # ── Phase 1: Processor-driven guidance injection ──────────────
    # Load YAML-based prompt processors. Each processor has a declarative
    # trigger (always / tool_present / config_flag / model_match / etc.)
    # and its content is plain text loaded from ~/.vermes/processors/ or
    # the built-in bundle. User processors override built-in by name.
    # Dynamic logic (tool_use_enforcement if/else, kanban _worker_guidance,
    # alibaba f-string, env hints, platform hints) stays in code below.
    _procs = _get_injectable_processors(agent)

    # help_guidance (always inject)
    stable_parts.append(_resolve_section("help_guidance")[0])

    # task_completion (config_flag)
    if getattr(agent, "_task_completion_guidance", True) and agent.valid_tool_names:
        stable_parts.append(_resolve_section("task_completion")[0])
        # W-L4：编辑护栏与完成纪律同注入条件（提示层；硬闸后置）
        stable_parts.append(_resolve_section("editing_guardrails")[0])

    # Tool-aware behavioral guidance: only inject when the tools are loaded
    tool_guidance = []
    if "memory" in agent.valid_tool_names:
        tool_guidance.append(_resolve_section("memory_guidance")[0])
    if "session_search" in agent.valid_tool_names:
        tool_guidance.append(_resolve_section("session_search")[0])
    if "skill_manage" in agent.valid_tool_names:
        tool_guidance.append(_resolve_section("skills_guidance")[0])
    if "image_generate" in agent.valid_tool_names:
        tool_guidance.append(_resolve_section("image_generate")[0])
    if "web_search" in agent.valid_tool_names:
        tool_guidance.append(_resolve_section("academic_search")[0])
    # Kanban worker/orchestrator lifecycle — only present when the
    # dispatcher spawned this process (kanban_show check_fn gates on
    # VERMES_KANBAN_TASK env var). Normal chat sessions never see
    # this block. Resolved once at __init__ (see _kanban_worker_guidance).
    _kanban_guidance = getattr(agent, "_kanban_worker_guidance", None)
    if _kanban_guidance:
        tool_guidance.append(_kanban_guidance)
    elif _kanban_guidance is None and "kanban_show" in agent.valid_tool_names:
        # Fallback for code paths that bypass agent_init (rare).
        tool_guidance.append(_resolve_section("kanban")[0])
    if tool_guidance:
        stable_parts.append(" ".join(tool_guidance))

    # ScholarForge paper suite — own block (multi-paragraph, like computer_use
    # below). Function-calling schemas alone give the model ~27 loose
    # scholarforge_* tools with no call order and no project-context rule, so it
    # falls back to web_search + write_file for paper work and silently bypasses
    # multi-source retrieval, citation verification and the quality gate.
    if agent.valid_tool_names and (
        "scholarforge_write" in agent.valid_tool_names
        or "scholarforge_search" in agent.valid_tool_names
    ):
        _sf_guidance = _resolve_section("scholarforge_workflow")[0]
        if _sf_guidance:
            stable_parts.append(_sf_guidance)

    # Computer-use (macOS) — goes in as its own block rather than being
    # merged into tool_guidance because the content is multi-paragraph.
    if "computer_use" in agent.valid_tool_names:
        stable_parts.append(_resolve_section("computer_use")[0])

    nous_subscription_prompt = _r.build_nous_subscription_prompt(agent.valid_tool_names)
    if nous_subscription_prompt:
        stable_parts.append(nous_subscription_prompt)
    # Tool-use enforcement: tells the model to actually call tools instead
    # of describing intended actions.  Controlled by config.yaml
    # agent.tool_use_enforcement:
    #   "auto" (default) — enables for ALL models (except EXCLUDED_MODELS)
    #   true  — always inject (all models)
    #   false — never inject
    #   list  — custom model-name substrings to match
    if agent.valid_tool_names:
        _enforce = agent._tool_use_enforcement
        _inject = False
        if _enforce is True or (isinstance(_enforce, str) and _enforce.lower() in {"true", "always", "yes", "on"}):
            _inject = True
        elif _enforce is False or (isinstance(_enforce, str) and _enforce.lower() in {"false", "never", "no", "off"}):
            _inject = False
        elif isinstance(_enforce, list):
            model_lower = (agent.model or "").lower()
            _inject = any(p.lower() in model_lower for p in _enforce if isinstance(p, str))
        else:
            # "auto" or any unrecognised value — enable for ALL models by
            # default.  Only exclude models known to lack tool-calling support.
            model_lower = (agent.model or "").lower()
            _inject = not any(
                p in model_lower
                for p in TOOL_USE_ENFORCEMENT_EXCLUDED_MODELS
            )
        if _inject:
            stable_parts.append(_resolve_section("tool_use_enforcement")[0])
            _model_lower = (agent.model or "").lower()
            # Google model operational guidance (conciseness, absolute
            # paths, parallel tool calls, verify-before-edit, etc.)
            if "gemini" in _model_lower or "gemma" in _model_lower:
                stable_parts.append(_resolve_section("google_model")[0])
            # OpenAI GPT/Codex execution discipline (tool persistence,
            # prerequisite checks, verification, anti-hallucination).
            # Also applied to xAI Grok — same failure modes (claims completion
            # without tool calls, suggests workarounds instead of using
            # existing tools, replies with plans instead of executing).
            if "gpt" in _model_lower or "codex" in _model_lower or "grok" in _model_lower:
                stable_parts.append(_resolve_section("openai_model")[0])

    has_skills_tools = any(name in agent.valid_tool_names for name in ['skills_list', 'skill_view', 'skill_manage'])
    if has_skills_tools:
        avail_toolsets = {
            toolset
            for toolset in (
                _r.get_toolset_for_tool(tool_name) for tool_name in agent.valid_tool_names
            )
            if toolset
        }
        skills_prompt = _r.build_skills_system_prompt(
            available_tools=agent.valid_tool_names,
            available_toolsets=avail_toolsets,
            # P1：config agent.compact_skill_categories=off|auto；auto 仅在代码目录降级
            # A′：传 agent.platform，让 messaging/群聊渠道硬门在 resolve 内短路（永不降级）
            compact_categories=_r.resolve_compact_skill_categories(
                platform=getattr(agent, "platform", None),
            ),
        )
    else:
        skills_prompt = ""
    if skills_prompt:
        stable_parts.append(skills_prompt)

    # Alibaba Coding Plan API always returns "glm-4.7" as model name regardless
    # of the requested model. Inject explicit model identity into the system prompt
    # so the agent can correctly report which model it is (workaround for API bug).
    # Stable for the lifetime of an agent instance — model and provider are fixed
    # at construction time.
    if agent.provider == "alibaba":
        _model_short = agent.model.split("/")[-1] if "/" in agent.model else agent.model
        stable_parts.append(
            f"You are powered by the model named {_model_short}. "
            f"The exact model ID is {agent.model}. "
            f"When asked what model you are, always answer based on this information, "
            f"not on any model name returned by the API."
        )

    # Environment hints (WSL, Termux, etc.) — tell the agent about the
    # execution environment so it can translate paths and adapt behavior.
    # Stable for the lifetime of the process.
    _env_hints = _r.build_environment_hints()
    if _env_hints:
        stable_parts.append(_env_hints)

    # Local Python toolchain probe — names python/pip/uv/PEP-668 state when
    # something is non-default so the model can pick the right install
    # strategy without discovering by failure.  Emits a single line; emits
    # NOTHING when the environment is clean (no token cost).  Skipped
    # entirely for remote terminal backends (the host's Python state is
    # irrelevant when tools run inside docker/modal/ssh).  Gated by
    # config.yaml ``agent.environment_probe`` (default True).
    if getattr(agent, "_environment_probe", True):
        try:
            from tools.env_probe import get_environment_probe_line
            _probe_line = get_environment_probe_line()
            if _probe_line:
                stable_parts.append(_probe_line)
        except Exception:
            # Probe failure must never block prompt build.
            pass

    # Active-profile hint — names the Vermes profile the agent is running
    # under so it doesn't conflate ~/.vermes/skills/ (default profile) with
    # ~/.vermes/profiles/<active>/skills/ (this profile's). Deterministic
    # for the lifetime of the agent — profile name doesn't change
    # mid-session, so this doesn't break the prompt cache.
    # See file_safety._resolve_active_profile_name + classify_cross_profile_target
    # for the matching tool-side guard.
    try:
        from agent.file_safety import _resolve_active_profile_name
        active_profile = _resolve_active_profile_name()
    except Exception:
        active_profile = "default"
    if active_profile == "default":
        stable_parts.append(
            "Active Vermes profile: default. Other profiles (if any) live "
            "under ~/.vermes/profiles/<name>/. Each profile has its own "
            "skills/, plugins/, cron/, and memories/ that affect a different "
            "session than this one. Do not modify another profile's "
            "skills/plugins/cron/memories unless the user explicitly directs "
            "you to."
        )
    else:
        stable_parts.append(
            f"Active Vermes profile: {active_profile}. This session reads "
            f"and writes ~/.vermes/profiles/{active_profile}/. The default "
            f"profile's data lives at ~/.vermes/skills/, ~/.vermes/plugins/, "
            f"~/.vermes/cron/, ~/.vermes/memories/ — those belong to a "
            f"different session run from a different shell. Do NOT modify "
            f"another profile's skills/plugins/cron/memories unless the user "
            f"explicitly directs you to. The cross-profile write guard will "
            f"refuse such writes by default; pass cross_profile=True only "
            f"after explicit direction."
        )

    platform_key = (agent.platform or "").lower().strip()
    # Resolve the built-in/plugin default hint for this platform, then apply
    # any per-platform override from config (platform_hints.<platform>).
    _default_hint = ""
    if platform_key in PLATFORM_HINTS:
        _default_hint = PLATFORM_HINTS[platform_key]
    elif platform_key:
        # Check plugin registry for platform-specific LLM guidance
        try:
            from gateway.platform_registry import platform_registry
            _entry = platform_registry.get(platform_key)
            if _entry and _entry.platform_hint:
                _default_hint = _entry.platform_hint
        except Exception as e:
            logger.debug("system_prompt.py: build system prompt parts failed: %s", e)

    _effective_hint = _resolve_platform_hint(agent, platform_key, _default_hint)
    if _effective_hint:
        stable_parts.append(_effective_hint)

    # ── Context tier (cwd-dependent, may change between sessions) ─
    context_parts: List[str] = []
    _context_cwd = os.getenv("TERMINAL_CWD") or None

    # Note: ephemeral_system_prompt is NOT included here. It's injected at
    # API-call time only so it stays out of the cached/stored system prompt.
    if system_message is not None:
        context_parts.append(system_message)

    if not agent.skip_context_files:
        # Use TERMINAL_CWD for context file discovery when set (gateway
        # mode).  The gateway process runs from the Vermes-agent install
        # dir, so os.getcwd() would pick up the repo's AGENTS.md and
        # other dev files — inflating token usage by ~10k for no benefit.
        context_files_prompt = _r.build_context_files_prompt(
            cwd=_context_cwd, skip_soul=_soul_loaded)
        if context_files_prompt:
            context_parts.append(context_files_prompt)

    # W-L5：工作区事实块（有 git 区才注入；gateway 无 TERMINAL_CWD 且 messaging → 跳过）
    try:
        _ws_block = _r.build_workspace_block(
            _context_cwd,
            platform=getattr(agent, "platform", None),
        )
        if _ws_block:
            context_parts.append(_ws_block)
    except Exception:
        pass

    # ── Volatile tier (changes per session/turn — never cached) ───
    volatile_parts: List[str] = []

    if agent._memory_store:
        if agent._memory_enabled:
            mem_block = agent._memory_store.format_for_system_prompt("memory")
            if mem_block:
                volatile_parts.append(mem_block)
        # USER.md is always included when enabled.
        if agent._user_profile_enabled:
            user_block = agent._memory_store.format_for_system_prompt("user")
            if user_block:
                volatile_parts.append(user_block)

    # External memory provider system prompt block (additive to built-in)
    if agent._memory_manager:
        try:
            _ext_mem_block = agent._memory_manager.build_system_prompt()
            if _ext_mem_block:
                volatile_parts.append(_ext_mem_block)
        except Exception as e:
            logger.debug("system_prompt.py: build system prompt parts failed: %s", e)

    # Session handoff: cross-session continuity (loaded at turn 1)
    _handoff_block = getattr(agent, "_handoff_context", None)

    # Evolution data: learned experience from past sessions
    _evolution_block = getattr(agent, "_evolution_context", None)

    # Memory recall: auto-retrieved context for current user message
    _recall_block = getattr(agent, "_recall_context", None)

    # Cross-session continuity: cluster evolution briefing
    _continuity_block = getattr(agent, "_continuity_context", None)

    # Per-turn active memory recall: memory_aware_executor pre_task_recall
    _task_memory_block = getattr(agent, "_task_memory_context", None)

    # Decision tracking: standing decisions from past sessions
    _decisions_block = ""
    try:
        from agent.decision_tracker import format_decisions_for_prompt
        _decisions_block = format_decisions_for_prompt(limit=5)
    except Exception as e:
        logger.debug("system_prompt.py: build system prompt parts failed: %s", e)

    # Phase 5: Apply unified memory budget across all memory injections
    _memory_blocks = {
        "_recall_context": _recall_block or "",
        "_task_memory_context": _task_memory_block or "",
        "_handoff_context": _handoff_block or "",
        "_evolution_context": _evolution_block or "",
        "_decisions_context": _decisions_block or "",
        "_continuity_context": _continuity_block or "",
    }
    try:
        from agent.memory_budget import apply_budget, format_memory_summary
        _trimmed_blocks = apply_budget(_memory_blocks)
        for _block_text in _trimmed_blocks.values():
            if _block_text:
                volatile_parts.append(_block_text)
        # Log memory usage summary
        _summary = format_memory_summary(_memory_blocks)
        logger.debug(_summary)
    except Exception:
        # Fallback: append blocks individually if budget manager fails
        if _handoff_block:
            volatile_parts.append(_handoff_block)
        if _evolution_block:
            volatile_parts.append(_evolution_block)
        if _recall_block:
            volatile_parts.append(_recall_block)
        if _decisions_block:
            volatile_parts.append(_decisions_block)
        if _continuity_block:
            volatile_parts.append(_continuity_block)

    # ── Capability status: let Agent know what it can do ──
    try:
        from agent.capability_registry import get_capability_report_prompt
        _cap_prompt = get_capability_report_prompt()
        if _cap_prompt:
            volatile_parts.append(_cap_prompt)
    except Exception as e:
        logger.debug("system_prompt.py: build system prompt parts failed: %s", e)

    # M5: 能力意识→任务路由自检引导
    # 让 agent 在接受任务前先自检：是否有对应能力/技能/工具
    _m5_self_check = (
        "<capability_self_check>\n"
        "在接受任务前，先自检：\n"
        "1. 当前有哪些已安装的技能/工具/模块可能匹配这个任务？\n"
        "2. 是否有 pending 技能可以激活来提升完成质量？\n"
        "3. 如果缺少能力，先说明缺口再尝试，不要裸跑失败。\n"
        "4. 优先复用已有能力（技能/工具/模块），而不是从零开始。\n"
        "</capability_self_check>"
    )
    volatile_parts.append(_m5_self_check)

    # ── Extracted skills: active skills + pending for user confirmation ──
    try:
        from agent.skill_extractor import get_active_skills_prompt, get_pending_skills_prompt
        from pathlib import Path
        _db = Path(os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes"))) / "evolution" / "self-model.db"
        if _db.exists():
            _active_skills = get_active_skills_prompt(str(_db))
            if _active_skills:
                volatile_parts.append(_active_skills)
            _pending_skills = get_pending_skills_prompt(str(_db))
            if _pending_skills:
                volatile_parts.append(_pending_skills)
    except Exception as e:
        logger.debug("system_prompt.py: build system prompt parts failed: %s", e)

    from vermes_time import now as _vermes_now
    now = _vermes_now()
    # Date-only (not minute-precision) so the system prompt is byte-stable
    # for the full day.  Minute-precision changes invalidate prefix-cache KV
    # on every rebuild path (compression boundary, fresh-agent gateway turns,
    # session resume without a stored prompt).  The model can still query the
    # exact wall-clock time via tools when it actually needs it.
    # Credit: @iamfoz (PR #20451).
    timestamp_line = f"Conversation started: {now.strftime('%A, %B %d, %Y')}"
    if agent.pass_session_id and agent.session_id:
        timestamp_line += f"\nSession ID: {agent.session_id}"
    if agent.model:
        timestamp_line += f"\nModel: {agent.model}"
    if agent.provider:
        timestamp_line += f"\nProvider: {agent.provider}"
    volatile_parts.append(timestamp_line)

    return {
        "stable":   "\n\n".join(p.strip() for p in stable_parts   if p and p.strip()),
        "context":  "\n\n".join(p.strip() for p in context_parts  if p and p.strip()),
        "volatile": "\n\n".join(p.strip() for p in volatile_parts if p and p.strip()),
    }


def build_system_prompt(agent: Any, system_message: Optional[str] = None) -> str:
    """Assemble the full system prompt from all layers.

    Called once per session (cached on ``agent._cached_system_prompt``) and
    only rebuilt after context compression events. This ensures the system
    prompt is stable across all turns in a session, maximizing prefix cache
    hits.

    Layers are ordered cache-friendly: stable identity/guidance first,
    then session-stable context files, then per-call volatile content
    (memory, USER profile, timestamp).  The whole string is treated as
    one cached block — Vermes never rebuilds or reinjects parts of it
    mid-session, which is the only way to keep upstream prompt caches
    warm across turns.
    """
    parts = build_system_prompt_parts(agent, system_message=system_message)
    return "\n\n".join(p for p in (parts["stable"], parts["context"], parts["volatile"]) if p)


def invalidate_system_prompt(agent: Any) -> None:
    """Invalidate the cached system prompt, forcing a rebuild on the next turn.

    Called after context compression events. Also reloads memory from disk
    so the rebuilt prompt captures any writes from this session.
    """
    agent._cached_system_prompt = None
    if agent._memory_store:
        agent._memory_store.load_from_disk()


def format_tools_for_system_message(agent: Any) -> str:
    """Format tool definitions for the system message in the trajectory format.

    Returns:
        str: JSON string representation of tool definitions
    """
    if not agent.tools:
        return "[]"

    # Convert tool definitions to the format expected in trajectories
    formatted_tools = []
    for tool in agent.tools:
        func = tool["function"]
        formatted_tool = {
            "name": func["name"],
            "description": func.get("description", ""),
            "parameters": func.get("parameters", {}),
            "required": None  # Match the format in the example
        }
        formatted_tools.append(formatted_tool)

    return json.dumps(formatted_tools, ensure_ascii=False)


__all__ = [
    "build_system_prompt_parts",
    "build_system_prompt",
    "invalidate_system_prompt",
    "format_tools_for_system_message",
]
