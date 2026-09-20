"""SkillRouter — prefetch-based skill routing (Phase 1, L2).

Maps the upcoming user message to top-k skill name+description hints via an
independent FTS5 index (NOT the memory ``chunks_fts`` table). Injection goes
through MemoryProvider.prefetch / MemoryManager.prefetch_all — cache-safe,
does not rely on the model voluntarily calling a search tool.

Design constraints (QClaw execution plan):
- One row per skill (name + description + category), never body chunks.
- Separate SQLite DB: ~/.vermes/rag/skills.db with table ``skills_fts``.
- Inject ≤3 hints, each description ≤60 chars; details via skill_view().
- No skill_search tool — pure context injection.
- Fail-open: any error → empty prefetch string.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from vermes_constants import get_vermes_home

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()
_conn_cache: Dict[str, sqlite3.Connection] = {}

MAX_HITS = 3
MAX_DESC_CHARS = 60
_INDEX_TTL_SEC = 600  # rebuild skill index at most every 10 minutes
_index_state: Dict[str, Any] = {"built_at": 0.0, "count": 0}


def _get_skills_db() -> Path:
    return get_vermes_home() / "rag" / "skills.db"


def _get_conn(db_path: str) -> sqlite3.Connection:
    key = str(db_path)
    with _db_lock:
        if key in _conn_cache:
            try:
                _conn_cache[key].execute("SELECT 1")
                return _conn_cache[key]
            except sqlite3.ProgrammingError:
                pass
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn_cache[key] = conn
        return conn


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT '',
            description TEXT DEFAULT '',
            trigger TEXT DEFAULT ''
        )
        """
    )
    # Migrate older schemas that lacked the trigger column.
    try:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(skills)").fetchall()
        }
        if "trigger" not in cols:
            conn.execute("ALTER TABLE skills ADD COLUMN trigger TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("DROP TABLE IF EXISTS skills_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts
            USING fts5(name, description, category, trigger, tokenize='trigram')
            """
        )
    except sqlite3.OperationalError:
        logger.warning("FTS5 trigram unavailable for skills — falling back to unicode61")
        conn.execute("DROP TABLE IF EXISTS skills_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts
            USING fts5(name, description, category, trigger, tokenize='unicode61')
            """
        )
    conn.commit()


def _normalize_trigger(raw: Any) -> str:
    """Normalize frontmatter `trigger` to a single searchable string."""
    if raw is None:
        return ""
    if isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        return ", ".join(parts)
    text = str(raw).strip()
    if not text:
        return ""
    # YAML folded scalars may keep newlines; collapse to commas/spaces
    text = re.sub(r"\s*\n\s*", ", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,")


