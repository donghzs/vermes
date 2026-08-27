"""Blueprint: Workflows（可复用工作流模板 REST 管理）

把 G3 的 ``WorkflowTemplateStore``（SQLite 模板库）与 G6 的运行入口
（``agent.workflow_runtime.run_workflow_template_sync`` / ``build_agent``）
暴露为 HTTP 端点，供前端 WorkflowsPage 可视化编排使用。

设计约束（与 cron_jobs.py 一致）：
- 用 ``register_to(app)`` + ``app.add_api_route`` 模式，不引 APIRouter。
- 无独立 auth 依赖（沿用 app 级 session-token 鉴权，与 cron 蓝图同口径）。
- 运行端点为同步 ``def`` → FastAPI 放进线程池执行，不阻塞事件循环。
"""

import asyncio
import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from agent.workflow_runtime import build_agent, get_step_pool, run_workflow_template_sync
from agent.workflow_templates import WorkflowTemplateStore

_log = logging.getLogger(__name__)
_store = WorkflowTemplateStore()


# ── models ─────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    id: str
    title: str = ""
    description: str = ""
    deliverable: str = ""
    done_when: str = ""
    dependencies: List[str] = []


class WorkflowSave(BaseModel):
    name: str
    description: str = ""
    steps: List[WorkflowStep]


class WorkflowRunRequest(BaseModel):
    prompt: Optional[str] = None
    version: Optional[int] = None
    concurrent: bool = False
    model: Optional[str] = None
    provider: Optional[str] = None


# ── helpers ────────────────────────────────────────────────────

def _step_to_dict(s: WorkflowStep) -> Dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "description": s.description,
        "deliverable": s.deliverable,
        "done_when": s.done_when,
        "dependencies": list(s.dependencies or []),
    }


# ── route handlers ─────────────────────────────────────────────

async def list_workflows() -> List[Dict[str, Any]]:
    return _store.list_templates()


async def get_workflow(name: str) -> Dict[str, Any]:
    tpl = _store.load_template(name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"workflow '{name}' not found")
    return tpl


async def save_workflow(body: WorkflowSave) -> Dict[str, Any]:
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not body.steps:
        raise HTTPException(status_code=400, detail="at least one step is required")
    # 校验依赖指向的 step 真实存在
    ids = {s.id for s in body.steps}
    for s in body.steps:
        for d in s.dependencies or []:
            if d not in ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"step '{s.id}' depends on unknown step '{d}'",
                )
    plan = {"steps": [_step_to_dict(s) for s in body.steps]}
    try:
        version = _store.save_template(body.name.strip(), plan, description=body.description or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"name": body.name.strip(), "version": version, "ok": True}


async def delete_workflow(name: str) -> Dict[str, Any]:
    if not _store.delete_template(name):
        raise HTTPException(status_code=404, detail=f"workflow '{name}' not found")
    return {"ok": True}


def run_workflow(name: str, body: WorkflowRunRequest) -> Dict[str, Any]:
    """同步运行（FastAPI 在线程池执行，不阻塞事件循环）。

    复用 G6 触发器同款入口：build_agent 建最小父 agent →
    run_workflow_template_sync 实例化模板并经 WorkflowScheduler 跑。
    """
    if _store.load_template(name) is None:
        raise HTTPException(status_code=404, detail=f"workflow '{name}' not found")
    session_id = f"wf_run_{uuid.uuid4().hex[:12]}"
    try:
        agent = build_agent(
            session_id,
            model=body.model,
            provider=body.provider,
            platform="workflow-ui",
        )
        result = run_workflow_template_sync(
            name,
            agent,
            session_id,
            user_message=body.prompt or None,
            version=body.version,
            step_pool=get_step_pool(),
            concurrent=bool(body.concurrent),
        )
        return {"name": name, "session_id": session_id, **(result or {})}
    except Exception as e:  # noqa: BLE001 - 向前端透出真实错误
        _log.exception("workflow %s run failed", name)
        raise HTTPException(status_code=500, detail=f"workflow run failed: {e}")


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register workflow template routes on the FastAPI app."""
    app.add_api_route("/api/workflows", list_workflows, methods=["GET"], name="list_workflows")
    app.add_api_route("/api/workflows", save_workflow, methods=["POST"], name="save_workflow")
    app.add_api_route(
        "/api/workflows/{name}", get_workflow, methods=["GET"], name="get_workflow"
    )
    app.add_api_route(
        "/api/workflows/{name}", delete_workflow, methods=["DELETE"], name="delete_workflow"
    )
    app.add_api_route(
        "/api/workflows/{name}/run",
        run_workflow,
        methods=["POST"],
        name="run_workflow",
    )


# 与 cron_jobs.py 一致：无 APIRouter，使用 register_to(app) 模式
blueprint = None
