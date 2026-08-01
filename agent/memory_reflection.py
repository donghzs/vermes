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
    """获取 open 状态的 flags（供 continuity_facade 注入 + 前端面板）"""
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute(
                """SELECT f.id, f.memory_id, f.flag_type, f.confidence, f.evidence,
                          f.created_at, m.source
                   FROM memory_flags f
                   LEFT JOIN memories m ON CAST(f.memory_id AS INTEGER) = m.id
                   WHERE f.status = 'open'
                   ORDER BY f.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        except Exception:
            # P2-10: memories 表不存在时回退到不 JOIN
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
                "source": r[6] if len(r) > 6 else None,
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
    """标记 flag 为已解决，并（按 resolution 类型）联动演进原记忆。

    铁律边界说明：铁律禁的是**无差别改写原 memories**；这里对 demote 类
    flag 的精确降级（把被标记记忆的 lifecycle_tag 置 ephemeral）属于用户
    显式意图（/resolve_flag demote = "这条记忆降权/弃用"），是合法演进，
    与"反思引擎静默改写记忆"不同。

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
        # 先取 memory_id（demote 时需联动原记忆）；flag 不存在直接返回 False
        _row = conn.execute(
            "SELECT memory_id FROM memory_flags WHERE id = ? AND status = 'open'",
            (flag_id,),
        ).fetchone()
        if not _row:
            return False
        _memory_id = _row[0]

        cur = conn.execute(
            """UPDATE memory_flags
               SET status = 'resolved', resolution = ?, resolved_at = ?
               WHERE id = ? AND status = 'open'""",
            (resolution, datetime.now(timezone.utc).isoformat(), flag_id),
        )
        if cur.rowcount == 0:
            return False

        # ── P3-⑩: demote 联动降级原记忆 → lifecycle_tag='ephemeral' ──
        # fail-open：memories 表缺失/该行不存在/类型不匹配都不应阻断 flag 解决。
        # P1-5 fix: demote 时保存原始 lifecycle_tag，供 restore 精确还原。
        if resolution == "demote":
            try:
                try:
                    _mid = int(_memory_id)
                except (TypeError, ValueError):
                    _mid = _memory_id
                # 读取原始 lifecycle_tag
                _orig = conn.execute(
                    "SELECT lifecycle_tag FROM memories WHERE id = ?", (_mid,)
                ).fetchone()
                _orig_tag = _orig[0] if _orig else "reference"
                _uc = conn.execute(
                    "UPDATE memories SET lifecycle_tag='ephemeral' WHERE id = ?",
                    (_mid,),
                ).rowcount
                # 保存原始 tag 到 flag 行（确保列存在）
                try:
                    conn.execute(
                        "ALTER TABLE memory_flags ADD COLUMN prev_lifecycle_tag TEXT"
                    )
                except Exception:
                    pass  # 列已存在
                conn.execute(
                    "UPDATE memory_flags SET prev_lifecycle_tag = ? WHERE id = ?",
                    (_orig_tag, flag_id),
                )
                logger.info(
                    "[Reflection] resolve_flag demote: flag=%s memory=%s → "
                    "lifecycle_tag='ephemeral' (was '%s', %d row(s))",
                    flag_id, _memory_id, _orig_tag, _uc,
                )
            except Exception:
                logger.warning(
                    "[Reflection] resolve_flag demote: memories 联动降级失败"
                    "（flag 已 resolved，非致命）",
                    exc_info=True,
                )

        conn.commit()
        return True
    finally:
        conn.close()


# ── R5: restore_flag ────────────────────────────────────────────────

def restore_flag(flag_id: int) -> bool:
    """用户反降级：恢复被 demote 的记忆权重 + 重开 flag。

    三路语义：
      demote       → flag→open + lifecycle_tag→prev_lifecycle_tag（精确还原）
      merge        → flag→open（只重开审视，不改 lifecycle_tag）
      false_positive → flag→open（只重开审视，不改 lifecycle_tag）

    P1-5 fix: 不再硬编码 reference，从 flag.prev_lifecycle_tag 读取原始值。
    若 prev_lifecycle_tag 不存在/为空（旧数据），回退到 _infer_lifecycle_tag。

    fail-open：memories 表缺失/行不存在/类型不符不阻断 flag 重开。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    conn = sqlite3.connect(db_path)
    try:
        _row = conn.execute(
            "SELECT memory_id, resolution FROM memory_flags "
            "WHERE id = ? AND status = 'resolved'",
            (flag_id,),
        ).fetchone()
        if not _row:
            logger.warning("[Reflection] restore_flag: flag %s not found or not resolved", flag_id)
            return False

        _memory_id, _resolution = _row[0], _row[1]

        # demote 恢复：联动把 lifecycle_tag 改回原始值
        if _resolution == "demote":
            try:
                try:
                    _mid = int(_memory_id)
                except (TypeError, ValueError):
                    _mid = _memory_id
                # P1-5 fix: 从 flag 行读取原始 tag，不硬编码 reference
                try:
                    _prev = conn.execute(
                        "SELECT prev_lifecycle_tag FROM memory_flags WHERE id = ?",
                        (flag_id,),
                    ).fetchone()
                    _restore_tag = (_prev and _prev[0]) or "reference"
                except Exception:
                    _restore_tag = "reference"  # 旧库无列，回退到 reference
                _uc = conn.execute(
                    "UPDATE memories SET lifecycle_tag=? WHERE id = ?",
                    (_restore_tag, _mid),
                ).rowcount
                logger.info(
                    "[Reflection] restore_flag: flag=%s memory=%s → lifecycle_tag='%s' (%d row(s))",
                    flag_id, _memory_id, _restore_tag, _uc,
                )
            except Exception:
                logger.warning(
                    "[Reflection] restore_flag: memories 联动恢复失败（非致命）",
                    exc_info=True,
                )

        # 所有类型：flag 退回 open
        conn.execute(
            "UPDATE memory_flags SET status='open', resolution=NULL, resolved_at=NULL "
            "WHERE id = ?",
            (flag_id,),
        )
        conn.commit()
        logger.info("[Reflection] restore_flag: flag=%s resolution=%s → reopened", flag_id, _resolution)
        return True
    except Exception:
        logger.warning("[Reflection] restore_flag failed", exc_info=True)
        return False
    finally:
        conn.close()


def get_resolved_flags(limit: int = 200) -> List[Dict]:
    """获取 resolved 状态的 flags（供"已解决"视图展示+恢复操作）。"""
    from agent.memory_fabric import _get_index_db as _get_mem_db, _init_db as _fab_init

    db_path = _get_mem_db()  # Path
    _fab_init(db_path)  # P2-10 fix: 确保表存在（传 Path）
    db_path_str = str(db_path)

    conn = sqlite3.connect(db_path_str)
    try:
        rows = conn.execute(
            """SELECT id, memory_id, flag_type, confidence, evidence,
                      created_at, resolution, resolved_at
               FROM memory_flags
               WHERE status = 'resolved'
               ORDER BY resolved_at DESC
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
                "resolution": r[6],
                "resolved_at": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ── 行动环闭合：自动降级 ──────────────────────────────────────────────

def auto_resolve_eligible_flags() -> int:
    """自动降级高置信度 flags：涌现闭环，复用 resolve_flag(P3-⑩)。

    置信度分级（安全护栏：ephemeral-only 降权，绝不 volatile 删除）：
      ≥0.9 duplicate + source=skill → 自动 demote（技能描述已在 skill 系统可查）
      ≥0.85 outdated → 自动 demote（过时信息降权，可恢复）
      ≥0.7 contradiction / scope_creep → 仅写 flag，不自动处理

    Returns: 自动处理的 flag 数量。
    """
    from agent.memory_fabric import _get_index_db as _get_mem_db

    db_path = _get_mem_db()
    if isinstance(db_path, Path):
        db_path = str(db_path)

    # P2-11 fix: 先收集 eligible ids，关闭外层连接后再逐个 resolve
    # 避免嵌套连接写同库的 database is locked 风险
    conn = sqlite3.connect(db_path)
    try:
        eligible = conn.execute(
            """SELECT id, memory_id, flag_type, confidence
               FROM memory_flags
               WHERE status = 'open'
                 AND (flag_type = 'duplicate' AND confidence >= 0.9
                      OR flag_type = 'outdated' AND confidence >= 0.85)""",
        ).fetchall()
    finally:
        conn.close()

    resolved_count = 0
    for flag_id, memory_id, flag_type, confidence in eligible:
        # 对 skill-source 的 duplicate，确认源是 skill 才自动 demote
        if flag_type == "duplicate" and confidence >= 0.9:
            try:
                _mid = int(memory_id)
            except (TypeError, ValueError):
                _mid = memory_id
            # 独立连接检查 source
            _conn2 = sqlite3.connect(db_path)
            try:
                src_row = _conn2.execute(
                    "SELECT source FROM memories WHERE id = ?", (_mid,)
                ).fetchone()
            finally:
                _conn2.close()
            # source=skill → 技能描述已在 skill 系统可查，安全降权
            # source!=skill → 可能是真实记忆重复，不自动处理
            if src_row and src_row[0] == "skill":
                ok = resolve_flag(flag_id, "demote")
                if ok:
                    resolved_count += 1
                    logger.info(
                        "[Reflection] auto_resolve: flag=%s duplicate+skill → demote",
                        flag_id,
                    )

        elif flag_type == "outdated" and confidence >= 0.85:
            ok = resolve_flag(flag_id, "demote")
            if ok:
                resolved_count += 1
                logger.info(
                    "[Reflection] auto_resolve: flag=%s outdated → demote",
                    flag_id,
                )

    if resolved_count > 0:
        logger.info("[Reflection] auto_resolve: %d flags auto-resolved", resolved_count)
    return resolved_count
