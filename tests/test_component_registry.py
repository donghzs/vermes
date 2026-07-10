"""Tests for agent.component_registry — harness component tracking.

Run: python -m pytest tests/test_component_registry.py -v
"""

import pytest

from agent.component_registry import (
    ComponentCategory,
    ComponentMeta,
    ComponentRegistry,
    get_registry,
    register_component,
    register_module,
)


# ---------------------------------------------------------------------------
# ComponentMeta tests
# ---------------------------------------------------------------------------


class TestComponentMeta:
    def test_basic_creation(self):
        meta = ComponentMeta(
            name="test",
            added_date="2025-01-01",
            added_for="Testing",
            model_dependent=False,
            removal_criteria="When tests pass without it",
            category=ComponentCategory.VALIDATOR,
        )
        assert meta.name == "test"
        assert meta.removable is True

    def test_permanent_not_removable(self):
        meta = ComponentMeta(
            name="infra",
            added_date="2025-01-01",
            added_for="Infrastructure",
            model_dependent=False,
            removal_criteria="permanent",
            category=ComponentCategory.INFRA,
        )
        assert meta.removable is False

    def test_empty_criteria_not_removable(self):
        meta = ComponentMeta(
            name="test",
            added_date="2025-01-01",
            added_for="Testing",
            model_dependent=False,
            removal_criteria="",
            category=ComponentCategory.VALIDATOR,
        )
        assert meta.removable is False

    def test_infra_not_removable(self):
        meta = ComponentMeta(
            name="core",
            added_date="2025-01-01",
            added_for="Core infra",
            model_dependent=False,
            removal_criteria="Some future condition",
            category=ComponentCategory.INFRA,
        )
        assert meta.removable is False  # INFRA is never removable


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestComponentRegistry:
    def test_register_and_get(self):
        registry = ComponentRegistry()
        meta = ComponentMeta(
            name="comp1",
            added_date="2025-01-01",
            added_for="Test component",
            model_dependent=False,
            removal_criteria="When done",
            category=ComponentCategory.VALIDATOR,
        )
        registry.register(meta)
        assert registry.get("comp1") is meta

    def test_get_nonexistent(self):
        registry = ComponentRegistry()
        assert registry.get("nonexistent") is None

    def test_overwrite_existing(self):
        registry = ComponentRegistry()
        meta1 = ComponentMeta(
            name="comp", added_date="2025-01-01", added_for="v1",
            model_dependent=False, removal_criteria="x",
            category=ComponentCategory.VALIDATOR,
        )
        meta2 = ComponentMeta(
            name="comp", added_date="2025-02-01", added_for="v2",
            model_dependent=False, removal_criteria="y",
            category=ComponentCategory.GUARDRAIL,
        )
        registry.register(meta1)
        registry.register(meta2)
        assert registry.get("comp").added_for == "v2"

    def test_list_by_category(self):
        registry = ComponentRegistry()
        for i in range(3):
            registry.register(ComponentMeta(
                name=f"val_{i}", added_date="2025-01-01",
                added_for=f"Validator {i}", model_dependent=False,
                removal_criteria="x", category=ComponentCategory.VALIDATOR,
            ))
        for i in range(2):
            registry.register(ComponentMeta(
                name=f"guard_{i}", added_date="2025-01-01",
                added_for=f"Guard {i}", model_dependent=False,
                removal_criteria="y", category=ComponentCategory.GUARDRAIL,
            ))
        assert len(registry.list_by_category(ComponentCategory.VALIDATOR)) == 3
        assert len(registry.list_by_category(ComponentCategory.GUARDRAIL)) == 2
        assert len(registry.list_by_category(ComponentCategory.INFRA)) == 0

    def test_list_removable(self):
        registry = ComponentRegistry()
        registry.register(ComponentMeta(
            name="removable", added_date="2025-01-01", added_for="Can remove",
            model_dependent=True, removal_criteria="Future",
            category=ComponentCategory.VALIDATOR,
        ))
        registry.register(ComponentMeta(
            name="permanent", added_date="2025-01-01", added_for="Keep forever",
            model_dependent=False, removal_criteria="permanent",
            category=ComponentCategory.GUARDRAIL,
        ))
        removable = registry.list_removable()
        assert len(removable) == 1
        assert removable[0].name == "removable"

    def test_list_model_dependent(self):
        registry = ComponentRegistry()
        registry.register(ComponentMeta(
            name="dep", added_date="2025-01-01", added_for="Model dep",
            model_dependent=True, removal_criteria="When models improve",
            category=ComponentCategory.VALIDATOR,
        ))
        registry.register(ComponentMeta(
            name="indep", added_date="2025-01-01", added_for="Model indep",
            model_dependent=False, removal_criteria="x",
            category=ComponentCategory.GUARDRAIL,
        ))
        model_dep = registry.list_model_dependent()
        assert len(model_dep) == 1
        assert model_dep[0].name == "dep"

    def test_count(self):
        registry = ComponentRegistry()
        assert registry.count == 0
        registry.register(ComponentMeta(
            name="x", added_date="2025-01-01", added_for="x",
            model_dependent=False, removal_criteria="x",
            category=ComponentCategory.TOOLING,
        ))
        assert registry.count == 1

    def test_summary_string(self):
        registry = ComponentRegistry()
        registry.register(ComponentMeta(
            name="comp_a", added_date="2025-01-01", added_for="Component A",
            model_dependent=False, removal_criteria="x",
            category=ComponentCategory.VALIDATOR,
        ))
        summary = registry.summary()
        assert "1 components" in summary
        assert "comp_a" in summary

    def test_to_dict_list(self):
        registry = ComponentRegistry()
        registry.register(ComponentMeta(
            name="comp", added_date="2025-01-01", added_for="Test",
            model_dependent=True, removal_criteria="Future",
            category=ComponentCategory.VALIDATOR,
            module_path="agent.test",
        ))
        dicts = registry.to_dict_list()
        assert len(dicts) == 1
        assert dicts[0]["name"] == "comp"
        assert dicts[0]["category"] == "validator"
        assert dicts[0]["model_dependent"] is True
        assert dicts[0]["removable"] is True


