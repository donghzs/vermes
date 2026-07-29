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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vermes_constants import get_vermes_home

logger = logging.getLogger(__name__)

# ── 持久化状态 ─────────────────────────────────────────────────────

def _reflection_state_path() -> Path:
    """镜像 curator .curator_state"""
    return get_vermes_home() / ".reflection_state"


def _load_state() -> Dict:
    """加载反思状态"""
    path = _reflection_state_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
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
        from vermes_constants import get_vermes_home
        cfg_path = get_vermes_home() / "curator_config.json"
        if cfg_path.exists():
            import json as _json
            cfg = _json.loads(cfg_path.read_text())
            return float(cfg.get("min_idle_hours", DEFAULT_MIN_IDLE_HOURS))
    except Exception:
        pass
    return DEFAULT_MIN_IDLE_HOURS


def _is_idle_enough() -> bool:
    """检查是否足够空闲（基于上次反思时间）"""
    state = _load_state()
    if state.get("paused"):
        return False

    last_run = state.get("last_run_at")
    if not last_run:
        return True  # 从未运行过

    try:
        last_dt = datetime.fromisoformat(last_run)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed_hours >= get_reflection_min_idle_hours()
    except Exception:
        return True  # 状态损坏时允许运行


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
        # 增量列（幂等：列已存在则跳过）
        for _col, _ctype in (("resolution", "TEXT"), ("resolved_at", "TEXT")):
            try:
                conn.execute(f"ALTER TABLE memory_flags ADD COLUMN {_col} {_ctype}")
            except sqlite3.OperationalError:
                pass  # 列已存在
        conn.commit()
    finally:
        conn.close()


# ── 核心入口 ────────────────────────────────────────────────────────

def maybe_run_reflection(
    *,
    idle_for_seconds: Optional[float] = None,
    on_summary: Optional[Callable[[str], None]] = None,
) -> None:
    """空闲门控 + 触发反思（镜像 curator.maybe_run_curator）

    Args:
        idle_for_seconds: 调用方提供的空闲秒数；None 表示不检查。
        on_summary: 反思完成后的回调。
    """
    state = _load_state()
    if state.get("paused"):
        logger.debug("[Reflection] Paused, skip")
        return

    if not _is_idle_enough():
        logger.debug("[Reflection] Not idle enough, skip")
        return

    # Idle gating: only enforce when the caller provided a measurement.
    if idle_for_seconds is not None:
        min_idle_s = get_reflection_min_idle_hours() * 3600.0
        if idle_for_seconds < min_idle_s:
            return

    try:
        run_reflection_review()
        if on_summary:
            on_summary("Reflection completed")
    except Exception as e:
        logger.warning(f"[Reflection] Reflection failed: {e} (fail-open)")


def run_reflection_review():
    """执行反思（镜像 curator.run_curator_review）

    R1: 矛盾校核（复用 decision_tracker._check_contradiction）
    R2: 四类 LLM 校核（stale/contradiction_with_new/scope_creep/redundant）
    R3-R4: 待实现（continuity_facade 已注入 + /resolve_flag）
    """
    ensure_reflection_schema()
    logger.info("[Reflection] Starting reflection review...")

    flags_created = 0

    # R1: 矛盾校核
    try:
        flags_created += _scan_contradictions()
    except Exception as e:
        logger.warning(f"[Reflection] R1 contradiction scan failed: {e}")

    # R2: 四类 LLM 校核（stale / contradiction_with_new / scope_creep / redundant）
    try:
        flags_created += _scan_llm_flags()
    except Exception as e:
        logger.warning(f"[Reflection] R2 LLM scan failed: {e}")

    # 更新状态
    state = _load_state()
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["last_summary"] = f"Reflection completed: {flags_created} flag(s) created"
    _save_state(state)

    logger.info("[Reflection] Reflection review done: %d flag(s)", flags_created)


# ── R1: 矛盾校核 ────────────────────────────────────────────────

