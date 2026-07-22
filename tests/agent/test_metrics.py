"""Tests for agent.metrics — in-process metrics collector."""

import time

import agent.metrics as metrics


class TestMetricsCollector:
    """Core metrics collection functionality."""

    def setup_method(self):
        """Reset state before each test."""
        metrics.get_state().reset()

    def test_initial_state_is_zero(self):
        s = metrics.get_state()
        assert s.sessions_created_total == 0
        assert s.active_sessions == 0
        assert s.turns_total == 0
        assert s.tool_calls_total == 0
        assert s.compressions_total == 0

    def test_session_lifecycle(self):
        metrics.record_session_created()
        assert metrics.get_state().active_sessions == 1
        assert metrics.get_state().sessions_created_total == 1

        metrics.record_session_created()
        assert metrics.get_state().active_sessions == 2
        assert metrics.get_state().sessions_created_total == 2

        metrics.record_session_closed()
        assert metrics.get_state().active_sessions == 1
        assert metrics.get_state().sessions_closed_total == 1

    def test_active_sessions_never_negative(self):
        metrics.record_session_closed()
        assert metrics.get_state().active_sessions == 0

    def test_turn_counter(self):
        for _ in range(5):
            metrics.record_turn_completed()
        assert metrics.get_state().turns_total == 5

    def test_tool_call_recording(self):
        metrics.record_tool_call("bash", 150.0)
        metrics.record_tool_call("bash", 250.0)
        metrics.record_tool_call("web_search", 300.0, error=True)

        s = metrics.get_state()
        assert s.tool_calls_total == 3
        assert s.tool_call_errors_total == 1
        assert len(s.tool_call_duration_ms["bash"]) == 2
        assert len(s.tool_call_duration_ms["web_search"]) == 1

    def test_tool_call_history_cap(self):
        for i in range(150):
            metrics.record_tool_call("test_tool", float(i))
        s = metrics.get_state()
        assert len(s.tool_call_duration_ms["test_tool"]) == 100

    def test_llm_call_recording(self):
        metrics.record_llm_call(500.0, prompt_tokens=100, completion_tokens=50)
        metrics.record_llm_call(800.0, prompt_tokens=200, completion_tokens=80, error=True)

        s = metrics.get_state()
        assert s.llm_calls_total == 2
        assert s.llm_call_errors_total == 1
        assert s.llm_tokens_prompt == 300
        assert s.llm_tokens_completion == 130
        assert len(s.llm_call_duration_ms) == 2

    def test_llm_call_history_cap(self):
        for _ in range(300):
            metrics.record_llm_call(100.0)
        s = metrics.get_state()
        assert len(s.llm_call_duration_ms) == 200

    def test_compression_counter(self):
        metrics.record_compression()
        metrics.record_compression()
        assert metrics.get_state().compressions_total == 2

    def test_fatigue_bridge_counter(self):
        metrics.record_fatigue_bridge()
        assert metrics.get_state().fatigue_bridges_total == 1

    def test_prune_counter(self):
        metrics.record_prune()
        metrics.record_prune()
        metrics.record_prune()
        assert metrics.get_state().prune_calls_total == 3

    def test_continuity_load_recording(self):
        metrics.record_continuity_load(
            sources_loaded=["handoff", "evolution"],
            sources_failed=["recall"],
        )
        s = metrics.get_state()
        assert s.continuity_loads_total == 1
        assert s.continuity_source_failures_total == 1

    def test_pipeline_stage_recording(self):
        metrics.record_pipeline_stage("topic")
        metrics.record_pipeline_stage("literature", error=True)
        s = metrics.get_state()
        assert s.pipeline_stages_total == 2
        assert s.pipeline_stage_failures_total == 1

    def test_error_recording(self):
        metrics.record_error("rate_limit")
        metrics.record_error("rate_limit")
        metrics.record_error("context_length")
        s = metrics.get_state()
        assert s.error_counts["rate_limit"] == 2
        assert s.error_counts["context_length"] == 1

    def test_reset_clears_all(self):
        metrics.record_session_created()
        metrics.record_tool_call("bash", 100.0)
        metrics.record_llm_call(500.0)
        metrics.record_compression()
        metrics.record_error("test")

        metrics.get_state().reset()

        s = metrics.get_state()
        assert s.sessions_created_total == 0
        assert s.tool_calls_total == 0
        assert s.llm_calls_total == 0
        assert s.compressions_total == 0
        assert len(s.error_counts) == 0


class TestPrometheusRendering:
    """Prometheus text format rendering."""

    def setup_method(self):
        metrics.get_state().reset()

    def test_render_empty_state(self):
        text = metrics.render_prometheus()
        assert "# HELP" in text
        assert "# TYPE" in text
        assert "vermes_uptime_seconds" in text
        assert "vermes_active_sessions 0" in text

    def test_render_with_data(self):
        metrics.record_session_created()
        metrics.record_turn_completed()
        metrics.record_tool_call("bash", 150.0)
        metrics.record_llm_call(500.0, prompt_tokens=100, completion_tokens=50)

        text = metrics.render_prometheus()
        assert "vermes_sessions_created_total 1" in text
        assert "vermes_turns_total 1" in text
        assert "vermes_tool_calls_total 1" in text
        assert "vermes_llm_calls_total 1" in text
        assert "vermes_llm_tokens_prompt_total 100" in text
        assert "vermes_llm_tokens_completion_total 50" in text

    def test_render_per_tool_metrics(self):
        metrics.record_tool_call("bash", 100.0)
        metrics.record_tool_call("bash", 200.0)
        metrics.record_tool_call("web_search", 300.0)

        text = metrics.render_prometheus()
        assert "vermes_tool_call_count_bash 2" in text
        assert "vermes_tool_call_count_web_search 1" in text
        assert "vermes_tool_call_avg_ms_bash 150.0" in text

    def test_render_error_categories(self):
        metrics.record_error("rate_limit")
        metrics.record_error("timeout")

        text = metrics.render_prometheus()
        assert "vermes_error_rate_limit 1" in text
        assert "vermes_error_timeout 1" in text

    def test_render_continuity_metrics(self):
        metrics.record_continuity_load(
            sources_loaded=["handoff"],
            sources_failed=["recall", "evolution"],
        )
        text = metrics.render_prometheus()
        assert "vermes_continuity_loads_total 1" in text
        assert "vermes_continuity_source_failures_total 2" in text

    def test_render_pipeline_metrics(self):
        metrics.record_pipeline_stage("topic")
        metrics.record_pipeline_stage("draft", error=True)
        text = metrics.render_prometheus()
        assert "vermes_pipeline_stages_total 2" in text
        assert "vermes_pipeline_stage_failures_total 1" in text

    def test_render_format_valid(self):
        """Output should have HELP/TYPE pairs followed by values."""
        metrics.record_session_created()
        text = metrics.render_prometheus()

        lines = text.strip().split("\n")
        # Every metric should have HELP and TYPE
        metric_names = set()
        for line in lines:
            if line.startswith("# HELP "):
                metric_names.add(line.split()[2])
            elif line.startswith("# TYPE "):
                assert line.split()[2] in metric_names
            elif line and not line.startswith("#"):
                # Value line: metric_name value
                parts = line.split()
                assert len(parts) >= 2
                assert parts[0] in metric_names

    def test_render_ends_with_newline(self):
        text = metrics.render_prometheus()
        assert text.endswith("\n")
