"""
ScholarForge 导出模块 — Markdown + BibTeX + (未来) LaTeX/Word
完全独立于 Vermes 核心
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("scholarforge.export")


@dataclass
class ExportPaper:
    title: str
    authors: list[str] = field(default_factory=list)
    year: str = ""
    venue: str = ""
    doi: str = ""
    url: str = ""


def extract_references(text: str) -> list[ExportPaper]:
    """从 LLM 输出中提取参考文献（多种格式兼容）"""
    refs = []

    # 模式 1: [n] Author. Title. Venue, Year.
    ref_blocks = re.split(r'\n(?=\[\d+\])', text)
    for block in ref_blocks:
        m = re.match(r'\[(\d+)\]\s*(.+)', block.strip())
        if m:
            refs.append(_parse_ref_line(m.group(2)))

    # 模式 2: Markdown 参考文献节的编号列表
    if not refs:
        ref_section = re.split(r'#{1,3}\s*参[考]考文[献]', text, maxsplit=1)
        if len(ref_section) > 1:
            lines = ref_section[1].strip().split('\n')
            for line in lines:
                line = line.strip()
                if re.match(r'^[\d\.]+', line):
                    clean = re.sub(r'^[\d\.]+\s*', '', line)
                    if len(clean) > 10:
                        refs.append(_parse_ref_line(clean))

    return refs


def _parse_ref_line(line: str) -> ExportPaper:
    """从引用行提取作者/标题/期刊/年份"""
    # 常见格式: Authors. Title. Venue, Year.
    # 或: Authors (Year). Title. Venue.
    authors = ""
    title = line
    year = ""
    venue = ""

    # 尝试提取年份
    year_match = re.search(r'\((\d{4})\)', line)
    if year_match:
        year = year_match.group(1)
        title = re.sub(r'\s*\(\d{4}\)', '', line)

    # 尝试用 . 分割
    parts = [p.strip() for p in title.split('. ') if p.strip()]
    if len(parts) >= 3:
        authors = parts[0]
        title_part = parts[1] if len(parts) > 1 else parts[0]
        # 如果 title_part 以大写字母开头且长度 >20，很可能是标题
        if len(title_part) > 20 and title_part[0].isupper():
            title = title_part
        else:
            title = parts[1] if len(parts) > 1 else parts[0]
        venue = parts[-1] if len(parts) > 2 else ""

    # 提取作者列表
    author_list = [a.strip() for a in authors.split(',') if a.strip()]

    return ExportPaper(
        title=title[:200],
        authors=author_list[:10],
        year=year,
        venue=venue[:100],
    )


def format_export_markdown(
    title: str,
    content: str,
    papers: list,
    format_type: str = "generic"
) -> str:
    """将写作结果格式化为论文 Markdown

    Args:
        title: 论文标题
        content: 全文学术正文（LLM 原始输出）
        papers: PaperResult 或 ExportPaper 列表
        format_type: "generic" | "cvpr" | "neurips" | "acl"
    """
    now = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# {title}",
        "",
        f"*由 ScholarForge v0.1.0 生成 · {now}*",
        "",
        "---",
        "",
        content.strip(),
        "",
        "---",
        "",
        "## 参考文献",
        "",
    ]
    for i, p in enumerate(papers, 1):
        title_str = getattr(p, 'title', str(p))
        authors_str = ", ".join(getattr(p, 'authors', [])) if hasattr(p, 'authors') and isinstance(getattr(p, 'authors'), list) else ""
        year_str = getattr(p, 'year', "")
        venue_str = getattr(p, 'venue', "")
        url_str = getattr(p, 'url', "")
        doi_str = getattr(p, 'doi', "")

        cite_line = f"[{i}] {authors_str}. **{title_str}**."
        if venue_str:
            cite_line += f" *{venue_str}*"
        if year_str:
            cite_line += f", {year_str}"
        if doi_str:
            cite_line += f". DOI: [{doi_str}](https://doi.org/{doi_str})"
        elif url_str:
            cite_line += f". [链接]({url_str})"
        lines.append(cite_line)
        lines.append("")

    # 导出格式标注
    format_notes = {
        "generic": "通用学术论文格式",
        "cvpr": "CVPR 格式（IEEE 双栏）",
        "neurips": "NeurIPS 格式",
        "acl": "ACL 格式",
    }
    lines.append(f"---")
    lines.append(f"*导出格式: {format_notes.get(format_type, '通用')}*")

    return "\n".join(lines)


def format_export_bibtex(papers: list) -> str:
    """从文献池生成 BibTeX"""
    entries = []
    for i, p in enumerate(papers, 1):
        title = getattr(p, 'title', '')
        authors = getattr(p, 'authors', [])
        year = getattr(p, 'year', '')
        venue = getattr(p, 'venue', '')
        doi = getattr(p, 'doi', '')

        # 生成 citation key
        first_author = (authors[0] if authors else "Unknown").split()[-1] if isinstance(authors, list) and authors else "Unknown"
        key = f"{first_author}{year}" if year else f"ref{i}"

        entry = f"""@article{{{key},
  title = {{{title}}},
  author = {{{' and '.join(authors) if isinstance(authors, list) and authors else 'Unknown'}}},
  year = {{{year or 'n.d.'}}},"""
        if venue:
            entry += f"\n  journal = {{{venue}}},"
        if doi:
            entry += f"\n  doi = {{{doi}}},"
        entry += "\n}"
        entries.append(entry)

    return "\n\n".join(entries)
