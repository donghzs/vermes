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


class TestRealBuiltinLoad:
    """No mocking: verify the real built-in processor directory loads.

    This test catches path resolution bugs that mock-based tests cannot."""

    def test_builtin_dir_exists_and_resolves(self):
        invalidate_cache()
        from agent.prompt_processor_loader import _get_builtin_dir
        d = _get_builtin_dir()
        assert d.exists(), f"built-in processor dir missing: {d}"
        assert d.is_dir()

    def test_builtin_processors_load_at_least_30(self):
        invalidate_cache()
        procs = load_all_processors()
        assert len(procs) >= 30, f"expected >=30 built-in processors, got {len(procs)}"

    def test_critical_processors_present(self):
        invalidate_cache()
        procs = load_all_processors()
        names = {p.name for p in procs}
        # These are the north-star guidance blocks that must never disappear
        for required in ["identity", "memory_guidance", "skills_guidance",
                         "task_completion", "help_guidance"]:
            assert required in names, f"missing critical processor: {required}"

    def test_non_replaceable_identity_is_loaded(self):
        """identity must be loaded from built-in with replaceable=False
        so the override protection actually works."""
        invalidate_cache()
        procs = load_all_processors()
        ident = [p for p in procs if p.name == "identity"]
        assert len(ident) == 1
        assert ident[0].replaceable is False
        assert ident[0].builtin is True


class TestV1Schema:
    """Test v1 schema parsing with full fields."""

    def test_v1_full_schema_parse(self, tmp_path):
        """A v1 YAML with all fields should parse correctly."""
        import yaml
        data = {
            "api": "vermes.processor/v1",
            "kind": "prompt_fragment",
            "id": "test_v1_full",
            "name": "Test V1 Full",
            "version": "2.0.0",
            "enabled": True,
            "priority": 50,
            "layer": "stable",
            "model_affinity": {"operator": "any_of", "match": ["gpt-4", "claude"]},
            "conditions": {"require_tools": ["memory"], "platform": ["cli"]},
            "content": "Test content {{budget}}",
            "render": {"engine": "mustache", "on_missing": "keep", "inputs": {"budget": "context.budget"}},
            "governance": {"risk_tier": "L1", "replaceable": True, "mutable_by_aegis": True, "rollback": "enabled", "critic_guarded": False, "hash": "auto"},
            "lifecycle": {"hooks": ["on_session_start"]},
            "metadata": {"author": "test", "source": "user"},
        }
        f = tmp_path / "test_v1.yaml"
        f.write_text(yaml.dump(data))
        proc = _parse_yaml(f)
        assert proc is not None
        assert proc.api == "vermes.processor/v1"
        assert proc.kind == "prompt_fragment"
        assert proc.id == "test_v1_full"
        assert proc.effective_id == "test_v1_full"
        assert proc.priority == 50
        assert proc.effective_priority == 50
        assert proc.layer == "stable"
        assert proc.model_affinity["operator"] == "any_of"
        assert proc.conditions["require_tools"] == ["memory"]
        assert proc.render["engine"] == "mustache"
        assert proc.governance["risk_tier"] == "L1"
        assert proc.lifecycle["hooks"] == ["on_session_start"]
        assert proc.metadata["source"] == "user"

    def test_v0_compat_simple_yaml(self, tmp_path):
        """A v0 YAML (simple triggers) should still work."""
        import yaml
        data = {
            "name": "test_v0",
            "content": "Hello",
            "order": 100,
            "triggers": {"type": "always"},
            "replaceable": True,
        }
        f = tmp_path / "test_v0.yaml"
        f.write_text(yaml.dump(data))
        proc = _parse_yaml(f)
        assert proc is not None
        assert proc.name == "test_v0"
        assert proc.effective_id == "test_v0"  # falls back to name
        assert proc.effective_priority == 100  # falls back to order
        assert proc.kind == "prompt_fragment"  # default
        assert proc.layer == "stable"  # default
        assert proc.governance["risk_tier"] == "L2"  # default fail-closed
        assert proc.governance["replaceable"] is True  # from v0 top-level

    def test_reserved_kind_skipped(self, tmp_path):
        """behavior_rule and lifecycle_hook kinds are RESERVED (Phase 2)."""
        import yaml
        for kind in ("behavior_rule", "lifecycle_hook"):
            data = {"api": "vermes.processor/v1", "kind": kind, "name": f"test_{kind}", "content": "x"}
            f = tmp_path / f"test_{kind}.yaml"
            f.write_text(yaml.dump(data))
            assert _parse_yaml(f) is None, f"{kind} should be skipped"

    def test_invalid_api_rejected(self, tmp_path):
        """Unknown api major version should be rejected."""
        import yaml
        data = {"api": "vermes.processor/v2", "name": "test", "content": "x"}
        f = tmp_path / "test_bad_api.yaml"
        f.write_text(yaml.dump(data))
        assert _parse_yaml(f) is None

    def test_invalid_lifecycle_hook_warned(self, tmp_path):
        """Invalid lifecycle hooks should be warned and dropped, not crash."""
        import yaml
        data = {
            "api": "vermes.processor/v1",
            "kind": "prompt_fragment",
            "name": "test_bad_hook",
            "content": "x",
            "lifecycle": {"hooks": ["on_session_start", "nonexistent_hook"]},
        }
        f = tmp_path / "test_bad_hook.yaml"
        f.write_text(yaml.dump(data))
        proc = _parse_yaml(f)
        assert proc is not None
        assert "on_session_start" in proc.lifecycle["hooks"]
        assert "nonexistent_hook" not in proc.lifecycle["hooks"]

    def test_override_by_id_not_filename(self, tmp_path):
        """User processor overrides built-in by effective_id, not filename."""
        import yaml
        # Built-in: name=memory_guidance, no explicit id → effective_id=memory_guidance
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        (builtin / "memory_guidance.yaml").write_text(yaml.dump({
            "name": "memory_guidance", "content": "BUILTIN", "order": 40, "replaceable": True,
        }))
        # User: different filename, same id → should override
        user = tmp_path / "user" / "memory_guidance_override" / "processor.yaml"
        user.parent.mkdir(parents=True)
        user.write_text(yaml.dump({
            "api": "vermes.processor/v1", "kind": "prompt_fragment",
            "id": "memory_guidance", "name": "My Memory Guidance",
            "content": "USER OVERRIDE", "governance": {"risk_tier": "L1", "replaceable": True},
        }))
        with patch("agent.prompt_processor_loader._get_builtin_dir", return_value=builtin), \
             patch("agent.prompt_processor_loader._get_user_dir", return_value=tmp_path / "user"):
            invalidate_cache()
            procs = load_all_processors()
            mg = [p for p in procs if p.effective_id == "memory_guidance"]
            assert len(mg) == 1
            assert mg[0].content == "USER OVERRIDE"
            assert mg[0].builtin is False


