"""Seed a sample session plan into SQLite via the REAL session_plan_store module.

Used by the cross-restart e2e (chat-cross-restart.spec.ts): a "prior process"
writes the plan here, then a *separate* mock-backend process reads it back after a
simulated restart — proving plan state survives across processes via SQLite
(boundary #4, vermes_task_pipeline_context_audit_REVISED_20260723.html §6).

The plan shape matches what the frontend store.onPlanCreated expects:
  plan.steps[].id / .title / .status ; todo_states maps step id -> status.

Usage:
    HERMES_HOME=/tmp/vermes-e2e python3 frontend/playwright/seed_session_plan.py [session_id]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agent.session_plan_store import save_plan_state  # noqa: E402

PLAN = {
    "id": "plan-cross-restart",
    "title": "跨重启恢复验证计划",
    "steps": [
        {"id": "s1", "title": "检索相关资料", "status": "pending"},
        {"id": "s2", "title": "撰写对比报告", "status": "pending"},
    ],
}

TODO_STATES = {"s1": "in_progress", "s2": "pending"}


def main() -> None:
    session_id = sys.argv[1] if len(sys.argv) > 1 else "sess-cross-restart"
    save_plan_state(session_id, PLAN, TODO_STATES, plan_emitted=True)
    db = os.path.join(os.environ.get("HERMES_HOME", "~/.hermes"), "session_plans.db")
    print(f"[seed] wrote plan for session={session_id} -> {db}")


if __name__ == "__main__":
    main()
