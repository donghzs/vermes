"""Turn lifecycle service for ``run_conversation``.

Extracted from ``agent/conversation_loop.py`` as part of the god-file
decomposition campaign (A2 §7.5 stage 3 — the TurnService seam). Two
pure setup helpers are lifted here verbatim:

* ``initialize_turn``  — formerly ``_initialize_turn``
  (conversation_loop.py:916). Stdio guard, provider bind, H1.1 task
  pre-check, session tagging, surrogate sanitize, stream callback,
  retry-counter reset, pre-turn connection health check.
* ``prepare_messages`` — formerly ``_prepare_messages``
  (conversation_loop.py:1051). Conversation copy, todo hydrate, nudge
  counters, user-turn tracking, user message append, turn-boundary
  marker.

Behavior-neutral: the bodies are moved unchanged. All ``agent.*`` side
effects fire exactly as before; only the module home changes. The
functions are synchronous with a single return — mirroring the regions
they replace.

Module ``logger`` is imported lazily inside the body
(``from agent.conversation_loop import logger``) so this module never
imports ``agent.conversation_loop`` at import time -> no import cycle,
and the log records keep the exact logger name (``"agent.conversation_loop"``).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from agent.auxiliary_client import set_runtime_main
from agent.codex_responses_adapter import _summarize_user_message_for_log
from agent.iteration_budget import IterationBudget
from agent.message_sanitization import _sanitize_surrogates
from agent.process_bootstrap import _install_safe_stdio
from vermes_logging import set_session_context
from tools.skill_provenance import set_current_write_origin


def initialize_turn(
    agent,
    user_message: str,
    persist_user_message: Optional[str],
    task_id: Optional[str],
    stream_callback: Optional[callable] = None,
) -> Tuple[str, Optional[str], str]:
    """Turn initialization — stdio guard, provider bind, task constraints,
    session tagging, surrogate sanitize, stream callback, retry reset, connection health.

    Returns (user_message, persist_user_message, effective_task_id).
    """
    from agent.conversation_loop import logger

    _install_safe_stdio()

    agent._ensure_db_session()

    # Tell auxiliary_client what the live main provider/model are for
    # this turn. Used by tools whose behaviour depends on the active
    # main model (e.g. vision_analyze's native fast path) so they see
    # the CLI/gateway override instead of the stale config.yaml
    # default. Idempotent - fine to call every turn.
    try:
        set_runtime_main(
            getattr(agent, "provider", "") or "",
            getattr(agent, "model", "") or "",
        )
    except Exception as e:
        # Fail-open: a runtime-main bind failure must never block the turn,
        # but it is now observable instead of silently swallowed.
        logger.debug("[harness] runtime-main bind failed (non-fatal): %s", e)

    # H1.1: task-level pre-execution constraints (fail-open, never blocks).
    # Runs after session is ensured but before any model call / tool dispatch.
    try:
        from harness.task_precheck import check_task_constraints
        _task_precheck = check_task_constraints(user_message, agent)
        if not _task_precheck.passed and _task_precheck.warning:
            logger.warning("[H1.1] task pre-check warning: %s", _task_precheck.warning)
            try:
                agent._emit_status(f"⚠️ [harness] {_task_precheck.warning}")
            except Exception as e:
                logger.debug("[harness] status emit failed (non-fatal): %s", e)
            # 融合 P0:任务级约束复发信号持久化(复用 H4.1 同款落库,fail-open)
            try:
                from harness.failure_learning import get_ledger
                for _check, _detail in (_task_precheck.detail or {}).items():
                    get_ledger().record(f"task:{_check}", _task_precheck.warning, _detail)
            except Exception as e:
                # Fail-open: learning persistence must not block the turn,
                # but a failed record is now observable (was silently swallowed).
                logger.warning("[harness] failure-learning record failed (non-fatal): %s", e)
    except Exception as e:
        # Fail-open: H1.1 must never block the agent loop - but the failure
        # is now observable in logs rather than silently discarded.
        logger.warning("[harness] H1.1 task pre-check failed (non-fatal, skipped): %s", e)

    # Tag all log records on this thread with the session ID so
    # ``Vermes logs --session <id>`` can filter a single conversation.
    set_session_context(agent.session_id)

    # Bind the skill write-origin ContextVar for this thread so tool
    # handlers (e.g. skill_manage create) can tell whether they are
    # running inside the background agent-improvement review fork vs.
    # a foreground user-directed turn. Set at the top of each call;
    # the review fork runs on its own thread with a fresh context,
    # so the foreground value here does not leak into it.
    set_current_write_origin(getattr(agent, "_memory_write_origin", "assistant_tool"))

    # If the previous turn activated fallback, restore the primary
    # runtime so this turn gets a fresh attempt with the preferred model.
    # No-op when _fallback_activated is False (gateway, first turn, etc.).
    agent._restore_primary_runtime()

    # Sanitize surrogate characters from user input.  Clipboard paste from
    # rich-text editors (Google Docs, Word, etc.) can inject lone surrogates
    # that are invalid UTF-8 and crash JSON serialization in the OpenAI SDK.
    if isinstance(user_message, str):
        user_message = _sanitize_surrogates(user_message)
    if isinstance(persist_user_message, str):
        persist_user_message = _sanitize_surrogates(persist_user_message)

    # Store stream callback for _interruptible_api_call to pick up
    agent._stream_callback = stream_callback
    agent._persist_user_message_idx = None
    agent._persist_user_message_override = persist_user_message
    # Generate unique task_id if not provided to isolate VMs between concurrent tasks
    effective_task_id = task_id or str(uuid.uuid4())
    # Expose the active task_id so tools running mid-turn (e.g. delegate_task
    # in delegate_tool.py) can identify this agent for the cross-agent file
    # state registry.  Set BEFORE any tool dispatch so snapshots taken at
    # child-launch time see the parent's real id, not None.
    agent._current_task_id = effective_task_id

    # Reset retry counters and iteration budget at the start of each turn
    # so subagent usage from a previous turn doesn't eat into the next one.
    agent._invalid_tool_retries = 0
    agent._invalid_json_retries = 0
    agent._empty_content_retries = 0
    agent._incomplete_scratchpad_retries = 0
    agent._codex_incomplete_retries = 0
    agent._thinking_prefill_retries = 0
    agent._post_tool_empty_retried = False
    agent._last_content_with_tools = None
    agent._last_content_tools_all_housekeeping = False
    agent._mute_post_response = False
    agent._unicode_sanitization_passes = 0
    agent._turn_tool_signatures = []  # 清空上回合的工具签名
    agent._operator_claim_rejection_count = getattr(agent, "_operator_claim_rejection_count", 0)  # 保持拒绝计数器
    agent._tool_guardrails.reset_for_turn()
    agent._tool_guardrail_halt_decision = None
    # True until the server rejects an image_url content part with an error
    # like "Only 'text' content type is supported."  Set to False on first
    # rejection and kept False for the rest of the session so we never re-send
    # images to a text-only endpoint.  Scoped per `_run()` call, not per instance.
    agent._vision_supported = True

    # Pre-turn connection health check: detect and clean up dead TCP
    # connections left over from provider outages or dropped streams.
    # This prevents the next API call from hanging on a zombie socket.
    if agent.api_mode != "anthropic_messages":
        try:
            if agent._cleanup_dead_connections():
                agent._emit_status(
                    "🔌 检测到上一个服务商遗留的失效连接 "
                    "—— 已自动清理，正在使用新连接继续。"
                )
        except Exception as e:
            logger.debug("conversation_loop.py:  initialize turn failed: %s", e)

    return user_message, persist_user_message, effective_task_id


def prepare_messages(
    agent,
    user_message: str,
    persist_user_message: Optional[str],
    conversation_history: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], str, int, bool]:
    """Message preparation — conversation copy, todo hydrate, nudge counters,
    user turn tracking, user message append, boundary marker.

    Returns (messages, original_user_message, current_turn_user_idx, _should_review_memory).
    """
    from agent.conversation_loop import logger

    # Replay compression warning through status_callback for gateway
    # platforms (the callback was not wired during __init__).
    if agent._compression_warning:
        agent._replay_compression_warning()
        agent._compression_warning = None  # send once

    # NOTE: _turns_since_memory and _iters_since_skill are NOT reset here.
    # They are initialized in __init__ and must persist across run_conversation
    # calls so that nudge logic accumulates correctly in CLI mode.
    agent.iteration_budget = IterationBudget(agent.max_iterations)

    # Log conversation turn start for debugging/observability
    _preview_text = _summarize_user_message_for_log(user_message)
    _msg_preview = (_preview_text[:80] + "...") if len(_preview_text) > 80 else _preview_text
    _msg_preview = _msg_preview.replace("\n", " ")
    logger.info(
        "conversation turn: session=%s model=%s provider=%s platform=%s history=%d msg=%r",
        agent.session_id or "none", agent.model, agent.provider or "unknown",
        agent.platform or "unknown", len(conversation_history or []),
        _msg_preview,
    )

    # Initialize conversation (copy to avoid mutating the caller's list)
    messages = list(conversation_history) if conversation_history else []

    # Hydrate todo store from conversation history (gateway creates a fresh
    # AIAgent per message, so the in-memory store is empty -- we need to
    # recover the todo state from the most recent todo tool response in history)
    if conversation_history and not agent._todo_store.has_items():
        agent._hydrate_todo_store(conversation_history)

    # Hydrate per-session nudge counters from persisted history.
    # Gateway creates a fresh AIAgent per inbound message (cache miss /
    # 1h idle eviction / config-signature mismatch / process restart), so
    # _turns_since_memory and _user_turn_count start at 0 every turn and
    # the memory.nudge_interval trigger may never be reached. Reconstruct
    # an effective count from prior user turns in conversation_history.
    # Idempotent: a cached agent that already accumulated counters keeps
    # them; only a freshly-built agent with empty in-memory state hydrates.
    # See issue #22357.
    if conversation_history and agent._user_turn_count == 0:
        prior_user_turns = sum(
            1 for m in conversation_history if m.get("role") == "user"
        )
        if prior_user_turns > 0:
            agent._user_turn_count = prior_user_turns
            if agent._memory_nudge_interval > 0 and agent._turns_since_memory == 0:
                # % preserves original 1-in-N cadence rather than firing a
                # review immediately on resume (which would surprise users
                # whose session happened to land just past a multiple of N).
                agent._turns_since_memory = prior_user_turns % agent._memory_nudge_interval

    # Prefill messages (few-shot priming) are injected at API-call time only,
    # never stored in the messages list. This keeps them ephemeral: they won't
    # be saved to session DB, session logs, or batch trajectories, but they're
    # automatically re-applied on every API call (including session continuations).

    # Track user turns for memory flush and periodic nudge logic
    agent._user_turn_count += 1

    # Reset the streaming context scrubber at the top of each turn so a
    # hung span from a prior interrupted stream can't taint this turn's
    # output.
    scrubber = getattr(agent, "_stream_context_scrubber", None)
    if scrubber is not None:
        scrubber.reset()
    # Reset the think scrubber for the same reason - an interrupted
    # prior stream may have left us inside an unterminated block.
    think_scrubber = getattr(agent, "_stream_think_scrubber", None)
    if think_scrubber is not None:
        think_scrubber.reset()

    # Preserve the original user message (no nudge injection).
    original_user_message = persist_user_message if persist_user_message is not None else user_message

    # Track memory nudge trigger (turn-based, checked here).
    # Skill trigger is checked AFTER the agent loop completes, based on
    # how many tool iterations THIS turn used.
    _should_review_memory = False
    if (agent._memory_nudge_interval > 0
            and "memory" in agent.valid_tool_names
            and agent._memory_store):
        agent._turns_since_memory += 1
        if agent._turns_since_memory >= agent._memory_nudge_interval:
            _should_review_memory = True
            agent._turns_since_memory = 0

    # Add user message
    user_msg = {"role": "user", "content": user_message}
    messages.append(user_msg)
    current_turn_user_idx = len(messages) - 1
    agent._persist_user_message_idx = current_turn_user_idx

    # ── 回合边界标记(抗"继续"后模型混淆)────────────────────────────
    # 如果上下文已有 3 对以上的 tool_call↔result 记录,说明这是多轮对话
    # 的"继续",模型容易把旧回合的工具调用误认成当前回合的操作。
    # 自动插入一个系统标记来分离边界,不加多余消耗。
    _tool_pairs = 0
    for _m in messages[:-1]:  # 不包括刚加的 user_msg
        if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("tool_calls"):
            _tool_pairs += 1
    if _tool_pairs >= 3:
        _boundary_note = (
            "\n\n[System: 这是新的一轮。前面列出的所有工具调用来自之前的回合。"
            "本轮如果需要执行操作,请重新调用相应的工具来执行,不要依赖之前回合的结果。"
            "不要在文本中声称「已完成」「已修改」「已安装」等操作,"
            "除非你真的在本轮调用了对应的工具。]"
        )
        # 把边界标记合并到用户消息末尾,而不是单独一条消息
        # 这样不增加消息数,模型也一定能看到
        messages[-1]["content"] = messages[-1]["content"] + _boundary_note

    if not agent.quiet_mode:
        _print_preview = _summarize_user_message_for_log(user_message)
        agent._safe_print(f"💬 Starting conversation: '{_print_preview[:60]}{'...' if len(_print_preview) > 60 else ''}'")

    return messages, original_user_message, current_turn_user_idx, _should_review_memory
