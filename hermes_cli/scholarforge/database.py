"""ScholarForge 数据持久化 — SQLite 单文件

设计：每个论文项目 = 一行 projects 表，关联大纲/文献/消息三个子表
存储路径：~/.vermes/scholarforge.db
"""
import os
import sqlite3
import time
import json
import logging
import threading
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DB_PATH = os.path.expanduser("~/.vermes/scholarforge.db")
_lock = threading.Lock()
logger = logging.getLogger("scholarforge.db")


@contextmanager
def get_conn():
    """线程安全的连接获取"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """建表 — 幂等"""
    with _lock, get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            paper_type TEXT DEFAULT '本科论文',
            target_words INTEGER DEFAULT 8000,
            current_model TEXT,
            last_section_key TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            section_key TEXT NOT NULL,
            section_number TEXT,
            section_title TEXT NOT NULL,
            word_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        -- P1-7 migration: add last_section_key column if missing
        

        CREATE TABLE IF NOT EXISTS section_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            section_key TEXT NOT NULL,
            content TEXT DEFAULT '',
            updated_at INTEGER NOT NULL,
            UNIQUE(project_id, section_key),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS literatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            authors TEXT,
            year INTEGER,
            venue TEXT,
            abstract TEXT,
            url TEXT,
            doi TEXT,
            added_at INTEGER NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            agent TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS agent_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL DEFAULT 0,
            agent_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL,
            UNIQUE(project_id, agent_name)
        );

        CREATE TABLE IF NOT EXISTS citation_verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            ref_num INTEGER NOT NULL,
            score REAL DEFAULT 5,
            reason TEXT DEFAULT '',
            verified_at INTEGER NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_outline_project ON outlines(project_id);
        CREATE INDEX IF NOT EXISTS idx_content_project ON section_contents(project_id);
        CREATE INDEX IF NOT EXISTS idx_lit_project ON literatures(project_id);
        CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project_id);
        CREATE INDEX IF NOT EXISTS idx_agent_prov ON agent_providers(project_id);
        CREATE INDEX IF NOT EXISTS idx_cv_project ON citation_verifications(project_id);

        -- P1-5: 版本历史表
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            label TEXT DEFAULT '',
            note TEXT DEFAULT '',
            payload TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_snap_project ON snapshots(project_id);
        """)
        
        # P1-7: add last_section_key to existing databases
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN last_section_key TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        
        # P1-5: add snapshots table to existing databases
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    label TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_project ON snapshots(project_id)")
        except sqlite3.OperationalError as e:
            logger.warning(f"snapshots table migration: {e}")

        # citation_style column for reference formatting (GB/T 7714 default)
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN citation_style TEXT DEFAULT 'gbt7714'")
        except sqlite3.OperationalError:
            pass  # column already exists

        # style_prompt column: learn_style 提取的写作风格指令，write 时自动注入
        try:
            conn.execute("ALTER TABLE projects ADD COLUMN style_prompt TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists

        # outlines.updated_at column (used by save_outline)
        try:
            conn.execute("ALTER TABLE outlines ADD COLUMN updated_at INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists

        # ── 工具使用埋点（用户场景验证：真实使用数据驱动优先级）──
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    ok INTEGER NOT NULL DEFAULT 1,
                    duration_ms INTEGER DEFAULT 0,
                    called_at INTEGER NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_tool ON tool_usage(tool_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_time ON tool_usage(called_at)")
        except sqlite3.OperationalError as e:
            logger.warning(f"tool_usage table migration: {e}")

        # Collection/标签系统
        conn.execute("""
            CREATE TABLE IF NOT EXISTS literature_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                literature_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(literature_id, tag),
                FOREIGN KEY (literature_id) REFERENCES literatures(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lit_tag ON literature_tags(literature_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tag ON literature_tags(tag)")

        # ── Literature Cards（文献知识沉淀，独立于 project，跨会话累积）──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS literature_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                authors TEXT,
                year TEXT,
                venue TEXT,
                doi TEXT,
                url TEXT,
                pdf_url TEXT,
                source TEXT,
                abstract TEXT,
                research_question TEXT,
                methods TEXT,
                datasets TEXT,
                findings TEXT,
                limitations TEXT,
                key_claims TEXT,
                tags TEXT,
                added_at INTEGER NOT NULL,
                UNIQUE(doi, title)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_doi ON literature_cards(doi)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_year ON literature_cards(year)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_source ON literature_cards(source)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_card_title ON literature_cards(title)")

        # 质量护栏报告表（写回闸门产出）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS section_quality (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                section_key TEXT NOT NULL,
                report TEXT NOT NULL,
                checked_at INTEGER NOT NULL,
                UNIQUE(project_id, section_key),
                FOREIGN KEY(project_id) REFERENCES projects(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sq_project ON section_quality(project_id)")


# ═══════════════════════════════════════════════════════════════════
# Project CRUD
# ═══════════════════════════════════════════════════════════════════

def list_projects() -> List[Dict[str, Any]]:
    """列出所有项目（含最近更新时间）"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT p.*,
                   (SELECT COUNT(*) FROM outlines WHERE project_id=p.id) as section_count,
                   (SELECT COALESCE(SUM(word_count),0) FROM outlines WHERE project_id=p.id) as total_words,
                   (SELECT COUNT(*) FROM literatures WHERE project_id=p.id) as literature_count,
                   (SELECT COUNT(*) FROM messages WHERE project_id=p.id) as message_count
            FROM projects p
            ORDER BY updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def create_project(title: str, paper_type: str = "本科论文",
                   target_words: int = 8000) -> Dict[str, Any]:
    """创建项目，根据论文类型自动初始化对应的大纲结构"""
    init_db()
    now = int(time.time())
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (title, paper_type, target_words, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, paper_type, target_words, now, now),
        )
        pid = cur.lastrowid

        # 按论文类型选择大纲模板
        outline_templates = {
            "本科论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("related", "2", "相关理论与技术基础", 0),
                ("method", "3", "系统设计与实现", 0),
                ("result", "4", "测试与分析", 0),
                ("conclusion", "5", "总结与展望", 0),
                ("refs", "", "参考文献", 0),
                ("ack", "", "致谢", 0),
            ],
            "硕士论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "绪论", 0),
                ("related", "2", "文献综述与理论基础", 0),
                ("method", "3", "研究方法与实验设计", 0),
                ("result", "4", "实验结果与分析", 0),
                ("discussion", "5", "讨论", 0),
                ("conclusion", "6", "结论与展望", 0),
                ("refs", "", "参考文献", 0),
                ("appendix", "", "附录", 0),
                ("ack", "", "致谢", 0),
            ],
            "博士论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "绪论", 0),
                ("related", "2", "文献综述", 0),
                ("theory", "3", "理论基础", 0),
                ("method", "4", "研究方法", 0),
                ("experiment1", "5", "实验一", 0),
                ("experiment2", "6", "实验二", 0),
                ("discussion", "7", "综合讨论", 0),
                ("conclusion", "8", "结论与创新点", 0),
                ("refs", "", "参考文献", 0),
                ("appendix", "", "附录", 0),
                ("ack", "", "致谢", 0),
            ],
            "期刊论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("related", "2", "相关工作", 0),
                ("method", "3", "方法", 0),
                ("experiment", "4", "实验", 0),
                ("discussion", "5", "讨论", 0),
                ("conclusion", "6", "结论", 0),
                ("refs", "", "参考文献", 0),
            ],
            "会议论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("related", "2", "相关工作", 0),
                ("method", "3", "方法", 0),
                ("experiment", "4", "实验", 0),
                ("conclusion", "5", "结论", 0),
                ("refs", "", "参考文献", 0),
            ],
            "综述论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("background", "2", "背景与概念", 0),
                ("taxonomy", "3", "分类与体系", 0),
                ("comparison", "4", "方法对比分析", 0),
                ("challenges", "5", "挑战与未来方向", 0),
                ("conclusion", "6", "结论", 0),
                ("refs", "", "参考文献", 0),
            ],
            "开题报告": [
                ("abstract", "", "摘要", 0),
                ("background", "1", "研究背景与意义", 0),
                ("related", "2", "国内外研究现状", 0),
                ("objectives", "3", "研究目标与内容", 0),
                ("method", "4", "研究方法与技术路线", 0),
                ("plan", "5", "进度安排", 0),
                ("feasibility", "6", "可行性分析", 0),
                ("refs", "", "参考文献", 0),
            ],
            "课程论文": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("related", "2", "相关理论概述", 0),
                ("discussion", "3", "分析与讨论", 0),
                ("conclusion", "4", "结论", 0),
                ("refs", "", "参考文献", 0),
            ],
            "调研报告": [
                ("abstract", "", "摘要", 0),
                ("background", "1", "调研背景与目的", 0),
                ("method", "2", "调研方法与对象", 0),
                ("findings", "3", "现状与发现", 0),
                ("analysis", "4", "问题与分析", 0),
                ("suggestions", "5", "建议与对策", 0),
                ("refs", "", "参考文献", 0),
            ],
            "实验报告": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "实验目的与原理", 0),
                ("method", "2", "实验设备与步骤", 0),
                ("result", "3", "实验数据与记录", 0),
                ("analysis", "4", "数据处理与分析", 0),
                ("conclusion", "5", "实验结论与误差分析", 0),
                ("refs", "", "参考文献", 0),
            ],
            "案例分析": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "案例背景", 0),
                ("description", "2", "案例描述", 0),
                ("theory", "3", "理论框架", 0),
                ("analysis", "4", "案例分析", 0),
                ("discussion", "5", "讨论与启示", 0),
                ("conclusion", "6", "结论", 0),
                ("refs", "", "参考文献", 0),
            ],
            "毕业设计": [
                ("abstract", "", "摘要", 0),
                ("intro", "1", "引言", 0),
                ("related", "2", "相关技术基础", 0),
                ("requirements", "3", "需求分析", 0),
                ("design", "4", "系统设计", 0),
                ("implementation", "5", "系统实现", 0),
                ("test", "6", "系统测试", 0),
                ("conclusion", "7", "总结与展望", 0),
                ("refs", "", "参考文献", 0),
                ("ack", "", "致谢", 0),
            ],
        }
        default_outline = outline_templates.get(
            paper_type,
            outline_templates["本科论文"]  # fallback
        )

        for i, (k, n, t, wc) in enumerate(default_outline):
            conn.execute(
                "INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, k, n, t, wc, "pending", i),
            )
            # 初始化空内容
            conn.execute(
                "INSERT INTO section_contents (project_id, section_key, content, updated_at) "
                "VALUES (?, ?, '', ?)",
                (pid, k, now),
            )
        conn.commit()
    # 单独连接读取（避免在 with 内重入 get_conn）
    return get_project(pid)


