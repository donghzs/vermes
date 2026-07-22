"""
FLAG 记忆反思引擎 — 复用 curator idiom（空闲门控 + fork 辅助 agent + 持久化状态）

核心原则：
1. 只读原 memories 表，只 INSERT INTO memory_flags（绝不 LLM 触发的 UPDATE/DELETE）
2. fail-open：反思失败/超时 → 静默跳过，不影响主会话
3. 空闲触发 + 批处理 + 采样，成本可控
"""
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# ── 持久化状态 ─────────────────────────────────────────────────────

def _reflection_state_path() -> Path:
    """镜像 curator .curator_state"""
    return get_hermes_home() / ".reflection_state"


def _load_state() -> Dict:
    """加载反思状态"""
    path = _reflection_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            pass
    return {"last_run_at": None, "paused": False, "last_summary": ""}


def _save_state(state: Dict):
    """保存反思状态"""
    path = _reflection_state_path()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ── 空闲门控 ───────────────────────────────────────────────────────

def get_reflection_min_idle_hours() -> float:
    """复用 curator 空闲阈值（或新增配置）"""
    # 直接复制 curator 逻辑，避免循环 import
    DEFAULT_MIN_IDLE_HOURS = 0.5
    try:
        from hermes_constants import get_hermes_home
        cfg_path = get_hermes_home() / "curator_config.json"
        if cfg_path.exists():
            import json
            cfg = json.loads(cfg_path.read_text())
            return float(cfg.get("min_idle_hours", DEFAULT_MIN_IDLE_HOURS))
    except:
        pass
    return DEFAULT_MIN_IDLE_HOURS


def _is_idle_enough() -> bool:
    """检查是否足够空闲"""
    state = _load_state()
    if state.get("paused"):
        return False

    min_hours = get_reflection_min_idle_hours()
    # 这里简化实现，实际需要检查用户最后活动时间
    # curator.py 有完整的空闲检测逻辑
    return True  # 暂时返回 True，后续补完整逻辑


# ── 数据库 Schema ──────────────────────────────────────────────────

def ensure_reflection_schema(db_path: Optional[Path] = None):
    """创建 memory_flags 表（幂等）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = db_path or _get_mem_db()
    if isinstance(db_path, str):
        db_path = Path(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_flags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id   TEXT NOT NULL,
                flag_type   TEXT NOT NULL,
                confidence  REAL DEFAULT 0.0,
                evidence    TEXT,
                status      TEXT NOT NULL DEFAULT 'open',
                created_at  TEXT NOT NULL,
                source      TEXT DEFAULT 'reflection'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_flags_status
            ON memory_flags(status, created_at)
        """)
        conn.commit()
    finally:
        conn.close()


# ── 核心入口 ────────────────────────────────────────────────────────

def maybe_run_reflection():
    """空闲门控 + 触发反思（镜像 curator.maybe_run_curator）"""
    if not _is_idle_enough():
        logger.debug("[Reflection] Not idle enough, skip")
        return

    state = _load_state()
    if state.get("paused"):
        logger.debug("[Reflection] Paused, skip")
        return

    try:
        run_reflection_review()
    except Exception as e:
        logger.warning(f"[Reflection] Reflection failed: {e} (fail-open)")


def run_reflection_review():
    """Fork 辅助 agent 执行反思（镜像 curator.run_curator_review）

    当前为骨架实现，R1-R4 逐步填充：
    - R1: 矛盾校核（复用 decision_tracker）
    - R2: 四类校核（LLM 反思）
    - R3: FlagFloatUp（continuity_facade 第 6 通道）
    - R4: Resolution（双通道）
    """
    ensure_reflection_schema()
    logger.info("[Reflection] Starting reflection review...")

    # R1-R4 实现点
    # ...

    # 更新状态
    state = _load_state()
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_summary"] = "Reflection completed (skeleton)"
    _save_state(state)

    logger.info("[Reflection] Reflection review done")


# ── 辅助函数 ────────────────────────────────────────────────────────

def get_open_flags(limit: int = 50) -> List[Dict]:
    """获取 open 状态的 flags（供 continuity_facade 注入）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id, memory_id, flag_type, confidence, evidence, created_at
               FROM memory_flags
               WHERE status = 'open'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "flag_type": r[2],
                "confidence": r[3],
                "evidence": r[4],
                "created_at": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def format_flags_for_context(flags: List[Dict]) -> str:
    """格式化 flags 供 continuity_facade 注入"""
    if not flags:
        return ""

    # 按类型分组
    by_type = {}
    for f in flags:
        t = f["flag_type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(f)

    lines = ["[Reflection] 潜在记忆问题："]
    for flag_type, items in by_type.items():
        type_names = {
            "contradiction": "矛盾",
            "outdated": "过时",
            "hallucination": "幻觉",
            "duplicate": "重复",
        }
        lines.append(f"  - {type_names.get(flag_type, flag_type)}: {len(items)} 条")

    lines.append("  建议：使用 /resolve_flag <id> <demote|merge|false_positive> 处理")
    return "\n".join(lines)


def write_flag(memory_id: str, flag_type: str, evidence: str, confidence: float = 0.8) -> int:
    """写入单条 flag（去重：同 memory_id + flag_type 已 open 则跳过）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 去重检查
        existing = conn.execute(
            """SELECT id FROM memory_flags
               WHERE memory_id = ? AND flag_type = ? AND status = 'open'""",
            (memory_id, flag_type),
        ).fetchone()
        if existing:
            logger.debug(f"[Reflection] Flag already exists for {memory_id}/{flag_type}")
            return existing[0]

        # 插入新 flag
        cursor = conn.execute(
            """INSERT INTO memory_flags
               (memory_id, flag_type, confidence, evidence, status, created_at, source)
               VALUES (?, ?, ?, ?, 'open', ?, 'reflection')""",
            (memory_id, flag_type, confidence, evidence, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()
