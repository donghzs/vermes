"""Change Ledger — 变更通知中心（审批分层 T5）

L1 的语义是「静默执行 + 通知 + 可撤回」。在本模块出现之前，自动执行的
变更只写 `logger.info`，用户不主动打开 EvolutionPanel 就完全不知情 ——
L1 事实上退化成了 L0。本模块补上「通知」这一半。

设计取舍：**不复制事实，只存通知条目**
--------------------------------------
变更的事实已经分别存在 `evolution_proposals`（进化提案）、
`raw_events`/`self_modify_history`（源码改写）等表里。如果这里再存一份
状态，撤回/应用时就要双写两处，迟早不一致。

所以本表只存三样东西：
  1. **事件快照**（title/summary）—— 通知讲的是「当时发生了什么」，
     快照语义天生正确，事后原记录变了也不该改写通知文案；
  2. **未读位**（read_at）—— 这是本模块唯一新增的语义；
  3. **引用**（ref_kind/ref_id）—— 当前状态一律回查源表，不在这里缓存。

分层与未读的对应关系：
  - **L0**（自动处理、不打扰）：落库即已读，只为可追溯，不进角标。
  - **L1**（静默执行、事后告知）：未读，进角标，通常带撤回信息。
  - **L2**（弹窗审批）：落库即已读 —— 用户当场就知情了，事后再红点是噪音。
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# ── 分层 ──
TIER_L0 = "L0"
TIER_L1 = "L1"
TIER_L2 = "L2"

# 只有 L1 需要用户事后知情。L0 不打扰，L2 当场已知情。
_UNREAD_TIERS = {TIER_L1}

# ── 变更种类 ──
KIND_CONFIG_AUTO_APPLY = "config_auto_apply"   # AEGIS 自动调参
KIND_CONFIG_APPLIED = "config_applied"         # 人工批准后应用
KIND_SKILL_ADOPTED = "skill_adopted"           # 技能自动采纳
KIND_CAPABILITY_ACTIVATED = "capability_activated"
KIND_SOURCE_MODIFY = "source_modify"
KIND_ROLLBACK = "rollback"

# 引用的源表，决定 list_changes 回查当前状态的方式
REF_PROPOSAL = "proposal"
REF_SKILL = "skill"


def _db_path():
    from agent.evolution_manager import get_self_model_db
    return get_self_model_db()


def _conn() -> Optional[sqlite3.Connection]:
    """Return a connection to self-model.db, seeding it if this is first run.

    Returns None when the evolution DB can't be reached — the ledger is a
    side channel and must never break the change it is reporting on.
    """
    try:
        from agent.evolution_manager import _get_conn, is_evolution_active
        path = _db_path()
        if not path.exists():
            # 触发正常的 seed 流程，避免造出一个只有 changes 表的半截库
            # （is_evolution_active 幂等且带进程内缓存）。
            try:
                is_evolution_active()
            except Exception as e:
                logger.debug("[ChangeLedger] seed probe failed: %s", e)
            path.parent.mkdir(parents=True, exist_ok=True)
        return _get_conn(str(path))
    except Exception as e:
        logger.warning("[ChangeLedger] cannot open DB: %s", e)
        return None


def ensure_schema() -> bool:
    """Create the agent_changes table if missing. Idempotent."""
    conn = _conn()
    if conn is None:
        return False
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS agent_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            tier TEXT NOT NULL,
            title TEXT,
            summary TEXT,
            detail TEXT,
            target_path TEXT,
            bak_path TEXT,
            retract_deadline TEXT,
            ref_kind TEXT,
            ref_id INTEGER,
            created TEXT NOT NULL,
            read_at TEXT
        )""")
        # 未读角标是高频查询（面板轮询），建索引避免全表扫。
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_unread "
                     "ON agent_changes(read_at, id DESC)")
        conn.commit()
        return True
    except Exception as e:
        logger.warning("[ChangeLedger] ensure_schema failed: %s", e)
        return False