def get_project(pid: int) -> Optional[Dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            return None
        proj = dict(row)

        # 大纲
        outline_rows = conn.execute(
            "SELECT * FROM outlines WHERE project_id=? ORDER BY sort_order", (pid,)
        ).fetchall()
        proj["outline"] = [dict(r) for r in outline_rows]

        # 各章节内容
        content_rows = conn.execute(
            "SELECT section_key, content FROM section_contents WHERE project_id=?", (pid,)
        ).fetchall()
        proj["contents"] = {r["section_key"]: r["content"] for r in content_rows}

        # 文献数
        proj["literature_count"] = conn.execute(
            "SELECT COUNT(*) c FROM literatures WHERE project_id=?", (pid,)
        ).fetchone()["c"]

        # 文献列表（供评分/共识度/引用核查使用）
        lit_rows = conn.execute(
            "SELECT * FROM literatures WHERE project_id=? ORDER BY added_at", (pid,)
        ).fetchall()
        proj["literatures"] = [dict(r) for r in lit_rows]

        return proj


def update_project(pid: int, **kwargs) -> bool:
    init_db()
    allowed = {"title", "paper_type", "target_words", "current_model", "last_section_key", "citation_style", "style_prompt"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return False
    fields["updated_at"] = int(time.time())
    sql = "UPDATE projects SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, (*fields.values(), pid))
    return True


def touch_project(pid: int):
    """更新 updated_at（章节/内容修改时调用）"""
    with get_conn() as conn:
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (int(time.time()), pid))


def delete_project(pid: int) -> bool:
    init_db()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════════
# Section content
# ═══════════════════════════════════════════════════════════════════

def save_outline(pid: int, outline_sections: list[dict]) -> None:
    """批量保存大纲到 outlines 表（先删后插）"""
    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("DELETE FROM outlines WHERE project_id=?", (pid,))
        for i, sec in enumerate(outline_sections):
            conn.execute(
                """INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, sec.get("id", f"sec_{i}"),
                 sec.get("number", i + 1),
                 sec.get("title", ""),
                 sec.get("wordCount", 0),
                 sec.get("status", "pending"),
                 i, now),
            )
    touch_project(pid)


def get_outline(pid: int) -> list[dict]:
    """获取大纲列表"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT section_key, section_number, section_title, word_count, status FROM outlines WHERE project_id=? ORDER BY sort_order",
            (pid,),
        ).fetchall()
        return [{"id": r["section_key"], "number": r["section_number"], "title": r["section_title"],
                 "wordCount": r["word_count"], "status": r["status"]} for r in rows]


def get_all_sections(pid: int) -> dict[str, str]:
    """获取项目所有章节内容，返回 {section_key: content}"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT section_key, content FROM section_contents WHERE project_id=?",
            (pid,),
        ).fetchall()
        return {r["section_key"]: r["content"] for r in rows}


def save_section_content(pid: int, section_key: str, content: str):
    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO section_contents (project_id, section_key, content, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, section_key) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
        """, (pid, section_key, content, now))
        # 同步更新 outline 的 word_count
        conn.execute(
            "UPDATE outlines SET word_count=? WHERE project_id=? AND section_key=?",
            (len(content), pid, section_key),
        )
    touch_project(pid)