# ---------------------------------------------------------------------------
# Decorator tests
# ---------------------------------------------------------------------------


class TestDecorator:
    def test_register_component_decorator(self):
        registry = ComponentRegistry()

        # Temporarily replace global registry
        import agent.component_registry as cr
        old = cr._global_registry
        cr._global_registry = registry

        try:
            @register_component(
                name="decorated",
                added_date="2025-07-10",
                added_for="Decorator test",
                model_dependent=True,
                removal_criteria="Test done",
                category=ComponentCategory.VALIDATOR,
            )
            class MyClass:
                pass

            assert registry.get("decorated") is not None
            assert MyClass._component_meta.name == "decorated"
            assert MyClass._component_meta.model_dependent is True
        finally:
            cr._global_registry = old

    def test_register_module_function(self):
        registry = ComponentRegistry()

        import agent.component_registry as cr
        old = cr._global_registry
        cr._global_registry = registry

        try:
            register_module(
                name="mod_test",
                added_date="2025-07-10",
                added_for="Module registration test",
                model_dependent=False,
                removal_criteria="Test done",
                category=ComponentCategory.TOOLING,
                module_path="agent.test_module",
            )
            assert registry.get("mod_test") is not None
            assert registry.get("mod_test").module_path == "agent.test_module"
        finally:
            cr._global_registry = old


# ---------------------------------------------------------------------------
# Integration: _register_components
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_all_harness_components_registered(self):
        """Importing _register_components should populate the registry."""
        import agent._register_components  # noqa: F401

        registry = get_registry()
        assert registry.count >= 12  # 7 new + 5 pre-existing

    def test_expected_components_present(self):
        import agent._register_components  # noqa: F401

        registry = get_registry()
        expected = [
            "self_validator",
            "entropy_gardener",
            "vertical_validators",
            "veto_ledger",
            "isolated_workspace",
            "tool_param_validator",
            "quality_gate",
            "tool_guardrails",
            "error_classifier",
            "evolution_manager",
        ]
        for name in expected:
            comp = registry.get(name)
            assert comp is not None, f"missing component: {name}"
            assert comp.added_for, f"{name} has empty added_for"
            assert comp.category, f"{name} has no category"

    def test_summary_contains_all_components(self):
        import agent._register_components  # noqa: F401

        registry = get_registry()
        summary = registry.summary()
        assert "Component Registry" in summary
        assert "self_validator" in summary
        assert "veto_ledger" in summary

    def test_model_dependent_components(self):
        import agent._register_components  # noqa: F401

        registry = get_registry()
        model_dep = registry.list_model_dependent()
        # self_validator, vertical_validators, evolution_manager,
        # context_compressor, background_review are model-dependent
        dep_names = {c.name for c in model_dep}
        assert "self_validator" in dep_names
        assert "evolution_manager" in dep_names
        assert "veto_ledger" not in dep_names  # model-agnostic

    def test_removable_components(self):
        import agent._register_components  # noqa: F401

        registry = get_registry()
        removable = registry.list_removable()
        rem_names = {c.name for c in removable}
        # These are removable (have clear removal criteria, not INFRA)
        assert "self_validator" in rem_names
        assert "evolution_manager" in rem_names
        # These are NOT removable (permanent or INFRA)
        assert "veto_ledger" not in rem_names
        assert "error_classifier" not in rem_names  # INFRA

    def test_export_to_dict_list(self):
        import agent._register_components  # noqa: F401

        registry = get_registry()
        dicts = registry.to_dict_list()
        assert len(dicts) >= 12
        for d in dicts:
            assert "name" in d
            assert "added_date" in d
            assert "added_for" in d
            assert "category" in d
            assert "removable" in d
