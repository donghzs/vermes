"""
ScholarForge 期刊质量评估与引用规范模块

功能：
1. 期刊白名单/黑名单评估 — CSSCI/北大核心/SCI/SSCI 白名单 + 掠夺性期刊警告
2. 文献质量评分 — 基于 FWCI、引用数、期刊分区、撤稿状态的综合评分
3. GB/T 7714-2015 引用格式生成 — 中国学位论文标准引用格式
4. 中文学位论文规范 — 四段式摘要、章节编号、附录格式
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("scholarforge.quality")


# ═══════════════════════════════════════════════════════════════════
# 期刊白名单/黑名单
# ═══════════════════════════════════════════════════════════════════

# 中文核心期刊（北大核心 2023 版，部分高频期刊）
CSSCI_JOURNALS = {
    # 教育学
    "学前教育研究", "教育研究", "比较教育研究", "高等教育研究", "华东师范大学学报（教育科学版）",
    "全球教育展望", "教育发展研究", "中国教育学刊", "电化教育研究", "开放教育研究",
    "教师教育研究", "教育学报", "教育科学", "外国教育研究", "教育理论与实践",
    "课程·教材·教法", "中国特殊教育", "学前教育", "早期教育",
    # 心理学
    "心理学报", "心理科学", "心理发展与教育", "心理科学进展", "中国临床心理学杂志",
    # 社会学
    "社会学研究", "中国社会科学", "社会", "青年研究",
    # 管理学
    "管理世界", "南开管理评论", "中国管理科学",
    # 综合
    "北京大学学报（哲学社会科学版）", "清华大学学报（哲学社会科学版）",
    "复旦学报（社会科学版）", "南京大学学报（哲学·人文科学·社会科学）",
}

# SCI/SSCI 期刊（常见顶级期刊，通过 ISSN 或期刊名匹配）
SCI_SSCI_TOP = {
    "Nature", "Science", "Cell", "PNAS", "Nature Communications",
    "PLOS ONE", "Scientific Reports",
    "Child Development", "Developmental Psychology", "Educational Researcher",
    "American Educational Research Journal", "Review of Educational Research",
    "Journal of Educational Psychology", "Early Childhood Research Quarterly",
    "Computers & Education", "Learning and Instruction",
    "Psychological Science", "Journal of Personality and Social Psychology",
}

# 掠夺性期刊/低质量期刊特征（部分已知列表）
PREDATORY_JOURNALS = {
    "International Journal of Science and Research",
    "IOSR Journal of Research & Method in Education",
    "International Journal of Engineering and Advanced Technology",
    "Journal of Critical Reviews",
    "International Journal of Innovative Technology and Exploring Engineering",
    "European Journal of Molecular & Clinical Medicine",
    "Advances in Social Sciences",  # 常见掠夺性出版商
    "Modern Education Forum",       # Crossref 中的低质量中文期刊
    "International Education Forum",
    "Teaching Method Innovation and Practice",
}

# 掠夺性期刊特征关键词（用于启发式检测）
PREDATORY_KEYWORDS = [
    "international journal of", "global journal of", "world journal of",
    "american journal of", "european journal of", "asian journal of",
]


@dataclass
class JournalQuality:
    """期刊质量评估结果"""
    tier: str          # "top" / "core" / "normal" / "low" / "predatory"
    label: str         # 显示标签
    color: str         # 颜色类名
    warning: str = ""  # 警告信息
    score_bonus: int = 0  # 质量加分 (top=20, core=10, normal=0, low=-10, predatory=-30)


def assess_journal_quality(venue: str, fwci: Optional[float] = None, is_retracted: bool = False) -> JournalQuality:
    """评估期刊/出版物质量等级

    Args:
        venue: 期刊名或出版物名
        fwci: OpenAlex 领域加权引用影响因子（可选）
        is_retracted: 是否被撤稿

    Returns:
        JournalQuality 质量评估结果
    """
    if not venue:
        return JournalQuality(tier="unknown", label="未知", color="gray")

    venue_clean = venue.strip()

    # 撤稿论文直接标记
    if is_retracted:
        return JournalQuality(
            tier="predatory", label="⚠️已撤稿", color="red",
            warning="该论文已被撤稿，不建议引用", score_bonus=-50
        )

    # 检查掠夺性期刊
    if venue_clean in PREDATORY_JOURNALS:
        return JournalQuality(
            tier="predatory", label="🚫掠夺性期刊", color="red",
            warning=f"'{venue_clean}' 疑似掠夺性/低质量期刊，不建议引用", score_bonus=-30
        )

    # 检查掠夺性关键词（启发式）
    venue_lower = venue_clean.lower()
    for kw in PREDATORY_KEYWORDS:
        if kw in venue_lower and venue_clean not in SCI_SSCI_TOP:
            # 进一步检查：如果 FWCI 很低，更可能是掠夺性
            if fwci is not None and fwci < 0.5:
                return JournalQuality(
                    tier="low", label="⚠️低质量", color="orange",
                    warning=f"'{venue_clean}' 可能是低质量期刊 (FWCI={fwci:.1f})", score_bonus=-10
                )

    # 检查中文核心期刊
    if venue_clean in CSSCI_JOURNALS:
        return JournalQuality(
            tier="core", label="⭐北大核心/CSSCI", color="blue", score_bonus=10
        )

    # 检查 SCI/SSCI 顶级期刊
    if venue_clean in SCI_SSCI_TOP:
        return JournalQuality(
            tier="top", label="🏆SCI/SSCI", color="purple", score_bonus=20
        )

    # 根据 FWCI 评估
    if fwci is not None:
        if fwci >= 3.0:
            return JournalQuality(tier="top", label="🏆高影响", color="purple", score_bonus=15)
        elif fwci >= 1.5:
            return JournalQuality(tier="core", label="⭐良好", color="blue", score_bonus=5)
        elif fwci >= 0.5:
            return JournalQuality(tier="normal", label="普通", color="gray", score_bonus=0)
        else:
            return JournalQuality(
                tier="low", label="⚠️低引用", color="orange",
                warning=f"FWCI={fwci:.1f}，引用影响力较低", score_bonus=-5
            )

    # 无 FWCI 数据，检查中文期刊特征
    # 正规中文期刊通常有"学报""研究""教育"等关键词
    if any(kw in venue_clean for kw in ["学报", "研究", "教育", "心理", "社会", "管理"]):
        return JournalQuality(tier="normal", label="中文期刊", color="gray", score_bonus=0)

    return JournalQuality(tier="normal", label="普通", color="gray", score_bonus=0)


def filter_quality_papers(papers: list, min_tier: str = "low") -> list:
    """过滤低质量论文

    Args:
        papers: PaperResult/PaperCard 列表
        min_tier: 最低质量等级 ("top" > "core" > "normal" > "low" > "predatory")

    Returns:
        过滤后的论文列表（掠夺性期刊排除）
    """
    tier_order = {"top": 5, "core": 4, "normal": 3, "low": 2, "predatory": 1, "unknown": 3}
    min_score = tier_order.get(min_tier, 2)

    result = []
    for p in papers:
        venue = getattr(p, "venue", "") or ""
        fwci = getattr(p, "fwci", None)
        is_retracted = getattr(p, "is_retracted", False)

        quality = assess_journal_quality(venue, fwci, is_retracted)
        if tier_order.get(quality.tier, 3) >= min_score:
            result.append(p)
        else:
            logger.info(f"Filtered low-quality paper: '{getattr(p, 'title', '')}' from '{venue}' ({quality.label})")

    return result


# ═══════════════════════════════════════════════════════════════════
# GB/T 7714-2015 引用格式生成
# ═══════════════════════════════════════════════════════════════════

def format_gbt7714(paper, ref_num: int) -> str:
    """生成 GB/T 7714-2015 标准引用格式

    格式：[序号] 作者. 题名[J]. 刊名, 年份, 卷(期): 页码.
         [序号] 作者. 题名[M]. 出版地: 出版者, 年份.
         [序号] 作者. 题名[D]. 保存地: 保存单位, 年份.

    Args:
        paper: PaperResult/PaperCard/dict 论文对象
        ref_num: 引用序号

    Returns:
        GB/T 7714 格式的引用字符串
    """
    # 统一获取字段
    if hasattr(paper, "to_dict"):
        d = paper.to_dict()
    elif isinstance(paper, dict):
        d = paper
    else:
        d = {
            "title": getattr(paper, "title", ""),
            "authors": getattr(paper, "authors", []),
            "year": getattr(paper, "year", ""),
            "venue": getattr(paper, "venue", ""),
            "doi": getattr(paper, "doi", ""),
            "publication_type": getattr(paper, "publication_type", ""),
        }

    authors = d.get("authors", [])
    title = d.get("title", "").strip()
    year = d.get("year", "")
    venue = d.get("venue", "").strip()
    doi = d.get("doi", "")
    pub_type = d.get("publication_type", "")

    # 作者格式：前3位，超过加"等"
    if isinstance(authors, list) and authors:
        if len(authors) <= 3:
            authors_str = ", ".join(authors)
        else:
            authors_str = ", ".join(authors[:3]) + ", 等"
    else:
        authors_str = "佚名"

    # 判断文献类型
    # [J] 期刊文章, [M] 专著, [D] 学位论文, [C] 会议论文, [R] 报告, [EB/OL] 电子文献
    type_marker = "[J]"  # 默认期刊
    type_lower = (pub_type or "").lower()
    venue_lower = venue.lower()

    if "dissertation" in type_lower or "thesis" in type_lower or "学位论文" in venue:
        type_marker = "[D]"
    elif "conference" in type_lower or "proceedings" in type_lower:
        type_marker = "[C]"
    elif "book" in type_lower or "monograph" in type_lower:
        type_marker = "[M]"
    elif "preprint" in type_lower or "arxiv" in venue_lower:
        type_marker = "[EB/OL]"
    elif doi:
        type_marker = "[J/OL]"  # 有DOI的期刊电子版

    # 组装引用
    ref = f"[{ref_num}] {authors_str}. {title}{type_marker}. "

    if type_marker in ("[D]",):
        # 学位论文：保存地: 保存单位, 年份
        ref += f"{venue}, {year}."
    elif type_marker in ("[M]",):
        # 专著：出版地: 出版者, 年份
        ref += f"{venue}, {year}."
    elif type_marker in ("[C]",):
        # 会议：会议名, 会议地, 年份
        ref += f"{venue}, {year}."
    elif type_marker in ("[EB/OL]",):
        # 电子文献：URL + DOI
        url = d.get("url", "")
        if doi:
            ref += f"{year}. DOI: {doi}."
        elif url:
            ref += f"{year}. {url}."
        else:
            ref += f"{year}."
    else:
        # 期刊文章：刊名, 年份
        if venue:
            ref += f"{venue}, {year}."
        else:
            ref += f"{year}."
        if doi:
            ref += f" DOI: {doi}."

    return ref


def format_all_references_gbt7714(papers: list) -> str:
    """批量生成 GB/T 7714 格式参考文献列表

    Args:
        papers: 论文列表

    Returns:
        完整参考文献列表文本
    """
    lines = ["## 参考文献", ""]
    for i, p in enumerate(papers, 1):
        lines.append(format_gbt7714(p, i))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# APA 7th 引用格式
# ═══════════════════════════════════════════════════════════════════

def format_apa7(paper, ref_num: int = 0) -> str:
    """生成 APA 7th 标准引用格式

    格式：Author, A. A., & Author, B. B. (Year). Title. Journal Name, vol(issue), pages.
    """
    if hasattr(paper, "to_dict"):
        d = paper.to_dict()
    elif isinstance(paper, dict):
        d = paper
    else:
        d = {
            "title": getattr(paper, "title", ""),
            "authors": getattr(paper, "authors", []),
            "year": getattr(paper, "year", ""),
            "venue": getattr(paper, "venue", ""),
            "doi": getattr(paper, "doi", ""),
        }

    authors = d.get("authors", [])
    title = d.get("title", "").strip()
    year = d.get("year", "")
    venue = d.get("venue", "").strip()
    doi = d.get("doi", "")

    # APA 作者格式：Last, F. M., & Last, F. M.
    if isinstance(authors, list) and authors:
        formatted_authors = []
        for a in authors[:20]:
            parts = a.strip().split()
            if len(parts) >= 2:
                last = parts[-1]
                initials = ". ".join(p[0].upper() for p in parts[:-1] if p) + "."
                formatted_authors.append(f"{last}, {initials}")
            else:
                formatted_authors.append(a)
        if len(authors) > 20:
            formatted_authors.append("... " + authors[-1])
        if len(formatted_authors) == 1:
            authors_str = formatted_authors[0]
        else:
            authors_str = ", ".join(formatted_authors[:-1]) + ", & " + formatted_authors[-1]
    else:
        authors_str = "Anonymous"

    ref = f"{authors_str} ({year}). {title}."

    if venue:
        ref += f" *{venue}*."
    if doi:
        ref += f" https://doi.org/{doi}"

    if ref_num:
        return f"{ref_num}. {ref}"
    return ref


# ═══════════════════════════════════════════════════════════════════
# 中文学位论文写作规范
# ═══════════════════════════════════════════════════════════════════

# 论文类型 → 第四章结构模板
THESIS_CHAPTER_TEMPLATES = {
    "实验研究": {
        "chapter_4_sections": [
            "## 4.1 实验准备",
            "### 4.1.1 实验对象",
            "### 4.1.2 实验设计",
            "### 4.1.3 实验材料与工具",
            "### 4.1.4 实验程序",
            "## 4.2 数据收集与处理",
            "### 4.2.1 数据收集方法",
            "### 4.2.2 数据编码与录入",
            "### 4.2.3 统计分析方法",
            "## 4.3 实验结果",
            "### 4.3.1 描述性统计",
            "### 4.3.2 假设检验",
            "### 4.3.3 效应量分析",
            "## 4.4 质性分析",
            "### 4.4.1 观察记录分析",
            "### 4.4.2 访谈材料分析",
        ],
        "abstract_format": "目的-方法-结果-结论",
        "statistics_required": True,
    },
    "调查研究": {
        "chapter_4_sections": [
            "## 4.1 调查实施",
            "### 4.1.1 调查对象",
            "### 4.1.2 调查工具",
            "### 4.1.3 调查过程",
            "## 4.2 数据处理",
            "## 4.3 调查结果",
            "### 4.3.1 描述性统计",
            "### 4.3.2 差异性检验",
            "### 4.3.3 相关分析",
            "## 4.4 结果讨论",
        ],
        "abstract_format": "目的-方法-结果-结论",
        "statistics_required": True,
    },
    "个案研究": {
        "chapter_4_sections": [
            "## 4.1 个案背景",
            "## 4.2 研究过程",
            "### 4.2.1 观察阶段",
            "### 4.2.2 干预阶段",
            "### 4.2.3 追踪阶段",
            "## 4.3 资料分析",
            "## 4.4 研究发现",
        ],
        "abstract_format": "目的-方法-发现-启示",
        "statistics_required": False,
    },
    "行动研究": {
        "chapter_4_sections": [
            "## 4.1 第一轮行动",
            "### 4.1.1 计划",
            "### 4.1.2 行动",
            "### 4.1.3 观察",
            "### 4.1.4 反思",
            "## 4.2 第二轮行动",
            "### 4.2.1 计划",
            "### 4.2.2 行动",
            "### 4.2.3 观察",
            "### 4.2.4 反思",
            "## 4.3 行动效果评估",
        ],
        "abstract_format": "问题-行动-结果-反思",
        "statistics_required": False,
    },
    "文献研究": {
        "chapter_4_sections": [
            "## 4.1 文献检索与筛选",
            "## 4.2 文献编码与分析",
            "## 4.3 研究发现",
            "### 4.3.1 主题分类",
            "### 4.3.2 发展脉络",
            "### 4.3.3 研究趋势",
        ],
        "abstract_format": "目的-方法-发现-结论",
        "statistics_required": False,
    },
}

# 中文学位论文摘要四段式模板
ABSTRACT_TEMPLATES = {
    "实验研究": """## 摘要