def get_section_content(pid: int, section_key: str) -> str:
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM section_contents WHERE project_id=? AND section_key=?",
            (pid, section_key),
        ).fetchone()
        return row["content"] if row else ""


def delete_section_content(pid: int, section_key: str):
    init_db()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM section_contents WHERE project_id=? AND section_key=?",
            (pid, section_key),
        )


# ═══════════════════════════════════════════════════════════════════
# Literature
# ═══════════════════════════════════════════════════════════════════

def add_literature(pid: int, **kwargs) -> int:
    init_db()
    now = int(time.time())
    authors = kwargs.get("authors", [])
    if isinstance(authors, list):
        authors = json.dumps(authors, ensure_ascii=False)
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO literatures (project_id, title, authors, year, venue, abstract, url, doi, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pid, kwargs.get("title", ""), authors, kwargs.get("year"),
              kwargs.get("venue", ""), kwargs.get("abstract", ""),
              kwargs.get("url", ""), kwargs.get("doi", ""), now))
        lit_id = cur.lastrowid
    touch_project(pid)
    return lit_id


def list_literature(pid: int) -> List[Dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM literatures WHERE project_id=? ORDER BY added_at DESC", (pid,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["authors"] = json.loads(d["authors"]) if d["authors"] else []
            except Exception:
                d["authors"] = []
            result.append(d)
        return result


def delete_literature(lit_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM literatures WHERE id=?", (lit_id,))
        # literature_tags 通过 ON DELETE CASCADE 自动清理
        return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════
# Literature Tags (Collection/标签系统)
# ═══════════════════════════════════════════════════════════════

def add_tag(literature_id: int, tag: str) -> bool:
    """给文献添加标签"""
    tag = tag.strip()
    if not tag:
        return False
    init_db()
    now = int(time.time())
    with _lock, get_conn() as conn:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO literature_tags (literature_id, tag, created_at) VALUES (?, ?, ?)",
                (literature_id, tag, now)
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tag(literature_id: int, tag: str) -> bool:
    """移除文献标签"""
    with _lock, get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM literature_tags WHERE literature_id=? AND tag=?",
            (literature_id, tag)
        )
        return cur.rowcount > 0


def get_tags(literature_id: int) -> List[str]:
    """获取文献的所有标签"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tag FROM literature_tags WHERE literature_id=? ORDER BY tag",
            (literature_id,)
        ).fetchall()
        return [r["tag"] for r in rows]


def get_all_tags(pid: int) -> List[Dict[str, Any]]:
    """获取项目下所有标签及其文献数"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.tag, COUNT(*) as count
            FROM literature_tags t
            JOIN literatures l ON t.literature_id = l.id
            WHERE l.project_id = ?
            GROUP BY t.tag
            ORDER BY t.tag
        """, (pid,)).fetchall()
        return [dict(r) for r in rows]


def get_literature_with_tags(pid: int) -> List[Dict[str, Any]]:
    """获取项目文献列表（含标签）"""
    lits = list_literature(pid)
    if not lits:
        return []
    with get_conn() as conn:
        # 批量查询所有文献的标签
        lit_ids = [l["id"] for l in lits]
        placeholders = ",".join("?" * len(lit_ids))
        tag_rows = conn.execute(
            f"SELECT literature_id, tag FROM literature_tags WHERE literature_id IN ({placeholders})",
            lit_ids
        ).fetchall()
    # 按文献 ID 分组
    tag_map: dict[int, list[str]] = {}
    for r in tag_rows:
        tag_map.setdefault(r["literature_id"], []).append(r["tag"])
    for lit in lits:
        lit["tags"] = tag_map.get(lit["id"], [])
    return lits


# ═══════════════════════════════════════════════════════════════════
# Messages (AI 对话历史)
# ═══════════════════════════════════════════════════════════════════

def add_message(pid: int, agent: str, role: str, content: str) -> int:
    init_db()
    now = int(time.time())
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (project_id, agent, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, agent, role, content, now),
        )
        msg_id = cur.lastrowid
    touch_project(pid)
    return msg_id


def list_messages(pid: int, agent: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    init_db()
    with get_conn() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM messages WHERE project_id=? AND agent=? ORDER BY created_at LIMIT ?",
                (pid, agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages WHERE project_id=? ORDER BY created_at LIMIT ?",
                (pid, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def clear_messages(pid: int, agent: Optional[str] = None) -> int:
    """清空消息 — 删库重建风格的清空"""
    with get_conn() as conn:
        if agent:
            cur = conn.execute(
                "DELETE FROM messages WHERE project_id=? AND agent=?", (pid, agent)
            )
        else:
            cur = conn.execute("DELETE FROM messages WHERE project_id=?", (pid,))
        return cur.rowcount


# ═══════════════════════════════════════════════════════════════════
# Agent-Provider 绑定 — 每个项目每个 Agent 独立选择厂商和模型
# ═══════════════════════════════════════════════════════════════════

SCHOLAR_AGENTS = ["topic", "literature", "outline", "writing", "refinement"]

# 默认分配：不同 Agent 默认用不同厂商（全用 Vermes 已配置的厂商）
DEFAULT_AGENT_PROVIDERS = {
    "topic":       {"provider": "", "model": ""},
    "literature":  {"provider": "", "model": ""},
    "outline":     {"provider": "", "model": ""},
    "writing":     {"provider": "", "model": ""},
    "refinement":  {"provider": "", "model": ""},
}


def get_agent_providers(pid: int) -> Dict[str, Dict[str, str]]:
    """返回项目所有 Agent 的 provider/model 配置
    未显式存储的返回 DEFAULT_AGENT_PROVIDERS 默认值"""
    init_db()
    result = {k: dict(v) for k, v in DEFAULT_AGENT_PROVIDERS.items()}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT agent_name, provider, model FROM agent_providers WHERE project_id=?",
            (pid,),
        ).fetchall()
        for r in rows:
            result[r["agent_name"]] = {"provider": r["provider"], "model": r["model"]}
    return result


def set_agent_provider(pid: int, agent_name: str, provider: str, model: str) -> bool:
    """为某个 Agent 设置 provider/model"""
    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO agent_providers (project_id, agent_name, provider, model, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, agent_name, provider, model, now),
        )
    return True


def reset_agent_provider(pid: int, agent_name: str) -> bool:
    """重置某个 Agent 使用默认模型"""
    init_db()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM agent_providers WHERE project_id=? AND agent_name=?",
            (pid, agent_name),
        )
    return True


def save_citation_verifications(pid: int, results: list[dict]) -> None:
    """保存引用验证结果 — 先清旧数据，再批量写入"""
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM citation_verifications WHERE project_id=?", (pid,))
        now = int(time.time())
        conn.executemany(
            "INSERT INTO citation_verifications (project_id, ref_num, score, reason, verified_at) VALUES (?, ?, ?, ?, ?)",
            [(pid, r.get("ref", 0), r.get("score", 5), r.get("reason", "")[:500], now) for r in results]
        )


def get_citation_verifications(pid: int) -> list[dict]:
    """获取项目的引用验证结果"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ref_num, score, reason, verified_at FROM citation_verifications WHERE project_id=? ORDER BY ref_num",
            (pid,)
        ).fetchall()
        return [{"ref": r[0], "score": r[1], "reason": r[2], "verified_at": r[3]} for r in rows]


