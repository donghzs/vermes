"""Tests for Phase 1: Prompt Processor YAML loader.

Covers: loading, user override, trigger evaluation, cache invalidation,
hot-reload generation bump, and fallback behavior.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# We need to import the loader fresh for each test since it has a cache
from agent.prompt_processor_loader import (
    PromptProcessor,
    load_all_processors,
    invalidate_cache,
    get_generation,
    _parse_yaml,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear processor cache before each test."""
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
def tmp_processors(tmp_path):
    """Create a temporary processors directory with test YAML files."""
    proc_dir = tmp_path / "processors"
    proc_dir.mkdir()

    # Always-injected processor
    (proc_dir / "identity.yaml").write_text(
        "processor_type: prompt\n"
        "name: identity\n"
        "version: '1.0.0'\n"
        "description: Test identity\n"
        "triggers:\n"
        "  type: always\n"
        "content: 'You are a test agent.'\n"
        "order: 10\n"
        "replaceable: false\n",
        encoding="utf-8",
    )

    # Tool-present processor
    (proc_dir / "memory.yaml").write_text(
        "processor_type: prompt\n"
        "name: memory_guidance\n"
        "version: '1.0.0'\n"
        "description: Memory guidance\n"
        "triggers:\n"
        "  type: tool_present\n"
        "  tools:\n"
        "    - memory\n"
        "content: 'Use memory tool to save facts.'\n"
        "order: 40\n"
        "replaceable: true\n",
        encoding="utf-8",
    )

    # Model-match processor
    (proc_dir / "google.yaml").write_text(
        "processor_type: prompt\n"
        "name: google_model\n"
        "version: '1.0.0'\n"
        "description: Google model guidance\n"
        "triggers:\n"
        "  type: model_match\n"
        "  patterns:\n"
        "    - gemini\n"
        "    - gemma\n"
        "content: 'Be concise and use absolute paths.'\n"
        "order: 120\n"
        "replaceable: true\n",
        encoding="utf-8",
    )

    return proc_dir


class TestParseYaml:
    def test_parse_valid_yaml(self, tmp_processors):
        yaml_file = tmp_processors / "identity.yaml"
        proc = _parse_yaml(yaml_file)
        assert proc is not None
        assert proc.name == "identity"
        assert proc.content == "You are a test agent."
        assert proc.order == 10
        assert proc.replaceable is False
        assert proc.triggers["type"] == "always"

    def test_parse_nonexistent(self, tmp_path):
        assert _parse_yaml(tmp_path / "nonexistent.yaml") is None

    def test_parse_empty_content(self, tmp_path):
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text(
            "name: empty\ntriggers:\n  type: always\ncontent: ''\n",
            encoding="utf-8",
        )
        assert _parse_yaml(yaml_file) is None


class TestLoadAllProcessors:
    def test_load_from_builtin_dir(self, tmp_processors):
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=Path("/nonexistent"),
            ):
                procs = load_all_processors()
        assert len(procs) == 3
        assert procs[0].name == "identity"  # order=10
        assert procs[1].name == "memory_guidance"  # order=40
        assert procs[2].name == "google_model"  # order=120

    def test_user_overrides_builtin(self, tmp_processors):
        user_dir = tmp_processors.parent / "user_processors"
        user_dir.mkdir()
        # Override memory_guidance
        (user_dir / "memory.yaml").write_text(
            "processor_type: prompt\n"
            "name: memory_guidance\n"
            "content: 'Override: use memory wisely.'\n"
            "triggers:\n  type: tool_present\n  tools:\n    - memory\n"
            "order: 40\n"
            "replaceable: true\n",
            encoding="utf-8",
        )
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=user_dir,
            ):
                procs = load_all_processors()

        mem = [p for p in procs if p.name == "memory_guidance"][0]
        assert mem.content == "Override: use memory wisely."
        assert mem.builtin is False

    def test_non_replaceable_not_overridden(self, tmp_processors):
        user_dir = tmp_processors.parent / "user_processors"
        user_dir.mkdir()
        # Try to override identity (replaceable=False)
        (user_dir / "identity.yaml").write_text(
            "processor_type: prompt\n"
            "name: identity\n"
            "content: 'HACKED'\n"
            "triggers:\n  type: always\n"
            "order: 10\n"
            "replaceable: true\n",
            encoding="utf-8",
        )
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=user_dir,
            ):
                procs = load_all_processors()

        ident = [p for p in procs if p.name == "identity"][0]
        assert ident.content == "You are a test agent."
        assert ident.builtin is True

    def test_cache_works(self, tmp_processors):
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=Path("/nonexistent"),
            ):
                p1 = load_all_processors()
                p2 = load_all_processors()
        assert p1 is p2  # Same cached list

    def test_invalidate_cache(self, tmp_processors):
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=Path("/nonexistent"),
            ):
                p1 = load_all_processors()
                invalidate_cache()
                p2 = load_all_processors()
        assert p1 is not p2  # Different instances after invalidation

    def test_generation_bumps_on_invalidate(self):
        gen1 = get_generation()
        invalidate_cache()
        gen2 = get_generation()
        assert gen2 > gen1


