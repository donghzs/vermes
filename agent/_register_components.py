"""Register all Vermes harness components.

This module is imported for side effects — it registers every
Vermes-specific harness component in the component registry.

Import this once at startup (e.g., in agent/__init__.py) to
populate the registry::

    from agent import _register_components  # noqa: F401
"""

from agent.component_registry import (
    register_module,
    ComponentCategory,
)

# ---------------------------------------------------------------------------
# Phase 1: Self-validation
# ---------------------------------------------------------------------------

register_module(
    name="self_validator",
    added_date="2025-07-10",
    added_for="Detect false-positive tool completions (fake success)",
    model_dependent=True,
    removal_criteria="When models self-verify natively > 95% accuracy",
    category=ComponentCategory.VALIDATOR,
    module_path="agent.self_validator",
)

# ---------------------------------------------------------------------------
# Phase 2: Entropy governance
# ---------------------------------------------------------------------------

register_module(
    name="entropy_gardener",
    added_date="2025-07-10",
    added_for="Track code quality debt (print, bare-except, large functions)",
    model_dependent=False,
    removal_criteria="Permanent — code quality monitoring is always needed",
    category=ComponentCategory.GARDENER,
    module_path="agent.entropy_gardener",
)

register_module(
    name="vertical_validators",
    added_date="2025-07-10",
    added_for="Domain-specific validators for ScholarForge and Studio",
    model_dependent=True,
    removal_criteria="When vertical ecosystems are retired or merged",
    category=ComponentCategory.VERTICAL,
    module_path="agent.vertical_validators",
)

# ---------------------------------------------------------------------------
# Phase 3: Veto + Isolation
# ---------------------------------------------------------------------------

register_module(
    name="veto_ledger",
    added_date="2025-07-10",
    added_for="Track consecutive tool failures and auto-pause after 3",
    model_dependent=False,
    removal_criteria="Permanent — loop prevention is model-agnostic",
    category=ComponentCategory.GUARDRAIL,
    module_path="agent.veto_ledger",
)

register_module(
    name="isolated_workspace",
    added_date="2025-07-10",
    added_for="Staging area for safe code modifications before commit",
    model_dependent=False,
    removal_criteria="Permanent — isolation is a safety primitive",
    category=ComponentCategory.ISOLATION,
    module_path="agent.isolated_workspace",
)

# ---------------------------------------------------------------------------
# Phase 4: Architecture constraints
# ---------------------------------------------------------------------------

register_module(
    name="tool_param_validator",
    added_date="2025-07-10",
    added_for="Lightweight parameter type checking before tool execution",
    model_dependent=False,
    removal_criteria="When tool schemas are enforced at framework level",
    category=ComponentCategory.VALIDATOR,
    module_path="agent.tool_param_validator",
)

register_module(
    name="quality_gate",
    added_date="2025-07-10",
    added_for="CI threshold enforcement to prevent code quality degradation",
    model_dependent=False,
    removal_criteria="Permanent — anti-degradation is always needed",
    category=ComponentCategory.GARDENER,
    module_path="scripts.quality_gate",
)

# ---------------------------------------------------------------------------
# Pre-existing harness components
# ---------------------------------------------------------------------------

register_module(
    name="tool_guardrails",
    added_date="2025-06-15",
    added_for="Tool loop detection and prevention (repeated failures)",
    model_dependent=False,
    removal_criteria="Permanent — loop prevention is model-agnostic",
    category=ComponentCategory.GUARDRAIL,
    module_path="agent.tool_guardrails",
)

register_module(
    name="error_classifier",
    added_date="2025-06-01",
    added_for="Classify API errors for smart failover and recovery",
    model_dependent=False,
    removal_criteria="Permanent — error classification is infrastructure",
    category=ComponentCategory.INFRA,
    module_path="agent.error_classifier",
)

register_module(
    name="evolution_manager",
    added_date="2025-06-01",
    added_for="Self-evolution: record tool outcomes, learn from patterns",
    model_dependent=True,
    removal_criteria="When models can self-evolve without external tracking",
    category=ComponentCategory.EVOLUTION,
    module_path="agent.evolution_manager",
)

register_module(
    name="context_compressor",
    added_date="2025-06-01",
    added_for="Compress conversation context to fit model window",
    model_dependent=True,
    removal_criteria="When models have infinite context windows",
    category=ComponentCategory.INFRA,
    module_path="agent.conversation_compression",
)

register_module(
    name="background_review",
    added_date="2025-06-10",
    added_for="Background fork review of memory and skills",
    model_dependent=True,
    removal_criteria="When models can self-review in-context",
    category=ComponentCategory.OBSERVABILITY,
    module_path="agent.background_review",
)