# ═══════════════════════════════════════════════════════════════════
# P1-5: 版本快照 (Snapshot)
# ═══════════════════════════════════════════════════════════════════

MAX_SNAPSHOTS_PER_PROJECT = 30

def create_snapshot(pid: int, label: str = "", note: str = "", data: dict = None) -> int:
    """创建快照，payload 为 JSON 序列化的全文+章节内容。超出 MAX_SNAPSHOTS 时淘汰最旧。"""
    init_db()
    import json, time
    payload = json.dumps(data or {}, ensure_ascii=False)
    now = int(time.time())
    with get_conn() as conn:
        sid = conn.execute(
            "INSERT INTO snapshots (project_id, label, note, payload, created_at) VALUES (?,?,?,?,?)",
            (pid, label, note, payload, now)
        ).lastrowid
        # 淘汰最旧的
        count = conn.execute("SELECT COUNT(*) FROM snapshots WHERE project_id=?", (pid,)).fetchone()[0]
        if count > MAX_SNAPSHOTS_PER_PROJECT:
            oldest = conn.execute(
                "SELECT id FROM snapshots WHERE project_id=? ORDER BY created_at ASC LIMIT ?",
                (pid, count - MAX_SNAPSHOTS_PER_PROJECT)
            ).fetchall()
            for (oid,) in oldest:
                conn.execute("DELETE FROM snapshots WHERE id=?", (oid,))
        return sid

