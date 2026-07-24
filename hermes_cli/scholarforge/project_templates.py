"""ScholarForge Phase 4 — 模板导入导出

让用户可以从已有项目导出模板，或基于预设模板创建新项目。

核心函数:
- export_project_as_template(pid) → 从项目导出模板 dict
- create_project_from_template(template, title) → 基于模板创建新项目
- list_builtin_templates() → 列出预设模板
- get_builtin_template(name) → 获取预设模板详情

预设模板:
- cs_undergraduate: 计算机本科毕设（含系统设计/实现/测试章节）
- business_master: 工商管理硕士论文（含案例分析/实证研究）
- edu_review: 教育学文献综述
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("scholarforge.project_templates")

# ── 预设模板库 ──

BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "cs_undergraduate": {
        "name": "计算机本科毕设",
        "paper_type": "本科论文",
        "target_words": 12000,
        "citation_style": "gbt7714",
        "outline": [
            {"id": "abstract", "number": "", "title": "摘要", "wordCount": 300},
            {"id": "intro", "number": "1", "title": "绪论", "wordCount": 1000},
            {"id": "related", "number": "2", "title": "相关技术与研究现状", "wordCount": 1500},
            {"id": "method", "number": "3", "title": "系统需求分析与总体设计", "wordCount": 2000},
            {"id": "impl", "number": "4", "title": "系统详细设计与实现", "wordCount": 3000},
            {"id": "test", "number": "5", "title": "系统测试与验证", "wordCount": 1500},
            {"id": "conclusion", "number": "6", "title": "总结与展望", "wordCount": 700},
            {"id": "refs", "number": "", "title": "参考文献", "wordCount": 0},
            {"id": "ack", "number": "", "title": "致谢", "wordCount": 0},
        ],
    },
    "business_master": {
        "name": "工商管理硕士论文",
        "paper_type": "硕士论文",
        "target_words": 30000,
        "citation_style": "gbt7714",
        "outline": [
            {"id": "abstract", "number": "", "title": "摘要", "wordCount": 500},
            {"id": "intro", "number": "1", "title": "绪论", "wordCount": 2000},
            {"id": "related", "number": "2", "title": "文献综述与理论基础", "wordCount": 4000},
            {"id": "method", "number": "3", "title": "研究方法与设计", "wordCount": 3000},
            {"id": "case", "number": "4", "title": "案例分析", "wordCount": 5000},
            {"id": "empirical", "number": "5", "title": "实证研究", "wordCount": 5000},
            {"id": "discussion", "number": "6", "title": "讨论", "wordCount": 3000},
            {"id": "conclusion", "number": "7", "title": "结论与建议", "wordCount": 2000},
            {"id": "refs", "number": "", "title": "参考文献", "wordCount": 0},
            {"id": "ack", "number": "", "title": "致谢", "wordCount": 0},
        ],
    },
    "edu_review": {
        "name": "教育学文献综述",
        "paper_type": "综述论文",
        "target_words": 8000,
        "citation_style": "gbt7714",
        "outline": [
            {"id": "abstract", "number": "", "title": "摘要", "wordCount": 200},
            {"id": "intro", "number": "1", "title": "引言", "wordCount": 800},
            {"id": "concept", "number": "2", "title": "核心概念界定", "wordCount": 1000},
            {"id": "history", "number": "3", "title": "研究历程与阶段", "wordCount": 1500},
            {"id": "themes", "number": "4", "title": "主要研究主题", "wordCount": 2000},
            {"id": "methods", "number": "5", "title": "研究方法述评", "wordCount": 1000},
            {"id": "gap", "number": "6", "title": "研究空白与展望", "wordCount": 1000},
            {"id": "conclusion", "number": "7", "title": "结论", "wordCount": 500},
            {"id": "refs", "number": "", "title": "参考文献", "wordCount": 0},
        ],
    },
}


def list_builtin_templates() -> list[dict[str, Any]]:
    """列出所有预设模板（元信息，不含完整大纲）。"""
    return [
        {
            "key": k,
            "name": v["name"],
            "paper_type": v["paper_type"],
            "target_words": v["target_words"],
            "sections": len(v["outline"]),
        }
        for k, v in BUILTIN_TEMPLATES.items()
    ]


def get_builtin_template(key: str) -> Optional[dict[str, Any]]:
    """获取预设模板详情。"""
    return BUILTIN_TEMPLATES.get(key)


def export_project_as_template(project_id: int) -> dict[str, Any]:
    """从已有项目导出模板。

    捕获项目元信息 + 大纲，不含章节内容（保护原创性）。
    """
    try:
        from hermes_cli.scholarforge.database import get_project
        proj = get_project(project_id)
        if not proj:
            return {"error": "项目不存在"}

        outline = []
        for s in proj.get("outline", []):
            outline.append({
                "id": s.get("section_key", ""),
                "number": s.get("section_number", ""),
                "title": s.get("section_title", ""),
                "wordCount": s.get("word_count", 0),
            })

        return {
            "name": f"导出自《{proj.get('title', '')}》",
            "paper_type": proj.get("paper_type", "本科论文"),
            "target_words": proj.get("target_words", 8000),
            "citation_style": proj.get("citation_style", "gbt7714"),
            "outline": outline,
            "source_project_id": project_id,
            "exported_at": int(time.time()),
        }
    except Exception as e:
        logger.warning("export_project_as_template(%s) failed: %s", project_id, e)
        return {"error": str(e)}


def create_project_from_template(
    template: dict[str, Any],
    title: str,
) -> dict[str, Any]:
    """基于模板创建新项目。

    Args:
        template: 模板 dict（来自 BUILTIN_TEMPLATES 或 export_project_as_template）
        title: 新项目标题

    Returns:
        新项目 dict（含 id），失败返回 {"error": ...}
    """
    try:
        from hermes_cli.scholarforge.database import create_project, save_outline
        paper_type = template.get("paper_type", "本科论文")
        target_words = template.get("target_words", 8000)

        proj = create_project(title=title, paper_type=paper_type, target_words=target_words)
        pid = proj["id"]

        # 覆盖默认大纲为模板大纲
        outline = template.get("outline", [])
        if outline:
            save_outline(pid, outline)

        # 设置引用格式
        citation_style = template.get("citation_style")
        if citation_style:
            from hermes_cli.scholarforge.database import update_project
            update_project(pid, citation_style=citation_style)

        logger.info("create_project_from_template: pid=%s, title=%s, template=%s",
                     pid, title, template.get("name", "builtin"))
        return get_project(pid) if (get_project := __import__(
            "hermes_cli.scholarforge.database", fromlist=["get_project"]).get_project) else proj
    except Exception as e:
        logger.warning("create_project_from_template failed: %s", e)
        return {"error": str(e)}