class TestMustacheRender:
    """Test the minimal mustache renderer."""

    def test_no_engine_returns_raw(self):
        from agent.prompt_processor_loader import _mustache_render
        assert _mustache_render("hello {{name}}", {}, {}, "keep") == "hello {{name}}"

    def test_simple_substitution(self):
        from agent.prompt_processor_loader import _mustache_render
        result = _mustache_render("hello {{name}}", {"name": "context.name"}, {"context": {"name": "world"}}, "keep")
        assert result == "hello world"

    def test_missing_keep(self):
        from agent.prompt_processor_loader import _mustache_render
        result = _mustache_render("hello {{name}}", {"name": "context.name"}, {"context": {}}, "keep")
        assert result == "hello {{name}}"

    def test_missing_empty(self):
        from agent.prompt_processor_loader import _mustache_render
        result = _mustache_render("hello {{name}}", {"name": "context.name"}, {"context": {}}, "empty")
        assert result == "hello "

    def test_missing_error(self):
        from agent.prompt_processor_loader import _mustache_render
        import pytest
        with pytest.raises(KeyError):
            _mustache_render("hello {{name}}", {"name": "context.name"}, {"context": {}}, "error")

    def test_no_html_escape(self):
        from agent.prompt_processor_loader import _mustache_render
        result = _mustache_render("{{x}}", {"x": "context.x"}, {"context": {"x": "a&b"}}, "keep")
        assert result == "a&b"  # NOT a&amp;b

    def test_nested_path(self):
        from agent.prompt_processor_loader import _mustache_render
        result = _mustache_render("{{budget}}", {"budget": "ctx.mem.budget"}, {"ctx": {"mem": {"budget": 1000}}}, "keep")
        assert result == "1000"


class TestProcessorClassify:
    """Test classify_component_swap with processor_hot_path."""

    def test_processor_yaml_is_processor_hot_path(self, tmp_path, monkeypatch):
        from tools.approval import classify_component_swap
        from agent.prompt_processor_loader import is_processor_hot_path, _get_user_dir
        # Use VERMES_HOME to construct path (conftest redirects it)
        user_dir = _get_user_dir()
        path = str(user_dir / "test" / "processor.yaml")
        assert is_processor_hot_path(path) is True
        assert classify_component_swap(path) == "processor_hot_path"

    def test_processor_flat_yaml_is_processor_hot_path(self, tmp_path, monkeypatch):
        from tools.approval import classify_component_swap
        from agent.prompt_processor_loader import _get_user_dir
        user_dir = _get_user_dir()
        path = str(user_dir / "test.yaml")
        assert classify_component_swap(path) == "processor_hot_path"

    def test_config_yaml_still_config_level(self):
        from tools.approval import classify_component_swap
        from agent.prompt_processor_loader import _get_user_dir
        # config.yaml is in ~/.vermes/ not ~/.vermes/processors/
        user_dir = _get_user_dir()
        config_path = str(user_dir.parent / "config.yaml")
        assert classify_component_swap(config_path) == "config_level"

    def test_frozen_py_still_source_level(self):
        from tools.approval import classify_component_swap
        path = "/Applications/Vermes.app/Contents/Resources/backend/_internal/agent/foo.py"
        assert classify_component_swap(path) == "source_level"