def list_snapshots(pid: int) -> list[dict]:
    """列出项目所有快照（倒序，附元信息不含 payload）"""
    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, label, note, LENGTH(payload), created_at FROM snapshots WHERE project_id=? ORDER BY created_at DESC",
            (pid,)
        ).fetchall()
        return [{"id": r[0], "label": r[1], "note": r[2], "size": r[3], "created_at": r[4]} for r in rows]

def get_snapshot(sid: int) -> dict:
    """获取单个快照完整内容"""
    init_db()
    import json
    with get_conn() as conn:
        row = conn.execute("SELECT id, label, note, payload, created_at FROM snapshots WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "label": row[1], "note": row[2], "payload": json.loads(row[3]), "created_at": row[4]}

def delete_snapshot(sid: int) -> bool:
    """删除单个快照"""
    init_db()
    with get_conn() as conn:
        conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))
        return True


def restore_snapshot(sid: int) -> dict:
    """从快照恢复项目状态。

    payload 格式: {"title","paper_type","target_words","citation_style",
                    "outline":[...], "contents":{"section_key": "content"}}

    恢复策略:
    1. 从 payload 读取项目元信息，更新 projects 表
    2. 清空旧大纲，从 payload 重建
    3. 清空旧章节内容，从 payload 重写
    4. 不恢复 literatures（文献库不可逆覆盖）

    返回恢复信息 dict。
    """
    import json, time
    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, project_id, label, note, payload, created_at FROM snapshots WHERE id=?",
            (sid,),
        ).fetchone()
        if not row:
            return {"error": "快照不存在"}

        pid = row["project_id"]
        data = json.loads(row["payload"]) if row["payload"] else {}
        now = int(time.time())

        # 1. 恢复项目元信息
        if data.get("title"):
            conn.execute(
                "UPDATE projects SET title=?, paper_type=?, target_words=?, citation_style=?, updated_at=? WHERE id=?",
                (
                    data.get("title", ""),
                    data.get("paper_type", "本科论文"),
                    data.get("target_words", 8000),
                    data.get("citation_style", "gbt7714"),
                    now,
                    pid,
                ),
            )

        # 2. 恢复大纲
        outline = data.get("outline", [])
        if outline:
            conn.execute("DELETE FROM outlines WHERE project_id=?", (pid,))
            for i, s in enumerate(outline):
                conn.execute(
                    "INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order) VALUES (?,?,?,?,?,?,?)",
                    (
                        pid,
                        s.get("section_key", f"section_{i+1}"),
                        s.get("section_number", str(i+1)),
                        s.get("section_title", s.get("title", "")),
                        s.get("word_count", 0),
                        s.get("status", "pending"),
                        i,
                    ),
                )

        # 3. 恢复章节内容
        contents = data.get("contents", {})
        if contents:
            conn.execute("DELETE FROM section_contents WHERE project_id=?", (pid,))
            for key, content in contents.items():
                conn.execute(
                    "INSERT INTO section_contents (project_id, section_key, content, updated_at) VALUES (?,?,?,?)",
                    (pid, key, content, now),
                )

        return {
            "restored": True,
            "project_id": pid,
            "snapshot_id": sid,
            "label": row["label"],
            "outline_sections": len(outline),
            "content_sections": len(contents),
        }


