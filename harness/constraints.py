"""Harness: generic constraint / validation base.

Generalized from ScholarForge validators
(``vermes_cli/scholarforge/validators.py``), whose pattern is:
"``async check(...)`` -> typed result dataclass -> ``run_all``". This module
gives every domain (ScholarForge, tool inputs, config, output schema) one
uniform contract so constraints compose and report identically — harness
insight #3 ("unified constraint contract across domains").

The base is intentionally tiny and side-effect free. Existing validators
(ScholarForge's 752-line module + its 133 tests) are left untouched; new
domains subclass ``Constraint`` and reuse ``run_constraints``. A future
follow-up can migrate ScholarForge onto this base behind its existing public
API without behavior change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

logger = logging.getLogger("harness.constraints")


@dataclass
class ConstraintResult:
    """Outcome of a single constraint check."""

    name: str
    passed: bool
    severity: str = "error"  # info | warning | error
    detail: str = ""
    suggestion: str = ""
    meta: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "meta": self.meta,
        }


class Constraint:
    """Base class for one validation.

    Subclass and implement ``async check(self, ctx) -> ConstraintResult``.
    ``name`` / ``severity`` may be overridden as class attributes.
    """

    name: str = "unnamed"
    severity: str = "error"

    async def check(self, ctx: Any) -> ConstraintResult:  # pragma: no cover
        raise NotImplementedError


@dataclass
class ConstraintReport:
    results: list[ConstraintResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # Only failing error-severity constraints block an overall pass.
        # info / warning findings are advisory and never block.
        return len(self.errors) == 0

    @property
    def errors(self) -> list[ConstraintResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[ConstraintResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]

    def to_payload(self) -> dict:
        return {
            "passed": self.passed,
            "errors": [r.to_payload() for r in self.errors],
            "warnings": [r.to_payload() for r in self.warnings],
            "all": [r.to_payload() for r in self.results],
        }


async def run_constraints(
    constraints: Sequence[Constraint],
    ctx: Any = None,
    *,
    short_circuit: bool = False,
) -> ConstraintReport:
    """Run a sequence of constraints against ``ctx``.

    Args:
        constraints: ordered constraints to evaluate.
        ctx: domain context passed to every ``check``.
        short_circuit: stop at the first failing *error*-severity constraint.

    A constraint that raises is recorded as a failed error-severity result
    rather than aborting the whole run.
    """
    report = ConstraintReport()
    for c in constraints:
        try:
            res = await c.check(ctx)
        except Exception as exc:  # noqa: BLE001 — a broken constraint must not crash the runner
            logger.warning("constraint %s raised: %r", getattr(c, "name", "?"), exc)
            res = ConstraintResult(
                name=getattr(c, "name", "?"),
                passed=False,
                severity="error",
                detail=f"constraint error: {exc}",
            )
        report.results.append(res)
        if short_circuit and not res.passed and res.severity == "error":
            break
    return report
