"""Vermes harness layer.

Cross-cutting harness capabilities extracted from the harness-insights
analysis (see harness_insights_for_vermes.html):

- recoverable: structured, machine-readable feedback when a tool fails
  unexpectedly (harness capability #2 — "check failure state + recoverable
  feedback").
- stability: opt-in best/worst-of-N probe for multi-run reliability
  (harness capability #4 — "multi-run stability > single-run").
- constraints: generic validation base + runner, generalized from
  ScholarForge validators (harness capability #3 — "unified constraint
  contract across domains").

These modules are side-effect free and intentionally opt-in: nothing in the
default request path imports them unless a caller wires them in.
"""

from .recoverable import (
    RecoverableFeedback,
    recoverable_tool,
    classify_failure,
)
from .stability import StabilityReport, probe_stability, stability_probe
from .constraints import (
    Constraint,
    ConstraintResult,
    ConstraintReport,
    run_constraints,
)
from .task_precheck import TaskPreCheckResult, check_task_constraints
from .stability_hotpath import probe_tool_stability, is_probe_enabled

__all__ = [
    "RecoverableFeedback",
    "recoverable_tool",
    "classify_failure",
    "StabilityReport",
    "probe_stability",
    "stability_probe",
    "Constraint",
    "ConstraintResult",
    "ConstraintReport",
    "run_constraints",
    "TaskPreCheckResult",
    "check_task_constraints",
    "probe_tool_stability",
    "is_probe_enabled",
]
