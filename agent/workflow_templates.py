"""可复用工作流模板（G3）—— 存储、实例化、运行。

家底核实（见 A2 设计稿 §0.11）：
- 工作流模板存储/注册表此前**为零**（`workflow_templates` / `save_as_template` 全无）。
- 计划 plan 已是 dict（steps + dependencies + outputs），可 JSON 序列化复用。
- `agent/session_plan_store.save_plan_state` 已能把 (plan, todo_states) 持久化到
  SQLite —— 实例化模板即「生成新 plan + 重置 todo_states 为 pending」后落盘，
  复用既有 WorkflowScheduler 执行链路（G1b/G2/G4），零新执行逻辑。

安全边界（§0.11.3）：
- 复用既有 DB 连接与 session_plan_store 的隔离语义（按 session_id 隔离，不跨会话泄漏）。
- 实例化时对上游依赖图做 id 重映射，保证「边（dependencies）不变、节点 id 全新」，
  避免与任何已存在 session 的 plan 撞 id（§0.11.4 T2 承重墙）。
- 模板 steps_json 损坏 → 加载抛错，不静默生成空 plan（§0.11.7 风险兜底）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .session_plan_store import load_plan_state, save_plan_state
from .workflow_scheduler import SessionPlanBackend, WorkflowScheduler

logger = logging.getLogger(__name__)

_DB_PATH: Optional[Path] = None
_lock = threading.Lock()


def _get_db_path() -> Path:
    """Lazily resolve the workflow-templates database path under VERMES_HOME."""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH
    VERMES_home = os.environ.get("VERMES_HOME") or os.path.expanduser("~/.vermes")
    _db_dir = Path(VERMES_home)
    _db_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = _db_dir / "workflow_templates.db"
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    path = _get_db_path()
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS workflow_templates (
            name TEXT NOT NULL,
            version INTEGER NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            steps_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (name, version)
        )"""
    )
    return conn


