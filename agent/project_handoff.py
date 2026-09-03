"""Agent 通用项目 Handoff 层 (Phase 5)

跨域通用的持续性项目状态注入。论文/剧本/小说/短剧等任何持续性任务
都通过 record_project_handoff() 发射状态，continuity_facade 第 7 源
读 get_active_handoffs() 自动注入新会话 turn-1。

核心设计:
- (domain, project_id) 复合键隔离 — 避免与 GCP OAuth project_id 冲突
- domain ∈ paper|screenplay|novel|shortdrama|custom|generic
  （generic = 任意长程任务通用入口，见 record_generic_handoff）
- 存储在 agent 层 memory_index.db.project_handoffs 表（通用记忆库）
- fail-open: 读/写失败不阻断主操作

发射桥: 各域模块在写操作时调 record_project_handoff(domain, project_id, ...)
接收端: continuity_facade 第 7 源调 get_active_handoffs() → format_handoffs_prompt()

契约:
    from agent.project_handoff import record_project_handoff
    record_project_handoff(
        domain="paper",           # 论文
        project_id=42,           # ScholarForge 项目 ID
        title="基于深度学习的图像分类",
        status="writing",        # active|writing|reviewing|done
        progress="3/9 章节，4500/12000 字",
        last_section="method",
        extra={"paper_type": "本科论文", "literatures": 15},
    )
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("agent.project_handoff")

# ── DB 层 ──

def _db_path():
    """复用 memory_fabric 的 memory_index.db。"""
    from agent.memory_fabric import index_db_path
    return index_db_path()


def _ensure_table():
    """确保 project_handoffs 表存在。幂等。"""
    import sqlite3
    db_path = _db_path()
    if db_path is None or not str(db_path).strip():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                progress TEXT NOT NULL DEFAULT '',
                last_section TEXT NOT NULL DEFAULT '',
                extra TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(domain, project_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_handoffs_domain "
            "ON project_handoffs(domain)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_handoffs_updated "
            "ON project_handoffs(updated_at DESC)"
        )
        conn.commit()
        return conn
    except Exception as e:
        logger.warning("project_handoff: _ensure_table failed: %s", e)
        conn.close()
        return None


# ── 写入 API（各域发射桥调用） ──

def record_project_handoff(
    domain: str,
    project_id: int,
    title: str = "",
    status: str = "active",
    progress: str = "",
    last_section: str = "",
    extra: dict[str, Any] | None = None,
) -> bool:
    """记录/更新一个项目的 handoff 状态。

    使用 UPSERT 语义：同一 (domain, project_id) 更新，不存在则插入。

    Args:
        domain: 领域标识符 (paper|screenplay|novel|shortdrama|custom|generic)
        project_id: 领域内部的项目 ID（ScholarForge projects.id 等）
        title: 项目标题
        status: 项目状态 (active|writing|reviewing|done)
        progress: 进度描述（如 "3/9 章节，4500/12000 字"）
        last_section: 最后操作的章节/场景标识
        extra: 额外元数据（paper_type, literature_count 等）

    Returns:
        True 成功，False 失败（fail-open：调用方应忽略失败）
    """
    conn = _ensure_table()
    if conn is None:
        return False
    try:
        now = int(time.time())
        extra_json = json.dumps(extra or {}, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO project_handoffs
                (domain, project_id, title, status, progress,
                 last_section, extra, updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain, project_id) DO UPDATE SET
                title = excluded.title,
                status = excluded.status,
                progress = excluded.progress,
                last_section = excluded.last_section,
                extra = excluded.extra,
                updated_at = excluded.updated_at
            """,
            (domain, project_id, title, status, progress,
             last_section, extra_json, now, now),
        )
        conn.commit()
        logger.debug(
            "project_handoff: recorded domain=%s pid=%s title=%s status=%s",
            domain, project_id, title[:40], status,
        )
        return True
    except Exception as e:
        logger.warning(
            "project_handoff: record failed domain=%s pid=%s: %s",
            domain, project_id, e,
        )
        return False
    finally:
        conn.close()


def remove_project_handoff(domain: str, project_id: int) -> bool:
    """删除一个项目的 handoff 记录（项目完成/删除时调用）。"""
    conn = _ensure_table()
    if conn is None:
        return False
    try:
        conn.execute(
            "DELETE FROM project_handoffs WHERE domain = ? AND project_id = ?",
            (domain, project_id),
        )
        conn.commit()
        return True
    except Exception as e:
        logger.warning("project_handoff: remove failed: %s", e)
        return False
    finally:
        conn.close()


# ── 通用发射入口（⑮ 腿 B：任意长程任务可写，非 scholarforge 专用） ──

def _stable_task_id(task_key: str) -> int:
    """把任意字符串 task_key 稳定哈希为 int（project_id 列要求 INTEGER）。

    用 fnv-1a 64-bit 取低 52 位（可安全存入 SQLite INTEGER），
    跨进程/跨会话稳定 —— 同一 task_key 恒得到同一 project_id，
    从而命中 UNIQUE(domain, project_id) 的 UPSERT 而非重复插入。
    """
    h = 0xCBF29CE484222325
    for b in task_key.encode("utf-8"):
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h & ((1 << 52) - 1)