**目的**：{purpose}

**方法**：{method}

**结果**：{result}

**结论**：{conclusion}

## 关键词

{keywords}""",
    "调查研究": """## 摘要

**目的**：{purpose}

**方法**：{method}

**结果**：{result}

**结论**：{conclusion}

## 关键词

{keywords}""",
    "个案研究": """## 摘要

**目的**：{purpose}

**方法**：{method}

**发现**：{finding}

**启示**：{implication}

## 关键词

{keywords}""",
    "行动研究": """## 摘要

**问题**：{problem}

**行动**：{action}

**结果**：{result}

**反思**：{reflection}

## 关键词

{keywords}""",
}


def get_chapter_template(paper_type: str, chapter_num: int = 4) -> list[str]:
    """获取指定论文类型的章节模板

    Args:
        paper_type: 论文类型（实验研究/调查研究/个案研究/行动研究/文献研究）
        chapter_num: 章节号（默认第4章）

    Returns:
        章节结构模板列表
    """
    template = THESIS_CHAPTER_TEMPLATES.get(paper_type, THESIS_CHAPTER_TEMPLATES["实验研究"])
    sections = template.get("chapter_4_sections", [])

    # 替换章节号
    if chapter_num != 4:
        old_prefix = "4."
        new_prefix = f"{chapter_num}."
        sections = [s.replace(old_prefix, new_prefix) for s in sections]

    return sections


def get_abstract_prompt(paper_type: str) -> str:
    """获取指定论文类型的摘要生成 prompt

    Args:
        paper_type: 论文类型

    Returns:
        摘要生成 prompt 字符串
    """
    template = THESIS_CHAPTER_TEMPLATES.get(paper_type, THESIS_CHAPTER_TEMPLATES["实验研究"])
    abstract_format = template.get("abstract_format", "目的-方法-结果-结论")

    prompt = f"""基于以下完整论文正文，生成一段 250-300 字的中文摘要。

摘要必须采用「{abstract_format}」四段式结构，严格按以下格式输出：

## 摘要

**目的**：（简述研究问题、研究目的，1-2句）

**方法**：（简述研究方法、研究对象、研究工具，1-2句）

**结果**：（报告核心发现，必须与正文实验结果一致，包含关键数据如 M±SD、t值、p值、效应量，2-3句）

**结论**：（概括研究结论和实践/理论意义，1-2句）

## 关键词

关键词1；关键词2；关键词3；关键词4；关键词5

⚠️ 严格要求：
1. 结果部分的数据必须与正文完全一致，不得编造
2. 关键词用分号分隔，3-5个
3. 全文 250-300 字
4. 不引用文献

正文（前 4000 字）：
{{body_preview}}"""
    return prompt
