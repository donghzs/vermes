"""Regression tests for two pre-existing bugs in _finalize_turn (fixed in this commit).

Bug #1: approx_tokens always 0 — _finalize_turn used ``approx_tokens if 'approx_tokens' in dir() else 0``
        but approx_tokens was never a local variable, so dir() never contained it.
        Fix: added approx_tokens as an explicit parameter (default 0), caller passes real value.

Bug #2: auto-retry NameError — the auto-retry block referenced system_message/task_id/
        stream_callback/persist_user_message which are run_conversation parameters, not
        _finalize_turn parameters. Any trigger would NameError into a swallowed warning.
        Fix: moved auto-retry logic from _finalize_turn to run_conversation where those
        variables are in scope.
"""

import pytest
from unittest.mock import MagicMock, patch
import inspect


class TestBug1ApproxTokensParam:
    """Bug #1: approx_tokens must be a real parameter, not a dir() lookup."""

    def test_approx_tokens_is_function_parameter(self):
        from agent.conversation_loop import _finalize_turn
        sig = inspect.signature(_finalize_turn)
        assert "approx_tokens" in sig.parameters, \
            "approx_tokens must be an explicit parameter of _finalize_turn"
        assert sig.parameters["approx_tokens"].default == 0, \
            "approx_tokens should default to 0 for backward compatibility"

    def test_no_dir_hack_remains(self):
        """The ``if 'approx_tokens' in dir()`` hack must be gone."""
        import agent.conversation_loop as mod
        source = inspect.getsource(mod._finalize_turn)
        assert "dir()" not in source, \
            "dir() hack must be removed — it always evaluates to False in _finalize_turn scope"

    def test_nonzero_approx_tokens_propagates_to_metrics(self):
        """When approx_tokens > 0, _record_turn_metrics must receive it."""
        from agent.conversation_loop import _finalize_turn

        agent = MagicMock()
        agent.max_iterations = 10
        agent.iteration_budget.remaining = 5
        agent.quiet_mode = True
        agent._save_trajectory = MagicMock()
        agent._cleanup_task_resources = MagicMock()
        agent._drop_trailing_empty_response_scaffolding = MagicMock()
        agent._persist_session = MagicMock()
        agent.session_id = "test-session"
        agent.model = "test-model"
        agent.provider = "test-provider"
        agent.base_url = "http://test"
        agent.session_input_tokens = 100
        agent.session_output_tokens = 50
        agent.session_cache_read_tokens = 0
        agent.session_cache_write_tokens = 0
        agent.session_reasoning_tokens = 0
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 150
        agent.session_estimated_cost_usd = 0.0
        agent.session_cost_status = "ok"
        agent.session_cost_source = "test"
        agent._tool_guardrail_halt_decision = None
        agent._drain_pending_steer = MagicMock(return_value=None)
        agent._response_was_previewed = False
        agent._skill_nudge_interval = 0
        agent._iters_since_skill = 0
        agent._sync_external_memory_for_turn = MagicMock()
        agent._spawn_background_review = MagicMock()
        agent.clear_interrupt = MagicMock()
        agent._stream_callback = None
        agent.platform = "test"
        agent._current_turn_tool_names = []
        agent._last_turn_tool_names = []
        agent.context_compressor = MagicMock()
        agent.context_compressor.last_prompt_tokens = 200

        scheduler = MagicMock()
        scheduler.evaluate = MagicMock(return_value=MagicMock(mode="none"))

        captured_kwargs = {}
        def mock_record_metrics(ag, msgs, sched, start_time, approx_tokens):
            captured_kwargs["approx_tokens"] = approx_tokens
        with patch("agent.conversation_loop._record_turn_metrics", side_effect=mock_record_metrics):
            with patch("agent.conversation_loop._apply_file_mutation_footer", return_value="done"):
                with patch("agent.conversation_loop._apply_operator_claim_verifier", return_value=("done", [])):
                    with patch("agent.conversation_loop._run_post_llm_hooks"):
                        with patch("agent.conversation_loop._build_conversation_result", return_value={"final_response": "done", "messages": []}):
                            with patch("agent.conversation_loop._log_turn_exit"):
                                _finalize_turn(
                                    agent=agent,
                                    messages=[{"role": "user", "content": "hi"}],
                                    final_response="done",
                                    interrupted=False,
                                    api_call_count=1,
                                    effective_task_id="task-1",
                                    _turn_exit_reason="normal",
                                    _scheduler=scheduler,
                                    api_start_time=0,
                                    user_message="hi",
                                    original_user_message="hi",
                                    _should_review_memory=False,
                                    conversation_history=None,
                                    approx_tokens=42,
                                )

        assert captured_kwargs["approx_tokens"] == 42, \
            "approx_tokens=42 must propagate to _record_turn_metrics, not be 0"


class TestBug2AutoRetryMovedToRunConversation:
    """Bug #2: auto-retry must be in run_conversation, not _finalize_turn."""

    def test_finalize_turn_does_not_call_run_conversation(self):
        """_finalize_turn must not contain run_conversation calls (auto-retry moved out)."""
        import agent.conversation_loop as mod
        source = inspect.getsource(mod._finalize_turn)
        # The function docstring or comments may mention run_conversation,
        # but there should be no actual call to it.
        assert "run_conversation(" not in source or source.count("run_conversation(") == 0, \
            "_finalize_turn must not call run_conversation — auto-retry moved to run_conversation"

    def test_finalize_turn_does_not_reference_retry_params(self):
        """_finalize_turn must not reference system_message/task_id/stream_callback/persist_user_message
        as they are run_conversation parameters (the NameError source)."""
        import agent.conversation_loop as mod
        source = inspect.getsource(mod._finalize_turn)
        # These are NOT _finalize_turn parameters — if referenced, it's the bug.
        # Note: user_message IS a _finalize_turn parameter, so we don't check that.
        for name in ["system_message", "task_id", "stream_callback", "persist_user_message"]:
            # Check for bare Name references (not string literals)
            import re
            # Match identifier not preceded by . or " or '
            pattern = rf'(?<![.\\"\']\w){re.escape(name)}(?!\w)'
            matches = re.findall(pattern, source)
            # Filter out string literals and comments
            real_refs = [m for m in matches if f'"{name}"' not in source and f"'{name}'" not in source]
            assert len(real_refs) == 0, \
                f"_finalize_turn must not reference '{name}' — it's a run_conversation parameter (NameError bug)"