def record_change(*, kind: str, tier: str, title: str, summary: str = "",
                  detail: Optional[Dict[str, Any]] = None,
                  target_path: Optional[str] = None,
                  bak_path: Optional[str] = None,
                  retract_deadline: Optional[str] = None,
                  ref_kind: Optional[str] = None,
                  ref_id: Optional[int] = None) -> Optional[int]:
    """Append one change to the ledger. Returns row id, or None on failure.

    Never raises: the ledger is a side channel. A failed notification must
    not roll back the change it was reporting on — but it IS logged at
    warning level, because a silent L1 is exactly the failure mode this
    module exists to prevent.
    """
    if not ensure_schema():
        logger.warning("[ChangeLedger] schema unavailable — change '%s' goes "
                       "UNNOTIFIED (user will not see it)", title)
        return None
    conn = _conn()
    if conn is None:
        return None
    try:
        now = datetime.now(timezone.utc).isoformat()
        # L0/L2 落库即已读：L0 不该打扰，L2 用户当场已确认过。
        read_at = None if tier in _UNREAD_TIERS else now
        cur = conn.execute(
            """INSERT INTO agent_changes
               (kind, tier, title, summary, detail, target_path, bak_path,
                retract_deadline, ref_kind, ref_id, created, read_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (kind, tier, title, summary,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             target_path, bak_path, retract_deadline, ref_kind, ref_id,
             now, read_at),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as e:
        logger.warning("[ChangeLedger] record_change failed (%s): %s", title, e)
        return None


def _resolve_ref_status(rows: List[Dict[str, Any]]) -> None:
    """Fill in ``ref_status`` by looking the referenced record up in its own
    table. Mutates rows in place.

    状态一律现查，不在本表缓存 —— 撤回/应用只更新源表一处，这里自然跟着变。
    """
    proposal_ids = [r["ref_id"] for r in rows
                    if r.get("ref_kind") == REF_PROPOSAL and r.get("ref_id")]
    if proposal_ids:
        status_by_id: Dict[int, str] = {}
        try:
            from agent.evolution_manager import get_proposal
            for pid in set(proposal_ids):
                p = get_proposal(pid)
                if p:
                    status_by_id[pid] = p.get("status", "")
        except Exception as e:
            logger.debug("[ChangeLedger] ref status lookup failed: %s", e)
            status_by_id = {}
        for r in rows:
            if r.get("ref_kind") == REF_PROPOSAL:
                r["ref_status"] = status_by_id.get(r.get("ref_id"), "")

    skill_ids = [r["ref_id"] for r in rows
                 if r.get("ref_kind") == REF_SKILL and r.get("ref_id")]
    if skill_ids:
        skill_status: Dict[int, str] = {}
        conn = _conn()
        if conn is not None:
            try:
                q = ",".join("?" * len(set(skill_ids)))
                for row in conn.execute(
                    f"SELECT id, status FROM extracted_skills WHERE id IN ({q})",
                    tuple(set(skill_ids)),
                ):
                    skill_status[row[0]] = row[1] or ""
            except Exception as e:
                logger.debug("[ChangeLedger] skill status lookup failed: %s", e)
        for r in rows:
            if r.get("ref_kind") == REF_SKILL:
                r["ref_status"] = skill_status.get(r.get("ref_id"), "")
        # 撤回可行性依赖刚查到的状态，所以必须在这之后重算。
        for r in rows:
            if r.get("ref_kind") == REF_SKILL:
                r["retractable"] = _is_retractable(r)


def list_changes(*, unread_only: bool = False, limit: int = 50,
                 kind: Optional[str] = None,
                 tier: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return ledger entries, newest first, with ``ref_status`` resolved."""
    if not ensure_schema():
        return []
    conn = _conn()
    if conn is None:
        return []
    try:
        conn.row_factory = sqlite3.Row
        clauses, params = [], []
        if unread_only:
            clauses.append("read_at IS NULL")
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if tier:
            clauses.append("tier = ?")
            params.append(tier)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            f"SELECT * FROM agent_changes {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("detail"):
                try:
                    d["detail"] = json.loads(d["detail"])
                except Exception:
                    pass
            d["unread"] = d.get("read_at") is None
            d["retractable"] = _is_retractable(d)
            out.append(d)
        _resolve_ref_status(out)
        return out
    except Exception as e:
        logger.warning("[ChangeLedger] list_changes failed: %s", e)
        return []


def _within_deadline(row: Dict[str, Any]) -> bool:
    deadline = row.get("retract_deadline")
    if not deadline:
        return False
    try:
        return datetime.now(timezone.utc) <= datetime.fromisoformat(deadline)
    except Exception:
        return False


def _is_retractable(row: Dict[str, Any]) -> bool:
    """A change is retractable while it has a live backup and an unexpired
    window. Mirrors the checks in the retract route so the UI can grey the
    button out instead of letting the user click into an error."""
    # 技能采纳没有文件备份 —— 「撤回」就是把它打回 rejected，只要它还是
    # active 且在窗口内就一直可行。用 bak_path 判定会把它误标成不可撤回。
    if row.get("ref_kind") == REF_SKILL:
        return row.get("ref_status") == "active" and _within_deadline(row)
    if not row.get("bak_path"):
        return False
    if not _within_deadline(row):
        return False
    try:
        import os
        if not os.path.exists(row["bak_path"]):
            return False
    except Exception:
        return False
    return True


def unread_count() -> int:
    """Number of changes the user has not been shown yet (drives the badge)."""
    if not ensure_schema():
        return 0
    conn = _conn()
    if conn is None:
        return 0
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM agent_changes WHERE read_at IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        logger.warning("[ChangeLedger] unread_count failed: %s", e)
        return 0


def mark_read(ids: Iterable[int]) -> int:
    """Mark specific entries read. Returns rows affected."""
    ids = [int(i) for i in ids if str(i).lstrip("-").isdigit()]
    if not ids or not ensure_schema():
        return 0
    conn = _conn()
    if conn is None:
        return 0
    try:
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"UPDATE agent_changes SET read_at=? "
            f"WHERE read_at IS NULL AND id IN ({placeholders})",
            [now, *ids],
        )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("[ChangeLedger] mark_read failed: %s", e)
        return 0


def mark_all_read() -> int:
    """Clear the badge. Returns rows affected."""
    if not ensure_schema():
        return 0
    conn = _conn()
    if conn is None:
        return 0
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE agent_changes SET read_at=? WHERE read_at IS NULL", (now,)
        )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("[ChangeLedger] mark_all_read failed: %s", e)
        return 0


def purge_old(max_age_days: int = 30) -> int:
    """Drop read entries older than N days so the ledger doesn't grow forever.

    Unread entries are never purged — an unseen notification is the one thing
    that must survive.
    """
    if not ensure_schema():
        return 0
    conn = _conn()
    if conn is None:
        return 0
    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=max_age_days)).isoformat()
        cur = conn.execute(
            "DELETE FROM agent_changes WHERE read_at IS NOT NULL AND created < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount
    except Exception as e:
        logger.warning("[ChangeLedger] purge_old failed: %s", e)
        return 0
