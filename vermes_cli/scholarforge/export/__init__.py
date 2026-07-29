"""
ScholarForge 导出模块 — Markdown + BibTeX + (未来) LaTeX/Word
完全独立于 Vermes 核心
"""
import json
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


# ═══════════════════════════════════════════════════════════════════
# Zotero CSL JSON 导出
# ═══════════════════════════════════════════════════════════════════

def _looks_like_given(s: str) -> bool:
    """ initials（如 'J.' / 'J. K.'）视作 given，而非姓。"""
    return bool(re.search(r'\.', s))


def _split_authors(raw: str) -> list:
    """把 'Smith, J., Lee, K.' / 'Smith, J. and Lee, K.' 拆成 [{family, given}]。

    规则：
      - 先用 ' and ' 切分多作者；
      - 每个 chunk 用逗号切分，出现「Family, Given(含句点)」时两两配对。
    """
    if not raw:
        return []
    out = []
    for chunk in re.split(r'\s+and\s+', raw, flags=re.IGNORECASE):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",") if p.strip()]
        if len(parts) == 1:
            toks = parts[0].split()
            if len(toks) == 1:
                out.append({"family": toks[0], "given": ""})
            else:
                out.append({"family": toks[-1], "given": " ".join(toks[:-1])})
            continue
        i = 0
        while i < len(parts):
            family = parts[i]
            given = ""
            if i + 1 < len(parts) and _looks_like_given(parts[i + 1]):
                given = parts[i + 1]
                i += 2
            else:
                i += 1
            out.append({"family": family, "given": given})
    return out


def _normalize_csl_authors(authors) -> list:
    """兼容 authors 为 字符串 / 字符串列表 / {family,given} 字典列表 / ExportPaper 列表。"""
    if not authors:
        return []
    if isinstance(authors, str):
        return _split_authors(authors)
    if isinstance(authors, list):
        result = []
        for a in authors:
            if isinstance(a, str):
                result.extend(_split_authors(a))
            elif isinstance(a, dict):
                result.append({
                    "family": str(a.get("family", "") or ""),
                    "given": str(a.get("given", "") or ""),
                })
            else:
                fam = getattr(a, "family", "") or ""
                giv = getattr(a, "given", "") or ""
                if not fam and not giv:
                    # ExportPaper 风格：authors 是字符串列表
                    continue
                result.append({"family": str(fam), "given": str(giv)})
        return result
    return []


def parse_references_csl(ref_text: str) -> list:
    """从参考文献区文本解析出结构化文献列表（供 CSL JSON 使用）。

    支持常见 '[n] Author (Year). Title. Venue. DOI:...' 形式；
    兼容中文逗号/「and」分隔的多作者；尽力抽取 year/doi。
    不读 SQLite，仅基于传入文本。
    """
    papers = []
    for m in re.finditer(r'\[(\d+)\]\s+(.+?)(?=\n\[|\n\n|$)', ref_text, re.DOTALL):
        body = m.group(2).strip().replace("\n", " ")
        ym = re.search(r'\((\d{4})\)', body)
        year = ym.group(1) if ym else ""

        am = re.match(r'^(.*?)\(\d{4}\)', body)
        authors_raw = am.group(1).strip() if am else body.split(".")[0].strip()
        authors = _split_authors(authors_raw)

        rest = body[am.end():] if am else body
        rest = rest.lstrip(". ").strip()
        parts = re.split(r'\.\s+', rest, maxsplit=1)
        title = parts[0].strip().rstrip(".").strip()
        venue = parts[1].strip().rstrip(".").strip() if len(parts) > 1 else ""

        doi = ""
        dm = re.search(
            r'(?i)doi[:\s]+([0-9.][^\s,;]*)|https?://(?:dx\.)?doi\.org/([^\s,;]+)',
            venue or title,
        )
        if dm:
            doi = (dm.group(1) or dm.group(2) or "").rstrip(").,")
            venue = re.sub(r'(?i)\s*(doi[:\s]+[0-9.][^\s,;]*|https?://(?:dx\.)?doi\.org/[^\s,;]+)',
                           '', venue).strip().rstrip(".").strip()
        papers.append({
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "doi": doi,
        })
    return papers


def format_export_csl_json(papers: list) -> str:
    """把文献列表转为 Zotero 兼容的 CSL JSON（数组）。"""
    items = []
    for i, p in enumerate(papers, 1):
        title = p.get("title") if isinstance(p, dict) else getattr(p, "title", "")
        authors = p.get("authors") if isinstance(p, dict) else getattr(p, "authors", [])
        year = str(p.get("year") if isinstance(p, dict) else getattr(p, "year", "") or "")
        venue = p.get("venue") if isinstance(p, dict) else getattr(p, "venue", "")
        doi = p.get("doi") if isinstance(p, dict) else getattr(p, "doi", "")

        item = {
            "id": i,
            "type": "article-journal",
            "title": title or "",
            "author": _normalize_csl_authors(authors),
        }
        if venue:
            item["container-title"] = venue
        if year.isdigit():
            item["issued"] = {"date-parts": [[int(year)]]}
        if doi:
            item["DOI"] = doi
            item["URL"] = f"https://doi.org/{doi}"
        items.append(item)
    return json.dumps(items, ensure_ascii=False, indent=2)
