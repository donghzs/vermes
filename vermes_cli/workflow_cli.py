"""Vermes CLI：可复用工作流模板（G3）。

子命令（见 A2 设计稿 §0.11.2 第4点）：
  vermes workflow save  <session_id> <name> [--description D]
  vermes workflow list
  vermes workflow show  <name>
  vermes workflow delete <name>
  vermes workflow run   <name> [--session SID] [--version N]

说明：
- ``save`` 从某 session 已持久化的 plan（session_plan_store）取当前步骤图存为模板。
- ``run`` 把模板实例化为一个**新 session**（默认自动生成 session id）并落盘 plan，
  打印出新 session id；真正执行由触发器（cron / webhook，G6）或应用内工作流运行时驱动。
  纯 CLI 环境没有 AIAgent，故 run 只实例化、不就地跑 LLM——这是诚实且可用的边界。
"""

import argparse
import json
import logging
import sys
import uuid
from typing import Optional

from agent.workflow_templates import (
    WorkflowTemplateStore,
    instantiate_template,
)

logger = logging.getLogger(__name__)


def _fmt_template_row(t: dict) -> str:
    return (
        f"  {t['name']}  (v{t['version']}, {t['step_count']} steps)  "
        f"updated {t['updated_at']:.0f}\n"
        f"    {t.get('description') or '(no description)'}"
    )


def workflow_save(session_id: str, name: str, description: str = "") -> int:
    from agent.session_plan_store import load_plan_state

    data = load_plan_state(session_id)
    if not data or not data.get("plan") or not data["plan"].get("steps"):
        logger.info(
            "No plan found for session '%s'. Run a workflow in that session first "
            "(or ensure its plan was emitted), then save.",
            session_id,
        )
        return 1
    plan = data["plan"]
    store = WorkflowTemplateStore()
    version = store.save_template(name, plan, description=description)
    logger.info(
        "Saved template '%s' (v%d) from session '%s' — %d steps.",
        name, version, session_id, len(plan["steps"]),
    )
    return 0


def workflow_list() -> int:
    store = WorkflowTemplateStore()
    templates = store.list_templates()
    if not templates:
        logger.info("No workflow templates saved yet.")
        return 0
    logger.info("Workflow templates:")
    for t in templates:
        logger.info(_fmt_template_row(t))
    return 0


def workflow_show(name: str) -> int:
    store = WorkflowTemplateStore()
    tpl = store.load_template(name)
    if not tpl:
        logger.info("Template '%s' not found.", name)
        return 1
    logger.info("Template: %s (v%d)", name, tpl["version"])
    if tpl.get("description"):
        logger.info("Description: %s", tpl["description"])
    logger.info("Steps:")
    for s in tpl["steps"]:
        deps = s.get("dependencies") or []
        dep_str = ", ".join(str(d) for d in deps) if deps else "(none)"
        logger.info("  - [%s] %s", s.get("id"), s.get("title", ""))
        logger.info("      deps: %s", dep_str)
    return 0


def workflow_delete(name: str) -> int:
    store = WorkflowTemplateStore()
    if store.delete_template(name):
        logger.info("Deleted template '%s' (all versions).", name)
        return 0
    logger.info("Template '%s' not found.", name)
    return 1


def workflow_run(name: str, session_id: Optional[str] = None, version: Optional[int] = None) -> int:
    sid = session_id or f"wf-{uuid.uuid4().hex[:12]}"
    try:
        new_plan = instantiate_template(name, sid, version=version)
    except KeyError:
        logger.info("Template '%s' not found.", name)
        return 1
    except ValueError as e:
        logger.info("Cannot instantiate template: %s", e)
        return 1
    logger.info(
        "Instantiated template '%s' into session '%s' (%d steps, all pending).",
        name, sid, len(new_plan["steps"]),
    )
    logger.info("Run it via a cron/webhook trigger, or resume the workflow in that session.")
    return 0


def workflow_command(args) -> int:
    sub = getattr(args, "workflow_command", None)
    if sub == "save":
        return workflow_save(args.session_id, args.name, getattr(args, "description", "") or "")
    if sub == "list":
        return workflow_list()
    if sub == "show":
        return workflow_show(args.name)
    if sub == "delete":
        return workflow_delete(args.name)
    if sub == "run":
        return workflow_run(
            args.name,
            session_id=getattr(args, "session", None),
            version=getattr(args, "version", None),
        )
    logger.info("Unknown workflow command. Use: save | list | show | delete | run")
    return 1


def build_workflow_parser(subparsers) -> argparse.ArgumentParser:
    """Register the ``workflow`` subcommand. Returns the workflow parser."""
    wf = subparsers.add_parser(
        "workflow", help="Manage reusable workflow templates (G3)"
    )
    wf_sub = wf.add_subparsers(dest="workflow_command")

    p_save = wf_sub.add_parser("save", help="Save a session's plan as a template")
    p_save.add_argument("session_id", help="Session id whose emitted plan to save")
    p_save.add_argument("name", help="Template name")
    p_save.add_argument("--description", default="", help="Optional description")

    wf_sub.add_parser("list", help="List saved templates")

    p_show = wf_sub.add_parser("show", help="Show a template's steps")
    p_show.add_argument("name", help="Template name")

    p_del = wf_sub.add_parser("delete", help="Delete a template (all versions)")
    p_del.add_argument("name", help="Template name")

    p_run = wf_sub.add_parser("run", help="Instantiate a template into a new session")
    p_run.add_argument("name", help="Template name")
    p_run.add_argument("--session", default=None, help="Target session id (default: auto)")
    p_run.add_argument("--version", type=int, default=None, help="Template version (default: latest)")

    return wf