def _discover_skills() -> List[Dict[str, str]]:
    """Enumerate skills as {name, category, description, trigger} (one row each)."""
    try:
        from agent.skill_utils import (
            get_all_skills_dirs,
            iter_skill_index_files,
            parse_frontmatter,
        )
    except Exception as e:
        logger.debug("skill_utils import failed: %s", e)
        return []

    seen: set[str] = set()
    out: List[Dict[str, str]] = []
    try:
        dirs = get_all_skills_dirs()
    except Exception:
        dirs = []
    for d in dirs or []:
        try:
            if not Path(d).is_dir():
                continue
            for f in iter_skill_index_files(Path(d), "SKILL.md"):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    fm, _ = parse_frontmatter(text)
                    name = str(fm.get("name") or "").strip() or f.parent.name
                    if not name or name in seen:
                        continue
                    desc = str(fm.get("description") or "").strip()
                    # High-value intent signals (QClaw audit A): must be indexed
                    trigger = _normalize_trigger(
                        fm.get("trigger") or fm.get("triggers") or fm.get("trigger_phrases")
                    )
                    try:
                        parts = f.relative_to(d).parts
                        category = parts[0] if len(parts) >= 2 else str(fm.get("category") or "")
                    except Exception:
                        category = str(fm.get("category") or "")
                    seen.add(name)
                    out.append(
                        {
                            "name": name,
                            "category": category or "",
                            "description": desc,
                            "trigger": trigger,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            continue
    return out


def rebuild_index(force: bool = False) -> int:
    """(Re)build skills_fts from the skill library. Returns skill count."""
    import time

    now = time.time()
    if (
        not force
        and _index_state.get("built_at")
        and (now - float(_index_state["built_at"])) < _INDEX_TTL_SEC
    ):
        return int(_index_state.get("count") or 0)

    db_path = _get_skills_db()
    try:
        _init_db(db_path)
        skills = _discover_skills()
        conn = _get_conn(str(db_path))
        conn.execute("DELETE FROM skills_fts")
        conn.execute("DELETE FROM skills")
        for s in skills:
            cur = conn.execute(
                "INSERT INTO skills (name, category, description, trigger) VALUES (?, ?, ?, ?)",
                (s["name"], s["category"], s["description"], s.get("trigger") or ""),
            )
            # Index trigger alongside name/description so short Chinese
            # intent phrases (写论文 etc.) hit without English descriptions.
            conn.execute(
                "INSERT INTO skills_fts (rowid, name, description, category, trigger) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    cur.lastrowid,
                    s["name"],
                    s["description"],
                    s["category"],
                    s.get("trigger") or "",
                ),
            )
        conn.commit()

        _index_state["built_at"] = now
        _index_state["count"] = len(skills)
        return len(skills)
    except Exception as e:
        logger.debug("SkillRouter rebuild_index failed: %s", e)
        return int(_index_state.get("count") or 0)


def _fts_query(query: str) -> str:
    """Build an FTS5 MATCH expression (trigram-friendly, fail-soft)."""
    safe = re.sub(r"[^\w一-鿿\s]", " ", query).strip()
    if not safe:
        return ""
    terms: List[str] = []
    for word in safe.split():
        cjk = [ch for ch in word if "一" <= ch <= "鿿"]
        ascii_part = "".join(ch for ch in word if ch not in cjk)
        if ascii_part and len(ascii_part) >= 3:
            terms.append(ascii_part)
        if len(cjk) >= 3:
            for i in range(len(cjk) - 2):
                terms.append("".join(cjk[i : i + 3]))
        elif len(cjk) >= 1:
            # short CJK: try the whole token as a phrase
            terms.append("".join(cjk))
    if not terms:
        return ""
    return " OR ".join(f'"{t}"' for t in terms[:8])


def _query_tokens(query: str) -> List[str]:
    """Extract scored tokens: ASCII words + CJK runs/2-grams (deduped)."""
    tokens: List[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", query):
        tokens.append(word.lower())
    for run in re.findall(r"[一-鿿]{2,}", query):
        tokens.append(run)
        if len(run) > 2:
            for i in range(len(run) - 1):
                tokens.append(run[i : i + 2])
    seen: set[str] = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


_NEGATION_MARKERS = (
    "not for",
    "not-for",
    "does not",
    "doesn't",
    "do not",
    "don't",
    "is not",
    "isn't",
    "are not",
    "aren't",
    "no ",
    "without ",
    "except ",
    "exclude",
    "不支持",
    "不适用",
    "不用于",
    "不针对",
    "不能",
    "无法",
    "不含",
    "不包括",
    "非本技能",
    "不处理",
)


def _positive_text(text: str) -> str:
    """Drop negation scopes so exclusion notes don't earn relevance score.

    QClaw round2 B: weather descriptions often say
    "NOT for: historical weather data, ... meteorological analysis."
    Both query tokens appear after a negation marker — they must not count.
    """
    if not text:
        return ""
    t = str(text)
    cut = len(t)
    low = t.lower()
    for marker in _NEGATION_MARKERS:
        idx = low.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return t[:cut]


def _hit_score(hit: Dict[str, str], tokens: List[str]) -> int:
    # Score only affirmative description/name/category/trigger text.
    # Negation scopes (NOT for / 不支持 / …) are stripped first (audit B).
    parts = [
        hit.get("name") or "",
        hit.get("category") or "",
        _positive_text(hit.get("description") or ""),
        _positive_text(hit.get("trigger") or ""),
    ]
    blob = " ".join(parts).lower()
    return sum(1 for t in tokens if t.lower() in blob)



def _required_score(tokens: List[str]) -> int:
    """QClaw audit B: multi-token queries need ≥2 distinct hits.

    Single-token queries still work when the token is length ≥3; short
    single tokens (<3) are too noisy and return no LIKE/filtered hits.
    """
    if not tokens:
        return 10**9
    if len(tokens) == 1:
        return 1 if len(tokens[0]) >= 3 else 10**9
    return 2


def search_skills(query: str, limit: int = MAX_HITS) -> List[Dict[str, str]]:
    """Hybrid search: FTS5 + token-score filter, then LIKE fallback.

    Both paths share the same precision rule (QClaw audit B): a hit must
    score at least `_required_score(tokens)` against name/description/trigger
    so single incidental words (e.g. "analysis" in weather) do not win.
    Frontmatter `trigger` is indexed (QClaw audit A) so short Chinese
    intents like 「写论文」 hit skills with English descriptions.
    """
    if not query or not query.strip():
        return []
    rebuild_index()
    db_path = _get_skills_db()
    try:
        conn = _get_conn(str(db_path))
    except Exception as e:
        logger.debug("SkillRouter db open failed: %s", e)
        return []

    tokens = _query_tokens(query)
    required = _required_score(tokens)

    match = _fts_query(query)
    if match:
        try:
            rows = conn.execute(
                """
                SELECT s.name, s.category, s.description, s.trigger
                FROM skills_fts f
                JOIN skills s ON s.id = f.rowid
                WHERE skills_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match, max(int(limit) * 5, 20)),
            ).fetchall()
            hits = [
                {
                    "name": r[0] or "",
                    "category": r[1] or "",
                    "description": r[2] or "",
                    "trigger": r[3] or "",
                }
                for r in rows
                if r and r[0]
            ]
            filtered = [h for h in hits if _hit_score(h, tokens) >= required]
            if filtered:
                filtered.sort(key=lambda h: (-_hit_score(h, tokens), h["name"]))
                return filtered[:limit]
        except Exception as e:
            logger.debug("SkillRouter FTS search failed: %s", e)

    # LIKE fallback (also used when FTS hits fail the precision filter)
    try:
        if not tokens:
            return []
        all_rows = conn.execute(
            "SELECT name, category, description, COALESCE(trigger, '') FROM skills"
        ).fetchall()
        scored: List[tuple[int, Dict[str, str]]] = []
        for name, category, description, trigger in all_rows:
            hit = {
                "name": name or "",
                "category": category or "",
                "description": description or "",
                "trigger": trigger or "",
            }
            score = _hit_score(hit, tokens)
            if score >= required:
                scored.append((score, hit))
        scored.sort(key=lambda x: (-x[0], x[1]["name"]))
        return [item for _, item in scored[:limit]]
    except Exception as e:
        logger.debug("SkillRouter LIKE fallback failed: %s", e)
        return []


def format_hints(hits: List[Dict[str, str]]) -> str:
    """Format top-k skill hints for prefetch injection."""
    if not hits:
        return ""
    lines = ["[相关技能]"]
    for h in hits[:MAX_HITS]:
        desc = (h.get("description") or "").replace("\n", " ").strip()
        if not desc:
            desc = (h.get("trigger") or "").replace("\n", " ").strip()
        if len(desc) > MAX_DESC_CHARS:
            desc = desc[: MAX_DESC_CHARS - 1] + "…"
        cat = h.get("category") or ""
        label = f"{h['name']} ({cat})" if cat else h["name"]
        lines.append(f"- {label}：{desc}" if desc else f"- {label}")
    lines.append("（细节用 skill_view(name) 查看）")
    return "\n".join(lines)



class SkillRouter(MemoryProvider):
    """Prefetch-only skill routing provider (no tools)."""

    def __init__(self):
        self._initialized = False
        self._session_id = ""

    @property
    def name(self) -> str:
        return "skill_router"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or ""
        try:
            n = rebuild_index()
            self._initialized = True
            logger.info("SkillRouter initialized (indexed %d skills)", n)
        except Exception as e:
            logger.debug("SkillRouter initialize failed (fail-open): %s", e)
            self._initialized = True

    def system_prompt_block(self) -> str:
        return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            if not query or not query.strip():
                return ""
            hits = search_skills(query, limit=MAX_HITS)
            return format_hints(hits)
        except Exception as e:
            logger.debug("SkillRouter prefetch failed (fail-open): %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        return None

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        scope: str = "",
    ) -> None:
        return None

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        return "{}"
