"""
ScholarForge — 项目上下文注入层 (Phase 1)

让 19 个 Agent 工具感知项目：
- load_project_context(project_id) → 从 DB 加载项目状态，格式化为 prompt 前缀
- save_section(project_id, section_key, content) → 写回 section_contents
- save_outline(project_id, sections) → 写回 outlines
- save_papers(project_id, papers) → 写回 literatures

设计原则：
- LLM 调用最小化（上下文用模板拼接，不额外调 LLM）
- 确定性优先（纯 DB 读取）
- fail-open（项目不存在/DB 错误不阻断工具执行）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("scholarforge.project_context")


def load_project_context(project_id: int) -> Optional[dict]:
    """从 DB 加载项目完整状态。

    返回 dict 或 None（项目不存在时）。
    """
    if not project_id or project_id <= 0:
        return None

    try:
        from hermes_cli.scholarforge.database import get_project
        proj = get_project(project_id)
        if not proj:
            return None
        return proj
    except Exception as e:
        logger.warning("load_project_context(%s) failed: %s", project_id, e)
        return None


def format_project_context_prompt(project_id: int) -> str:
    """格式化项目上下文为 LLM prompt 前缀。

    返回空字符串如果项目不存在或无上下文。
    """
    proj = load_project_context(project_id)
    if not proj:
        return ""

    parts = []

    # 基本信息
    title = proj.get("title", "")
    paper_type = proj.get("paper_type", "")
    target_words = proj.get("target_words", 0)
    citation_style = proj.get("citation_style", "")

    if title:
        parts.append(f"【当前论文项目】《{title}》")
    if paper_type:
        parts.append(f"论文类型: {paper_type}")
    if target_words:
        parts.append(f"目标字数: {target_words}")
    if citation_style:
        parts.append(f"引用格式: {citation_style}")

    # 大纲
    outline = proj.get("outline", [])
    if outline:
        parts.append("\n大纲:")
        for s in outline:
            status = s.get("status", "")
            mark = "✅" if status == "done" else ("📝" if status == "writing" else "⬜")
            parts.append(
                f"  {mark} {s.get('section_number', '')} {s.get('section_title', '')}"
                f"（预估 {s.get('word_count', 0)} 字）"
            )

    # 已有章节内容摘要（每章前 200 字）
    contents = proj.get("contents", {})
    if contents:
        written_sections = []
        for key, content in contents.items():
            if content and content.strip():
                preview = content.strip()[:200]
                word_count = len(content)
                written_sections.append(f"  {key}（{word_count} 字）: {preview}...")
        if written_sections:
            parts.append("\n已写章节:")
            written_sections.append("")  # trailing newline
            parts.extend(written_sections)

    # 文献数
    lit_count = proj.get("literature_count", 0)
    if lit_count:
        parts.append(f"\n已收录文献: {lit_count} 篇")

    # 文献列表（最多 5 篇）
    literatures = proj.get("literatures", [])
    if literatures:
        parts.append("近期文献:")
        for lit in literatures[:5]:
            authors = lit.get("authors", "")
            parts.append(
                f"  - {lit.get('title', '')[:60]} ({lit.get('year', '')}) {authors[:30]}"
            )
        if lit_count > 5:
            parts.append(f"  ... 共 {lit_count} 篇")

    if len(parts) <= 1:
        return ""

    return "\n".join(parts) + "\n\n请基于以上项目上下文继续工作。"


def save_section(project_id: int, section_key: str, content: str) -> bool:
    """写回章节内容到 section_contents 表。"""
    if not project_id or project_id <= 0:
        return False

    try:
        from hermes_cli.scholarforge.database import get_conn, init_db
        init_db()
        now = int(time.time())
        with get_conn() as conn:
            # upsert
            existing = conn.execute(
                "SELECT id FROM section_contents WHERE project_id=? AND section_key=?",
                (project_id, section_key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE section_contents SET content=?, updated_at=? WHERE id=?",
                    (content, now, existing["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO section_contents (project_id, section_key, content, updated_at) VALUES (?, ?, ?, ?)",
                    (project_id, section_key, content, now),
                )
            # 更新 projects.updated_at
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return True
    except Exception as e:
        logger.warning("save_section(%s, %s) failed: %s", project_id, section_key, e)
        return False


def save_outline(project_id: int, sections: list[dict]) -> bool:
    """写回大纲到 outlines 表。"""
    if not project_id or project_id <= 0 or not sections:
        return False

    try:
        from hermes_cli.scholarforge.database import get_conn, init_db
        init_db()
        now = int(time.time())
        with get_conn() as conn:
            # 清除旧大纲
            conn.execute("DELETE FROM outlines WHERE project_id=?", (project_id,))
            # 插入新大纲
            for i, s in enumerate(sections):
                conn.execute("""
                    INSERT INTO outlines (project_id, section_key, section_number, section_title, word_count, status, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    project_id,
                    s.get("section_key", f"section_{i+1}"),
                    s.get("section_number", str(i+1)),
                    s.get("title", s.get("section_title", "")),
                    s.get("word_count", 0),
                    s.get("status", "pending"),
                    i,
                ))
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        return True
    except Exception as e:
        logger.warning("save_outline(%s) failed: %s", project_id, e)
        return False


