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

# ---------------------------------------------------------------------------
# Phase 5: Emergent memory & evolution framework (2026-07)
# ---------------------------------------------------------------------------

_EMERGENCE_MODULES = [
    ("raw_event", "2026-07-12", "Zero-classification event recording — all tool executions stored as raw facts", False, ComponentCategory.EVOLUTION),
    ("emergent_clusterer", "2026-07-12", "Pure Python DBSCAN clustering of raw events — no preset categories", False, ComponentCategory.EVOLUTION),
    ("cluster_lifecycle", "2026-07-12", "Cluster state machine: emerging → stable → declining → dormant → dead", False, ComponentCategory.EVOLUTION),
    ("emergent_insight", "2026-07-12", "Generate insights from cluster statistics — purely data-driven", True, ComponentCategory.EVOLUTION),
    ("domain_modules", "2026-07-12", "Vertical domain modules emerged from cluster patterns — hot-pluggable", False, ComponentCategory.EVOLUTION),
    ("cross_session_continuity", "2026-07-12", "Snapshot cluster state at session end, brief evolution at session start", False, ComponentCategory.EVOLUTION),
    ("self_assessment", "2026-07-14", "Context richness scoring — data-driven signal for compression and emergence", False, ComponentCategory.OBSERVABILITY),
    ("capability_evolver", "2026-07-14", "Emergence decision engine — observes signals and activates capabilities", True, ComponentCategory.EVOLUTION),
    ("skill_extractor", "2026-07-14", "Extract skills from repetitive clusters — user confirms before activation", False, ComponentCategory.EVOLUTION),
    ("graph_sync", "2026-07-14", "Knowledge graph export/import in GraphJSON format", False, ComponentCategory.INFRA),
    ("memory_recall", "2026-07-12", "Auto-retrieve relevant memory for current user message", True, ComponentCategory.EVOLUTION),
    ("memory_budget", "2026-07-12", "Unified token budget across all memory injections", False, ComponentCategory.INFRA),
    ("hybrid_retriever", "2026-07-12", "Embedding-based retrieval with Jaccard fallback — follows user's provider config", True, ComponentCategory.INFRA),
    ("session_handoff", "2026-07-12", "Generate session summary at end, inject at next session start", True, ComponentCategory.EVOLUTION),
    ("evolution_injector", "2026-07-12", "Inject learned experience from past sessions into system prompt", True, ComponentCategory.EVOLUTION),
    ("decision_tracker", "2026-07-12", "Track standing decisions across sessions, detect contradictions", False, ComponentCategory.EVOLUTION),
    ("compression_scheduler", "2026-07-15", "Proactive compression: cache-aware, richness-aware depth, decision-point cleanup", True, ComponentCategory.INFRA),
]

for _name, _date, _for, _model_dep, _cat in _EMERGENCE_MODULES:
    register_module(
        name=_name,
        added_date=_date,
        added_for=_for,
        model_dependent=_model_dep,
        removal_criteria="Permanent" if not _model_dep else "When models handle this natively",
        category=_cat,
        module_path=f"agent.{_name}",
    )