def record_generic_handoff(
    task_key: str,
    title: str = "",
    status: str = "active",
    progress: str = "",
    last_section: str = "",
    extra: dict[str, Any] | None = None,
) -> bool:
    """通用长程任务的 handoff 发射入口（⑮ 腿 B）。

    与 record_project_handoff(domain="generic", ...) 等价，但用字符串
    task_key 代替整数 project_id —— 通用任务（代码重构、长文档写作、
    多轮调研等）没有领域内整数 ID，由 task_key 稳定哈希得到 project_id。

    供任意 agent / 工具调用，使非 scholarforge 的持续性任务也能进入
    continuity_facade 第 7 源的跨会话注入（此前仅 scholarforge 单域发射）。

    Args:
        task_key: 任务稳定标识（如 "refactor-auth-flow"、"docs:roadmap"）。
                  同一 key 会更新而非新增记录。
        title: 任务标题（人类可读）
        status: active|writing|reviewing|done
        progress: 进度描述
        last_section: 最后处理的阶段/模块标识
        extra: 附加元数据（结论、依据、未完成项等）

    Returns:
        True 成功，False 失败（fail-open）
    """
    if not task_key or not task_key.strip():
        return False
    project_id = _stable_task_id(task_key.strip())
    return record_project_handoff(
        domain="generic",
        project_id=project_id,
        title=title,
        status=status,
        progress=progress,
        last_section=last_section,
        extra=extra,
    )


def emit_generic_handoff_from_session(
    messages: list[dict[str, Any]],
    session_id: str = "",
) -> bool:
    """会话结束时的通用 handoff 自动发射钩子（⑮ 腿 B）。

    复用 session_handoff 的确定性提取器（无 LLM、零成本），仅在存在
    「阶段结论」（有决策或有待办）时发射到 generic 域 —— 门控目的是
    避免纯问答型会话污染 project_handoffs 活跃列表（get_active_handoffs
    默认只取最近 5 条）。

    同一 session_id 多次调用（如 /new 轮换 + 退出）会 UPSERT 同一条记录，
    不会无限堆积。

    这是「自动」发射的**保守实现**：只对"有实质阶段性结论"的会话落盘，
    与 ② 注 3 的「自动溯源」同理 —— 更激进的自动触发（每轮都发）留待
    用户拍板，避免 token/污染成本。

    Returns:
        True 发射成功（或有结论且写入成功）；False 无结论（跳过）或失败
    """
    if not messages:
        return False
    try:
        from agent.session_handoff import (
            _extract_user_request,
            _extract_decisions,
            _extract_pending_tasks,
        )
    except Exception:
        return False

    try:
        user_request = _extract_user_request(messages)
        decisions = _extract_decisions(messages)
        pending = _extract_pending_tasks(messages)
    except Exception as e:
        logger.warning("emit_generic_handoff_from_session: extract failed: %s", e)
        return False

    # 门控：既无决策又无待办 → 一次性问答，不发射
    if not decisions and not pending:
        return False

    task_key = f"session:{session_id}" if session_id else "session:unknown"
    title = user_request[:80] or f"会话 {session_id or 'unknown'}"
    progress = f"{len(pending)} 待办 / {len(decisions)} 结论" if pending else f"{len(decisions)} 结论"
    extra: dict[str, Any] = {
        "decisions": [d["decision"] for d in decisions[:5]],
        "pending_tasks": [t["task"] for t in pending[:5]],
    }
    return record_generic_handoff(
        task_key=task_key,
        title=title,
        status="active",
        progress=progress,
        extra=extra,
    )


# ── 读取 API（continuity_facade 调用） ──

def get_active_handoffs(limit: int = 5) -> list[dict[str, Any]]:
    """获取最近活跃的项目 handoff 列表。

    按 updated_at 降序，最多 limit 条。
    """
    conn = _ensure_table()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT domain, project_id, title, status, progress,
                   last_section, extra, updated_at
            FROM project_handoffs
            WHERE status != 'done'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            try:
                extra = json.loads(r[6]) if r[6] else {}
            except (json.JSONDecodeError, IndexError):
                extra = {}
            result.append({
                "domain": r[0],
                "project_id": r[1],
                "title": r[2],
                "status": r[3],
                "progress": r[4],
                "last_section": r[5],
                "extra": extra,
                "updated_at": r[7],
            })
        return result
    except Exception as e:
        logger.warning("project_handoff: get_active failed: %s", e)
        return []
    finally:
        conn.close()


def format_handoffs_prompt() -> str:
    """格式化活跃项目 handoff 列表（用于 turn-1 注入）。

    通用格式，不依赖任何领域特定知识。
    各域的 title/progress/last_section 由发射方填充。
    """
    handoffs = get_active_handoffs(limit=5)
    if not handoffs:
        return ""

    # 按域分组
    by_domain: dict[str, list[dict]] = {}
    for h in handoffs:
        d = h["domain"]
        by_domain.setdefault(d, []).append(h)

    DOMAIN_LABELS = {
        "paper": "论文",
        "screenplay": "剧本",
        "novel": "小说",
        "shortdrama": "短剧",
        "custom": "项目",
        "generic": "任务",
    }

    parts = ["【你的活跃项目】"]
    for domain, items in by_domain.items():
        label = DOMAIN_LABELS.get(domain, domain)
        for h in items:
            pid = h["project_id"]
            title = h["title"] or "(未命名)"
            progress = h["progress"]
            status = h["status"]
            last_sec = h["last_section"]

            line = f"  [{label}] #{pid} 《{title}》"
            if status and status != "active":
                line += f"（{status}）"
            if progress:
                line += f" — {progress}"
            if last_sec:
                line += f" | 上次: {last_sec}"
            parts.append(line)

    parts.append("\n提示：使用对应工具的 project_id 参数可继续该项目。")
    return "\n".join(parts)
