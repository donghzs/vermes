"""质量护栏前移 — 写回闸门

把质量检查从"用户记得调的工具"变成"写回动作自带的质量闸门"。

分级闸门（按能否自动填充输入分层）：
  Tier 1（本地确定性，每次写回必跑，零联网）:
    - De-AIGC（check_aigc + apply_deaigc_suggestions）
    - 查重（full_plagiarism_check 本地 simhash）
  Tier 2（本地确定性，flag/block 模式跑）:
    - 设计缺陷（detect_design_flaws，自由文本可跑）
  Tier 3（联网/结构化，仅 replace_citations 后或显式工具）:
    - 引用真实性（verify_citation_authenticity，需 papers 列表）
    - 统计一致性（check_statistics_consistency，需结构化 stats）

mode 语义:
  off   — 仅 Tier 1
  flag  — 写回成功，报告附返回 + 存 section_quality（默认）
  block — Tier 2 critical / 假引用 → 拒绝 save_section，返回报告先修
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("scholarforge.quality_gate")


def run_quality_gate(
    project_id: int,
    section_key: str,
    content: str,
    mode: str = "flag",
    stage: str = "write",
) -> tuple[str, str, bool]:
    """写回质量闸门。

    Args:
        project_id: 论文项目 ID
        section_key: 章节标识（如 "introduction"）
        content: 待写回的文本内容
        mode: "off" | "flag" | "block"
        stage: "write" | "replace_citations"

    Returns:
        (content, report_md, blocked)
        - content: 可能经 De-AIGC 净化后的文本（用于落库+返回，修 DB/返回不一致 BUG）
        - report_md: 质量报告（Markdown），空串表示无报告
        - blocked: True 表示拒绝写回
    """
    report_parts: list[str] = []
    blocked = False

    # ── Tier 1: 本地确定性，每次写回必跑，零联网 ──

    # De-AIGC
    try:
        from vermes_cli.scholarforge.plagcheck import check_aigc, apply_deaigc_suggestions

        a = check_aigc(content)
        aigc_score = a.get("aigc_score", a.get("overall_ratio", 0))
        if aigc_score > 0.4:
            cleaned = apply_deaigc_suggestions(content)
            if cleaned != content:
                content = cleaned
                report_parts.append(
                    f"✍️ 已做文风自然化（机械化特征指数 {aigc_score:.0%}，启发式风格度量，非 AI 检测结论）"
                )
    except Exception as e:
        logger.warning("gate aigc failed: %s", e)

    # 查重（本地 simhash）
    try:
        from vermes_cli.scholarforge.plagcheck import full_plagiarism_check

        plag = full_plagiarism_check(content)
        if plag.overall_similarity > 0.3:
            report_parts.append(
                f"⚠️ 查重相似度 {plag.overall_similarity:.1%}，建议关注高重复段落"
            )
    except Exception as e:
        logger.warning("gate plag failed: %s", e)

    # ── Tier 2: 设计缺陷（自由文本可跑，本地） ──
    if mode in ("flag", "block"):
        try:
            from vermes_cli.scholarforge.validators import (
                detect_design_flaws,
                format_design_report,
            )

            flaws = detect_design_flaws(content)
            if flaws:
                report_parts.append(format_design_report(flaws))
                # block 模式下 P0 缺陷拒绝写回
                if mode == "block" and any(f.severity == "P0" for f in flaws):
                    blocked = True
        except Exception as e:
            logger.warning("gate design flaws failed: %s", e)

    report_md = "\n\n---\n\n".join(report_parts) if report_parts else ""

    # ── 报告落库（fail-open，不影响主写回） ──
    if report_md and project_id:
        try:
            _save_quality_report(project_id, section_key, report_md)
        except Exception as e:
            logger.warning("gate report save failed: %s", e)

    return content, report_md, blocked


async def run_citation_gate(
    papers: list[dict],
    mode: str = "flag",
) -> tuple[str, bool]:
    """引用解析后闸门。

    Args:
        papers: 解析出的真实文献列表
        mode: "flag" | "block"

    Returns:
        (report_md, blocked)
    """
    if not papers:
        return "", False

    try:
        from vermes_cli.scholarforge.validators import (
            verify_citation_authenticity,
            format_citation_report,
        )

        checks = await verify_citation_authenticity(
            papers,
            enable_online=(mode != "off"),
        )
        fake = [c for c in checks if not c.verified and c.confidence <= 0.3]
        if fake:
            report = format_citation_report(checks)
            blocked = mode == "block"
            return report, blocked
    except Exception as e:
        logger.warning("citation gate failed: %s", e)

    return "", False


async def run_full_quality_gate(
    project_id: int,
    section_key: str | None = None,
    papers: list[dict] | None = None,
    stats: dict | None = None,
    paper_text: str = "",
    design_info: dict | None = None,
) -> str:
    """显式全量质量检查（scholarforge_quality_gate 工具调用）。

    调 run_all_validators，返回综合报告。
    """
    from vermes_cli.scholarforge.validators import run_all_validators

    # 如果未提供 paper_text，从 DB 读
    if not paper_text and project_id and section_key:
        try:
            from vermes_cli.scholarforge.database import get_section_content

            paper_text = get_section_content(project_id, section_key)
        except Exception:
            pass

    # 如果未提供 papers，从 DB 读
    if not papers and project_id:
        try:
            from vermes_cli.scholarforge.database import get_conn, init_db

            init_db()
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT title, authors, year, venue, doi FROM literatures WHERE project_id=?",
                    (project_id,),
                ).fetchall()
                papers = [dict(r) for r in rows] if rows else None
        except Exception:
            pass

    return await run_all_validators(
        papers=papers,
        stats=stats,
        paper_text=paper_text,
        design_info=design_info,
        enable_online_citation=True,
    )


# ── 报告落库 ──

def _save_quality_report(project_id: int, section_key: str, report_md: str) -> None:
    """将质量报告写入 section_quality 表。"""
    from vermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    now = int(time.time())
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO section_quality (project_id, section_key, report, checked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, section_key) DO UPDATE SET
                report=excluded.report,
                checked_at=excluded.checked_at
        """, (project_id, section_key, report_md, now))


def get_quality_report(project_id: int, section_key: str) -> str:
    """读取已存的质量报告。"""
    from vermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT report FROM section_quality WHERE project_id=? AND section_key=?",
            (project_id, section_key),
        ).fetchone()
        return row["report"] if row else ""


def list_quality_reports(project_id: int) -> list[dict]:
    """列出某项目的全部质量报告（按检查时间倒序）。

    供前端 QualityView 读取。section_key 为空字符串的全量检查统一记为 "__full__"。
    """
    from vermes_cli.scholarforge.database import get_conn, init_db

    init_db()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT section_key, report, checked_at FROM section_quality "
            "WHERE project_id=? ORDER BY checked_at DESC, id DESC",
            (project_id,),
        ).fetchall()
        return [
            {
                "section_key": (r["section_key"] or "__full__"),
                "report": r["report"],
                "checked_at": r["checked_at"],
            }
            for r in rows
        ]


def save_quality_report(project_id: int, section_key: str, report: str) -> None:
    """显式保存一份质量报告（如用户在前端手动触发全量检查）。

    复用 _save_quality_report 的 upsert 逻辑；section_key 空串归入 "__full__"。
    """
    key = section_key or "__full__"
    _save_quality_report(project_id, key, report)