def create_project_snapshot(project_id: int, label: str = "", note: str = "") -> int:
    """自动快照：捕获当前项目完整状态。

    在关键操作（write/outline/replace_citations）前自动调用。
    payload 包含项目元信息 + 大纲 + 章节内容。
    """
    init_db()
    import json, time

    proj = get_project(project_id)
    if not proj:
        return 0

    data = {
        "title": proj.get("title", ""),
        "paper_type": proj.get("paper_type", ""),
        "target_words": proj.get("target_words", 0),
        "citation_style": proj.get("citation_style", ""),
        "outline": proj.get("outline", []),
        "contents": proj.get("contents", {}),
    }

    return create_snapshot(project_id, label=label, note=note, data=data)


# ══════════════════════════════════════════════════════════════════
# 工具使用埋点（用户场景验证：用真实使用数据驱动工具优先级）
# ══════════════════════════════════════════════════════════════════

def record_tool_usage(tool_name: str, ok: bool = True, duration_ms: int = 0) -> None:
    """记录一次工具调用。失败静默（埋点绝不能影响工具本身）。"""
    try:
        init_db()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO tool_usage (tool_name, ok, duration_ms, called_at) VALUES (?, ?, ?, ?)",
                (tool_name, 1 if ok else 0, int(duration_ms), int(time.time())),
            )
    except Exception:
        pass


def get_tool_usage_stats(days: int = 30) -> List[Dict[str, Any]]:
    """按工具聚合最近 N 天的使用统计（调用次数/成功率/平均耗时/最近使用）。"""
    init_db()
    since = int(time.time()) - days * 86400
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT tool_name,
                   COUNT(*) AS calls,
                   SUM(ok) AS successes,
                   AVG(duration_ms) AS avg_ms,
                   MAX(called_at) AS last_used
            FROM tool_usage
            WHERE called_at >= ?
            GROUP BY tool_name
            ORDER BY calls DESC
        """, (since,)).fetchall()
        return [dict(r) for r in rows]