class WorkflowTemplateStore:
    """工作流模板存储（SQLite，仿 session_plan_store 风格）。

    每个 (name, version) 一行；同名多次 save 自动递增 version，load 默认取最新。
    """

    def save_template(
        self,
        name: str,
        plan: dict,
        description: str = "",
        version: Optional[int] = None,
    ) -> int:
        """存为模板。version 省略时自动 = 当前最大 version + 1（首版为 1）。

        plan 须含 ``steps`` 列表（每项含 id / dependencies 等）。返回存储的 version。
        """
        if not isinstance(plan, dict) or not plan.get("steps"):
            raise ValueError("plan must be a dict with a non-empty 'steps' list")
        steps = plan["steps"]
        if not isinstance(steps, list) or not steps:
            raise ValueError("plan['steps'] must be a non-empty list")
        # 校验每个 step 有 id
        for s in steps:
            if not isinstance(s, dict) or not s.get("id"):
                raise ValueError("every template step must have a non-empty 'id'")

        with _lock, _conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS m FROM workflow_templates WHERE name=?",
                (name,),
            ).fetchone()
            max_v = row["m"] if row else 0
            new_version = int(version) if version is not None else max_v + 1
            if new_version <= 0:
                new_version = 1
            now = time.time()
            conn.execute(
                """INSERT INTO workflow_templates (name, version, description, steps_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name, version) DO UPDATE SET
                       description=excluded.description,
                       steps_json=excluded.steps_json,
                       updated_at=excluded.updated_at""",
                (name, new_version, description or "", json.dumps(steps, ensure_ascii=False), now, now),
            )
            conn.commit()
        return new_version

    def load_template(
        self, name: str, version: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """加载模板（默认最新 version）。返回 {name, version, description, steps, created_at, updated_at} 或 None。"""
        with _lock, _conn() as conn:
            if version is not None:
                row = conn.execute(
                    "SELECT * FROM workflow_templates WHERE name=? AND version=?",
                    (name, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM workflow_templates WHERE name=? ORDER BY version DESC LIMIT 1",
                    (name,),
                ).fetchone()
            if row is None:
                return None
            try:
                steps = json.loads(row["steps_json"])
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"template '{name}' steps_json is corrupted: {e}") from e
            return {
                "name": row["name"],
                "version": row["version"],
                "description": row["description"],
                "steps": steps,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def list_templates(self) -> List[Dict[str, Any]]:
        """列出每个模板的最新 version 记录。"""
        with _lock, _conn() as conn:
            rows = conn.execute(
                """SELECT t.* FROM workflow_templates t
                   INNER JOIN (
                       SELECT name, MAX(version) AS mv FROM workflow_templates GROUP BY name
                   ) m ON t.name = m.name AND t.version = m.mv
                   ORDER BY t.name""",
            ).fetchall()
            out = []
            for r in rows:
                out.append(
                    {
                        "name": r["name"],
                        "version": r["version"],
                        "description": r["description"],
                        "step_count": len(json.loads(r["steps_json"]) or []),
                        "updated_at": r["updated_at"],
                    }
                )
            return out

    def delete_template(self, name: str) -> bool:
        """删除某模板的全部 version。返回是否删除了任何行。"""
        with _lock, _conn() as conn:
            cur = conn.execute("DELETE FROM workflow_templates WHERE name=?", (name,))
            conn.commit()
            return cur.rowcount > 0


def _remap_plan(plan: dict, session_id: str) -> dict:
    """生成新 plan：每个 step 分配全新 id，dependencies 同步重映射。

    边（依赖关系）保持拓扑不变，仅节点 id 全新 → 与任何既有 session plan 不撞 id。
    """
    steps = plan.get("steps", []) or []
    id_map: Dict[str, str] = {}
    for s in steps:
        old_id = s.get("id")
        # 8 位 hex 保证唯一且足够短，前缀含 session 避免跨会话混淆
        new_id = f"{session_id}__st_{uuid.uuid4().hex[:8]}"
        id_map[old_id] = new_id

    new_steps: List[dict] = []
    for s in steps:
        old_deps = s.get("dependencies") or []
        new_deps = [id_map[d] for d in old_deps if d in id_map]
        new_step = dict(s)  # 保留 title/description/deliverable/done_when 等全部字段
        new_step["id"] = id_map[s.get("id")]
        new_step["dependencies"] = new_deps
        # 重置运行态：全新实例化，状态 pending；inputs/outputs 清空（由 G4 运行时再聚合）
        new_step["status"] = "pending"
        new_step["inputs"] = {}
        new_step["outputs"] = {}
        new_steps.append(new_step)

    return {"steps": new_steps}


def instantiate_template(
    name: str,
    session_id: str,
    version: Optional[int] = None,
) -> dict:
    """把模板实例化为某 session 的 plan，并持久化到 session_plan_store。

    生成全新 step id（避免与既有 plan 撞 id），依赖边不变，状态全部 pending。
    返回实例化的 new_plan（含重映射后的 steps）。模板不存在 / 损坏 → 抛错（不静默空 plan）。
    """
    tpl = WorkflowTemplateStore().load_template(name, version=version)
    if tpl is None:
        raise KeyError(f"workflow template '{name}' not found")
    new_plan = _remap_plan({"steps": tpl["steps"]}, session_id)
    todo_states = {s["id"]: "pending" for s in new_plan["steps"]}
    save_plan_state(session_id, new_plan, todo_states, plan_emitted=True)
    return new_plan


async def run_template_async(
    name: str,
    session_id: str,
    step_executor,
    concurrent: bool = False,
    version: Optional[int] = None,
) -> "WorkflowScheduler.__init__.__class__":  # type: ignore[name-defined]
    """实例化模板并运行（异步）。step_executor 须匹配 workflow_scheduler.StepExecFunc。"""
    instantiate_template(name, session_id, version=version)
    backend = SessionPlanBackend()
    scheduler = WorkflowScheduler(backend=backend, step_executor=step_executor)
    return await scheduler.execute(session_id, concurrent=concurrent)


def run_template(
    name: str,
    session_id: str,
    step_executor,
    concurrent: bool = False,
    version: Optional[int] = None,
) -> Any:
    """实例化模板并运行（同步封装，内部 asyncio.run）。"""
    return asyncio.run(
        run_template_async(
            name, session_id, step_executor, concurrent=concurrent, version=version
        )
    )
