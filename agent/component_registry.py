"""Component registry — track why each module exists and whether it can be removed.

Provides decorators and a central registry so that every Vermes-specific
component (harness module, validator, guardrail, etc.) is annotated with:

- **added_date**: when it was introduced
- **added_for**: what problem it solves
- **model_dependent**: whether it relies on LLM behaviour (can be removed
  if the model becomes smart enough)
- **removal_criteria**: when this component can be safely deleted
- **category**: harness layer (validator, guardrail, gardener, etc.)

This makes it easy to:
1. Audit what's Vermes-specific vs upstream Vermes
2. Identify components that can be removed as the model improves
3. Track the growth of the harness layer over time

Usage
-----
::

    from agent.component_registry import register_component, ComponentCategory

    @register_component(
        name="self_validator",
        added_date="2025-07-10",
        added_for="Detect false-positive tool completions",
        model_dependent=True,
        removal_criteria="When models self-verify natively > 95% accuracy",
        category=ComponentCategory.VALIDATOR,
    )
    class SelfValidator: ...

Query
-----
::

    from agent.component_registry import get_registry
    registry = get_registry()
    print(registry.summary())
    for c in registry.components:
        print(f"{c.name}: {c.added_for}")
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger("vermes.component_registry")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ComponentCategory(enum.Enum):
    """Category of a harness component."""
    VALIDATOR = "validator"          # Self-validation, param validation
    GUARDRAIL = "guardrail"          # Tool loop prevention, veto ledger
    GARDENER = "gardener"            # Entropy/quality scanning
    ISOLATION = "isolation"          # Workspace isolation
    EVOLUTION = "evolution"          # Self-evolution system
    OBSERVABILITY = "observability"  # Logging, metrics, tracing
    TOOLING = "tooling"              # Tool enhancements
    VERTICAL = "vertical"            # Domain-specific (ScholarForge, Studio)
    INFRA = "infrastructure"         # Core infrastructure


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ComponentMeta:
    """Metadata for a registered harness component."""
    name: str
    added_date: str
    added_for: str
    model_dependent: bool
    removal_criteria: str
    category: ComponentCategory
    module_path: str = ""
    removable: bool = field(init=False)

    def __post_init__(self) -> None:
        # A component is removable if it has clear removal criteria
        # and is not infrastructure
        self.removable = (
            bool(self.removal_criteria)
            and self.removal_criteria.strip().lower() not in ("", )
            and not self.removal_criteria.strip().lower().startswith(("never", "permanent", "always", "forever"))
            and self.category != ComponentCategory.INFRA
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ComponentRegistry:
    """Central registry for harness components."""

    def __init__(self) -> None:
        self._components: dict[str, ComponentMeta] = {}

    def register(self, meta: ComponentMeta) -> None:
        """Register a component. Overwrites if name already exists."""
        if meta.name in self._components:
            logger.debug("overwriting existing component: %s", meta.name)
        self._components[meta.name] = meta

    def get(self, name: str) -> ComponentMeta | None:
        """Get a component by name."""
        return self._components.get(name)

    def list_by_category(self, category: ComponentCategory) -> list[ComponentMeta]:
        """List all components in a category."""
        return [c for c in self._components.values() if c.category == category]

    def list_removable(self) -> list[ComponentMeta]:
        """List all components that can be removed."""
        return [c for c in self._components.values() if c.removable]

    def list_model_dependent(self) -> list[ComponentMeta]:
        """List components that depend on LLM behaviour."""
        return [c for c in self._components.values() if c.model_dependent]

    @property
    def components(self) -> list[ComponentMeta]:
        """All registered components, sorted by name."""
        return sorted(self._components.values(), key=lambda c: c.name)

    @property
    def count(self) -> int:
        """Total number of registered components."""
        return len(self._components)

    def summary(self) -> str:
        """Human-readable summary of the registry."""
        removable = self.list_removable()
        model_dep = self.list_model_dependent()
        lines = [
            f"Component Registry: {self.count} components",
            f"  Removable: {len(removable)}",
            f"  Model-dependent: {len(model_dep)}",
            "",
        ]
        by_cat: dict[ComponentCategory, int] = {}
        for c in self._components.values():
            by_cat[c.category] = by_cat.get(c.category, 0) + 1
        for cat in sorted(by_cat.keys(), key=lambda x: x.value):
            lines.append(f"  {cat.value}: {by_cat[cat]}")
        lines.append("")
        lines.append("Components:")
        for c in self.components:
            tag = "📦" if c.removable else "🔒"
            md = " [model-dep]" if c.model_dependent else ""
            lines.append(f"  {tag} {c.name}{md} — {c.added_for}")
        return "\n".join(lines)

    def to_dict_list(self) -> list[dict]:
        """Export as list of dicts (for JSON serialization)."""
        return [
            {
                "name": c.name,
                "added_date": c.added_date,
                "added_for": c.added_for,
                "model_dependent": c.model_dependent,
                "removal_criteria": c.removal_criteria,
                "category": c.category.value,
                "removable": c.removable,
                "module_path": c.module_path,
            }
            for c in self.components
        ]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_global_registry = ComponentRegistry()


def get_registry() -> ComponentRegistry:
    """Get the global component registry."""
    return _global_registry


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def register_component(
    name: str,
    added_date: str,
    added_for: str,
    model_dependent: bool = False,
    removal_criteria: str = "",
    category: ComponentCategory = ComponentCategory.TOOLING,
) -> Any:
    """Decorator to register a class as a harness component.

    Parameters
    ----------
    name : str
        Unique component name.
    added_date : str
        ISO date string (YYYY-MM-DD) when the component was added.
    added_for : str
        Description of the problem this component solves.
    model_dependent : bool
        Whether this component compensates for LLM limitations.
        If True, it can potentially be removed when models improve.
    removal_criteria : str
        When this component can be safely deleted. Use "permanent" for
        components that should never be removed.
    category : ComponentCategory
        Classification of the component.

    Returns
    -------
    callable
        Class decorator that registers the component and returns the
        class unchanged.
    """

    def decorator(cls):
        meta = ComponentMeta(
            name=name,
            added_date=added_date,
            added_for=added_for,
            model_dependent=model_dependent,
            removal_criteria=removal_criteria,
            category=category,
            module_path=f"{cls.__module__}.{cls.__name__}",
        )
        _global_registry.register(meta)
        # Attach meta to the class for introspection
        cls._component_meta = meta
        return cls

    return decorator


def register_module(
    name: str,
    added_date: str,
    added_for: str,
    model_dependent: bool = False,
    removal_criteria: str = "",
    category: ComponentCategory = ComponentCategory.TOOLING,
    module_path: str = "",
) -> None:
    """Register a module (not a class) as a component.

    Use this for modules that are imported for side effects
    (e.g., registering validators).
    """
    meta = ComponentMeta(
        name=name,
        added_date=added_date,
        added_for=added_for,
        model_dependent=model_dependent,
        removal_criteria=removal_criteria,
        category=category,
        module_path=module_path,
    )
    _global_registry.register(meta)