def _scan_contradictions() -> int:
    """扫描 @decision 标签的记忆，检测两两矛盾。

    复用 decision_tracker._check_contradiction 纯函数。
    只读 memories 表，只 INSERT INTO memory_flags。

    Returns:
        新增 flag 数量
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db
    from agent.decision_tracker import (
        _check_contradiction,
        _extract_decision_keywords,
    )

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        # 读取所有 @decision 记忆（lifecycle_tag=decision）
        rows = conn.execute(
            """SELECT id, fts_content, pointer, scope
               FROM memories
               WHERE lifecycle_tag = 'decision'
               ORDER BY id DESC
               LIMIT 100""",
        ).fetchall()
    finally:
        conn.close()

    if len(rows) < 2:
        logger.debug("[Reflection] R1: <2 decision memories, skip")
        return 0

    # 提取关键词
    decisions = []
    for row in rows:
        mem_id = str(row[0])
        content = row[1] or ""
        keywords = _extract_decision_keywords(content)
        if keywords:  # 跳过无关键词的
            decisions.append({
                "id": mem_id,
                "content": content,
                "keywords": keywords,
            })

    flags_created = 0
    # 两两比对（O(n²) 但 n≤100，可接受）
    for i in range(len(decisions)):
        for j in range(i + 1, len(decisions)):
            new = decisions[i]  # 较新的（DESC 排序）
            old = decisions[j]  # 较旧的

            reason = _check_contradiction(
                new_decision=new["content"],
                old_decision=old["content"],
                new_keywords=new["keywords"],
                old_keywords=old["keywords"],
            )
            if reason:
                flag_id, is_new = write_flag(
                    memory_id=new["id"],
                    flag_type="contradiction",
                    evidence=f"与记忆 #{old['id']} 矛盾: {reason}",
                    confidence=0.7,
                )
                if is_new:
                    flags_created += 1
                    logger.info(
                        "[Reflection] R1: contradiction found "
                        "between #%s and #%s: %s",
                        new["id"], old["id"], reason,
                    )

    logger.info("[Reflection] R1: scanned %d decisions, %d flags created",
                len(decisions), flags_created)
    return flags_created


# ── R2: 四类 LLM 校核 ────────────────────────────────────────────────

# R2 四类问题 → memory_flags.flag_type 映射
_R2_FLAG_TYPES = {
    "stale": "outdated",
    "outdated": "outdated",
    "contradiction": "contradiction",
    "contradiction_with_new": "contradiction",
    "scope_creep": "scope_creep",
    "redundant": "duplicate",
    "duplicate": "duplicate",
}


def _reflection_llm_review(prompt: str) -> Dict:
    """惰性复用 curator._run_llm_review（fork 辅助 agent）。

    失败返回带 error 的结构，由调用方 fail-open 处理。
    测试可通过 monkeypatch 替换本函数，避免真实 LLM 调用。
    """
    try:
        from agent.curator import _run_llm_review
        return _run_llm_review(prompt)
    except Exception as e:
        logger.warning(f"[Reflection] LLM review call failed: {e}")
        return {"final": "", "summary": "", "error": str(e)}


def _build_r2_prompt(content: str) -> str:
    """构造 R2 四类校核 prompt"""
    return (
        "你是一个记忆质量审查器。判断以下记忆是否存在四类问题之一；"
        "若有，返回 JSON 数组，每项格式："
        '{"class": "stale|contradiction_with_new|scope_creep|redundant", '
        '"confidence": 0.0-1.0, "evidence": "简短理由"}。\n'
        "分类说明：stale=事实可能已过时；contradiction_with_new=与已知新信息矛盾；"
        "scope_creep=适用范围被不当泛化；redundant=与其他已知记忆重复。\n"
        "若无问题返回 []。\n\n记忆内容：\n"
        + content[:2000]
    )


def _parse_r2_response(text: str) -> List[Dict]:
    """从 LLM 返回解析 JSON 数组（fail-open：解析失败返回 []）"""
    if not text:
        return []
    try:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        data = json.loads(text[start:end + 1])
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception:
        pass
    return []


def _scan_llm_flags(limit: int = 50) -> int:
    """R2: 四类 LLM 一致性校核。

    读取近期记忆（跳过 @decision/@preference，已由 R1 覆盖），逐条调 LLM
    分类四类问题：stale / contradiction_with_new / scope_creep / redundant。
    只读 memories 表，只 INSERT memory_flags。fail-open。

    Returns:
        新增 flag 数量（仅 is_new）
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT id, fts_content, pointer, scope
               FROM memories
               WHERE lifecycle_tag IS NULL
                  OR lifecycle_tag NOT IN ('decision', 'preference')
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.debug("[Reflection] R2: no candidate memories, skip")
        return 0

    flags_created = 0
    for row in rows:
        mem_id = str(row[0])
        content = row[1] or ""
        if len(content) < 20:
            continue  # 过短记忆无意义
        result = _reflection_llm_review(_build_r2_prompt(content))
        if result.get("error"):
            continue  # fail-open：LLM 失败跳过本条
        for issue in _parse_r2_response(result.get("final", "")):
            ftype = _R2_FLAG_TYPES.get(str(issue.get("class", "")).lower())
            if not ftype:
                continue
            try:
                conf = float(issue.get("confidence", 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            flag_id, is_new = write_flag(
                memory_id=mem_id,
                flag_type=ftype,
                evidence=str(issue.get("evidence", ""))[:500],
                confidence=conf,
            )
            if is_new:
                flags_created += 1
                logger.info(
                    "[Reflection] R2: %s flag for #%s: %s",
                    ftype, mem_id, str(issue.get("evidence", ""))[:80]
                )
    logger.info("[Reflection] R2: scanned %d memories, %d flags created",
                len(rows), flags_created)
    return flags_created


# ── 辅助函数 ────────────────────────────────────────────────────────────

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
            "scope_creep": "范围漂移",
            "hallucination": "幻觉",
            "duplicate": "重复",
        }
        lines.append(f"  - {type_names.get(flag_type, flag_type)}: {len(items)} 条")

    lines.append("  建议：使用 /resolve_flag <id> <demote|merge|false_positive> 处理")
    return "\n".join(lines)


def write_flag(memory_id: str, flag_type: str, evidence: str, confidence: float = 0.8) -> tuple:
    """写入单条 flag（去重：同 memory_id + flag_type 已 open 则跳过）

    Returns:
        (flag_id, is_new) — flag_id 为数据库 ID；is_new 表示是否本次新增。
    """
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
            return (existing[0], False)

        # 插入新 flag
        cursor = conn.execute(
            """INSERT INTO memory_flags
               (memory_id, flag_type, confidence, evidence, status, created_at, source)
               VALUES (?, ?, ?, ?, 'open', ?, 'reflection')""",
            (memory_id, flag_type, confidence, evidence, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return (cursor.lastrowid, True)
    finally:
        conn.close()


# ── R4: resolve_flag ────────────────────────────────────────────────

def get_flag(flag_id: int) -> Optional[Dict]:
    """读取单条 flag（供 API/CLI 校验）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """SELECT id, memory_id, flag_type, confidence, evidence,
                      status, resolution, resolved_at, created_at
               FROM memory_flags WHERE id = ?""",
            (flag_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "memory_id": row[1], "flag_type": row[2],
            "confidence": row[3], "evidence": row[4], "status": row[5],
            "resolution": row[6], "resolved_at": row[7], "created_at": row[8],
        }
    finally:
        conn.close()


def resolve_flag(flag_id: int, resolution: str) -> bool:
    """标记 flag 为已解决。

    仅 UPDATE memory_flags 状态列（status→resolved + resolution + resolved_at），
    **不触碰原 memories 表**，符合铁律（铁律禁的是改原 memories；flag 表自身
    状态演进合法）。

    Args:
        flag_id: flag 主键
        resolution: demote | merge | false_positive
    Returns:
        是否成功（flag 存在且原为 open 才更新）
    """
    if resolution not in ("demote", "merge", "false_positive"):
        logger.warning("[Reflection] Invalid resolution: %s", resolution)
        return False

    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """UPDATE memory_flags
               SET status = 'resolved', resolution = ?, resolved_at = ?
               WHERE id = ? AND status = 'open'""",
            (resolution, datetime.now(timezone.utc).isoformat(), flag_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