class TestTriggerEvaluation:
    def test_always_trigger(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "always"}, replaceable=True,
        )
        agent = MagicMock()
        assert proc.should_inject(agent) is True

    def test_tool_present_match(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "tool_present", "tools": ["memory"]},
            replaceable=True,
        )
        agent = MagicMock()
        agent.valid_tool_names = {"memory", "chat"}
        assert proc.should_inject(agent) is True

    def test_tool_present_no_match(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "tool_present", "tools": ["memory"]},
            replaceable=True,
        )
        agent = MagicMock()
        agent.valid_tool_names = {"chat", "search"}
        assert proc.should_inject(agent) is False

    def test_model_match(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "model_match", "patterns": ["gemini", "gemma"]},
            replaceable=True,
        )
        agent = MagicMock()
        agent.model = "google/gemini-2.0-flash"
        assert proc.should_inject(agent) is True

        agent.model = "openai/gpt-4"
        assert proc.should_inject(agent) is False

    def test_provider_match(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "provider_match", "value": "alibaba"},
            replaceable=True,
        )
        agent = MagicMock()
        agent.provider = "alibaba"
        assert proc.should_inject(agent) is True

        agent.provider = "openai"
        assert proc.should_inject(agent) is False

    def test_env_var_trigger(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "env_var", "var": "VERMES_KANBAN_TASK"},
            replaceable=True,
        )
        with patch.dict(os.environ, {"VERMES_KANBAN_TASK": "1"}):
            agent = MagicMock()
            assert proc.should_inject(agent) is True

        with patch.dict(os.environ, {}, clear=True):
            agent = MagicMock()
            assert proc.should_inject(agent) is False

    def test_unknown_trigger_type(self):
        proc = PromptProcessor(
            name="test", content="x", order=1,
            triggers={"type": "nonexistent"},
            replaceable=True,
        )
        agent = MagicMock()
        assert proc.should_inject(agent) is False


class TestFallbackInSystemPrompt:
    """Verify that _get_processor returns None gracefully when
    processors aren't available, so the hardcoded constants remain
    as fallback."""

    def test_get_processor_returns_none_when_empty(self):
        invalidate_cache()
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=Path("/nonexistent"),
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=Path("/nonexistent"),
            ):
                from agent.system_prompt import _get_processor
                assert _get_processor("identity") is None

    def test_get_processor_returns_content_when_available(self, tmp_processors):
        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=Path("/nonexistent"),
            ):
                invalidate_cache()
                from agent.system_prompt import _get_processor
                result = _get_processor("identity")
                assert result == "You are a test agent."


class TestProcessorWatcher:
    """Tests for the lightweight processor directory watcher."""

    def test_watcher_detects_new_file(self, tmp_processors):
        import time
        from agent.prompt_processor_loader import (
            start_processor_watcher,
            stop_processor_watcher,
            invalidate_cache,
        )

        user_dir = tmp_processors.parent / "user_procs"
        user_dir.mkdir()

        with patch(
            "agent.prompt_processor_loader._get_builtin_dir",
            return_value=tmp_processors,
        ):
            with patch(
                "agent.prompt_processor_loader._get_user_dir",
                return_value=user_dir,
            ):
                invalidate_cache()
                load_all_processors()  # Initial load
                start_processor_watcher(poll_interval=0.1)

                # Wait for initial scan
                time.sleep(0.5)

                # Add a new processor file
                (user_dir / "new_proc.yaml").write_text(
                    "name: new_proc\ncontent: 'New!'\n"
                    "triggers:\n  type: always\norder: 5\nreplaceable: true\n",
                    encoding="utf-8",
                )

                # Wait for watcher to pick it up (poll=0.1 + processing)
                time.sleep(1.0)

                stop_processor_watcher()

                # Cache should have been invalidated
                procs = load_all_processors()
                names = [p.name for p in procs]
                assert "new_proc" in names

    def test_watcher_lifecycle(self):
        from agent.prompt_processor_loader import (
            start_processor_watcher,
            stop_processor_watcher,
        )

        start_processor_watcher(poll_interval=0.1)
        stop_processor_watcher()
        # Should be cleanly stopped, can restart
        start_processor_watcher(poll_interval=0.1)
        stop_processor_watcher()
