#!/usr/bin/env python3
"""CI gate: enforce code quality thresholds via entropy_gardener.

Exits non-zero if any threshold is violated. Designed for pre-commit
or CI pipeline integration.

Usage:
    python scripts/quality_gate.py [--root /path/to/project] [--json]

Exit codes:
    0 — all thresholds passed
    1 — one or more thresholds violated
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from agent.entropy_gardener import scan_codebase, DebtReport


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Maximum allowed values (violation = gate failure)
MAX_LARGE_FUNCTIONS = 15      # functions > 500 lines
MAX_PRINT_CALLS = 20          # print() calls in scanned dirs
MAX_BARE_EXCEPT = 0           # bare except: (must be zero)
MAX_EXCEPT_PASS = 600         # except Exception: pass
MAX_TODO_FIXME = 10           # TODO/FIXME comments


def check_thresholds(report: DebtReport) -> list[dict]:
    """Check report against thresholds. Returns list of violations."""
    violations = []

    if len(report.large_functions) > MAX_LARGE_FUNCTIONS:
        violations.append({
            "metric": "large_functions",
            "value": len(report.large_functions),
            "threshold": MAX_LARGE_FUNCTIONS,
            "message": f"Too many large functions: {len(report.large_functions)} > {MAX_LARGE_FUNCTIONS}",
        })

    if report.total_print > MAX_PRINT_CALLS:
        violations.append({
            "metric": "print_calls",
            "value": report.total_print,
            "threshold": MAX_PRINT_CALLS,
            "message": f"Too many print() calls: {report.total_print} > {MAX_PRINT_CALLS}",
        })

    if report.total_bare_except > MAX_BARE_EXCEPT:
        violations.append({
            "metric": "bare_except",
            "value": report.total_bare_except,
            "threshold": MAX_BARE_EXCEPT,
            "message": f"Bare except found: {report.total_bare_except} > {MAX_BARE_EXCEPT}",
        })

    if report.total_except_pass > MAX_EXCEPT_PASS:
        violations.append({
            "metric": "except_pass",
            "value": report.total_except_pass,
            "threshold": MAX_EXCEPT_PASS,
            "message": f"Too many except-pass: {report.total_except_pass} > {MAX_EXCEPT_PASS}",
        })

    if report.total_todo_fixme > MAX_TODO_FIXME:
        violations.append({
            "metric": "todo_fixme",
            "value": report.total_todo_fixme,
            "threshold": MAX_TODO_FIXME,
            "message": f"Too many TODO/FIXME: {report.total_todo_fixme} > {MAX_TODO_FIXME}",
        })

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Code quality gate")
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    report = scan_codebase(args.root)
    violations = check_thresholds(report)

    if args.json:
        output = {
            "summary": report.summary(),
            "metrics": report.to_dict(),
            "violations": violations,
            "passed": len(violations) == 0,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(report.summary())
        if violations:
            print("\n❌ THRESHOLD VIOLATIONS:")
            for v in violations:
                print(f"  {v['metric']}: {v['message']}")
            print(f"\n{len(violations)} violation(s) — quality gate FAILED")
        else:
            print("\n✅ All thresholds passed — quality gate OK")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