def save_papers(project_id: int, papers: list[dict]) -> int:
    """写回文献到 literatures 表。返回新增数量。"""
    if not project_id or project_id <= 0 or not papers:
        return 0

    from hermes_cli.scholarforge.database import get_conn, init_db
    init_db()
    now = int(time.time())
    added = 0

    try:
        with get_conn() as conn:
            for p in papers:
                title = (p.get("title", "") or "").strip()
                if not title:
                    continue
                doi = (p.get("doi", "") or "").strip()
                # 去重
                if doi:
                    exists = conn.execute(
                        "SELECT id FROM literatures WHERE project_id=? AND doi=?",
                        (project_id, doi),
                    ).fetchone()
                else:
                    exists = conn.execute(
                        "SELECT id FROM literatures WHERE project_id=? AND LOWER(TRIM(title))=LOWER(TRIM(?))",
                        (project_id, title),
                    ).fetchone()
                if exists:
                    continue

                authors = p.get("authors", [])
                if isinstance(authors, list):
                    authors = ", ".join(str(a) for a in authors)

                conn.execute("""
                    INSERT INTO literatures (project_id, title, authors, year, venue, abstract, url, doi, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project_id, title, authors,
                    p.get("year", ""),
                    p.get("venue", ""),
                    p.get("abstract", ""),
                    p.get("url", ""),
                    doi,
                    now,
                ))
                added += 1
            if added:
                conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
    except Exception as e:
        logger.warning("save_papers(%s) failed: %s", project_id, e)

    return added


def list_active_projects() -> list[dict]:
    """列出最近活跃的项目（最多 5 个）。"""
    try:
        from hermes_cli.scholarforge.database import list_projects
        projects = list_projects()
        return projects[:5]
    except Exception as e:
        logger.warning("list_active_projects failed: %s", e)
        return []


def format_active_projects_prompt() -> str:
    """格式化活跃项目列表（用于 turn-1 注入或工具无 project_id 时提示）。"""
    projects = list_active_projects()
    if not projects:
        return ""

    parts = ["【你的论文项目】"]
    for p in projects:
        sections = p.get("section_count", 0)
        words = p.get("total_words", 0)
        lits = p.get("literature_count", 0)
        parts.append(
            f"  #{p['id']} 《{p['title']}》"
            f"（{p.get('paper_type', '')}，{sections} 章/{words} 字/{lits} 文献）"
        )

    if len(projects) == 1:
        parts.append("\n提示：使用 project_id 参数可继续该项目。")
    else:
        parts.append("\n提示：使用 project_id 参数指定要操作的论文项目。")

    return "\n".join(parts)
