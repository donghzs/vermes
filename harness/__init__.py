"""Vermes harness layer.

Cross-cutting harness capabilities extracted from the harness-insights
analysis (see harness_insights_for_vermes.html):

- recoverable: structured, machine-readable feedback when a tool fails
  unexpectedly (harness capability #2 — "check failure state + recoverable
  feedback").
- tool_precheck / task_precheck: pre-execution constraint gates (H2.1/H1.1).
- circuit_breaker + failure_learning + precision_matrix + result_validator
  + outcome_verifier: quality gates wired into ``agent/tool_executor.py``
  and orchestration paths on the **default request path** (fail-open).
- stability: opt-in best/worst-of-N probe (guarded by agent flag;
  historically dormant unless `_enable_stability_probe` is set).
- constraints / release_constraints: ops/CLI release checks — **not** on
  the default conversation path.

Wiring reality (2026-09 E-P0 audit):
- recoverable / task_precheck / tool_precheck / failure_learning /
  precision_matrix / result_validator / outcome_verifier **are imported
  from production agent paths** (tool_executor, turn_service, …).
- Fail-open: missing modules or internal errors must not block tool
  execution; E-P0-2 surfaces such failures via warning + counts
  (``agent.tool_executor.get_harness_fail_counts``) instead of silent debug.
- Stability probing is opt-in; do not assume it runs unless enabled.
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
from .stability_hotpath import (
    probe_tool_stability,
    is_probe_enabled,
    is_stability_probe_enabled,
    set_stability_probe_enabled,
)
from .failure_learning import FailurePattern, FailureLedger, get_ledger
from .metrics import MetricsCollector, get_metrics, track_recall_latency

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
    "is_stability_probe_enabled",
    "set_stability_probe_enabled",
    "FailurePattern",
    "FailureLedger",
    "get_ledger",
    "MetricsCollector",
    "get_metrics",
    "track_recall_latency",
]
