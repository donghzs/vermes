"""
ScholarForge LaTeX 导出模块 — 模板系统 + Markdown→LaTeX 转换
支持模板：IEEEtran, ACM, Springer LNCS, 国标 GB/T 7714

生成可编译的 .tex 文件，包含标准 preamble、document 环境
"""
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger("scholarforge.export.latex")


# ═══════════════════════════════════════════════════════════════════
# LaTeX 模板系统
# ═══════════════════════════════════════════════════════════════════

LATEX_TEMPLATES = {
    "ieee": {
        "name": "IEEEtran",
        "description": "IEEE 会议/期刊双栏格式",
        "documentclass": r"\documentclass[conference]{IEEEtran}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",        # 中文支持
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{cite}",
            r"\usepackage{balance}",
        ],
        "bib_style": r"\bibliographystyle{IEEEtran}",
    },
    "acm-sigconf": {
        "name": "ACM SigConf",
        "description": "ACM 会议/期刊标准格式",
        "documentclass": r"\documentclass[sigconf]{acmart}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{cite}",
        ],
        "bib_style": r"\bibliographystyle{ACM-Reference-Format}",
    },
    "springer-svjour": {
        "name": "Springer SVJour",
        "description": "Springer 期刊模板",
        "documentclass": r"\documentclass[twocolumn]{svjour3}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{spmpsci}",
    },
    "elsevier-elsarticle": {
        "name": "Elsevier Elsarticle",
        "description": "Elsevier 期刊模板",
        "documentclass": r"\documentclass[review]{elsarticle}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{elsarticle-num}",
        "preamble_extra": [
            r"\journal{Journal Name}",
        ],
    },
    "nature": {
        "name": "Nature",
        "description": "Nature 期刊风格",
        "documentclass": r"\documentclass[12pt,a4paper]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{natbib}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{naturemag}",
        "preamble_extra": [
            r"\setlength{\parindent}{0pt}",
            r"\setlength{\parskip}{6pt}",
        ],
    },
    "science": {
        "name": "Science",
        "description": "Science 期刊风格",
        "documentclass": r"\documentclass[12pt,twocolumn]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{natbib}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{Science}",
    },
    "apa": {
        "name": "APA 6th",
        "description": "心理学/社会科学 APA 格式",
        "documentclass": r"\documentclass[12pt,a4paper,man]{apa6}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{apacite}",
    },
    "mlr": {
        "name": "MLR/JMLR",
        "description": "机器学习会议/期刊",
        "documentclass": r"\documentclass[11pt]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{jmlr2e}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage[round]{natbib}",
        ],
        "bib_style": r"\bibliographystyle{plainnat}",
    },
    "neurips": {
        "name": "NeurIPS",
        "description": "NeurIPS 会议 2024+",
        "documentclass": r"\documentclass[11pt]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage[preprint]{neurips_2024}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage[round]{natbib}",
        ],
        "bib_style": r"\bibliographystyle{plainnat}",
    },
    "icml": {
        "name": "ICML",
        "description": "ICML 会议",
        "documentclass": r"\documentclass[11pt]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{icml2025}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage[round]{natbib}",
        ],
        "bib_style": r"\bibliographystyle{icml2025}",
    },
    "cvpr": {
        "name": "CVPR/ICCV",
        "description": "计算机视觉顶会",
        "documentclass": r"\documentclass[10pt,twocolumn,letterpaper]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{cvpr}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage[numbers]{natbib}",
        ],
        "bib_style": r"\bibliographystyle{ieeenat_fullname}",
    },
    # ── 中国期刊（国标）──
    "iclr": {
        "name": "ICLR",
        "description": "ICLR 会议 (International Conference on Learning Representations)",
        "documentclass": r"\documentclass[11pt]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{iclr2025_conference}",
            r"\usepackage{times}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage[round]{natbib}",
        ],
        "bib_style": r"\bibliographystyle{iclr2025_conference}",
        "preamble_extra": [r"\iclrconference"],
    },
    "acl": {
        "name": "ACL/EMNLP/NAACL",
        "description": "ACL 自然语言处理顶会系列",
        "documentclass": r"\documentclass[11pt,a4paper]{article}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{acl2025}",
            r"\usepackage{times}",
            r"\usepackage{latexsym}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
        ],
        "bib_style": r"\bibliographystyle{acl_natbib}",
        "preamble_extra": [r"\usepackage[T1]{fontenc}"],
    },
    "aaai": {
        "name": "AAAI",
        "description": "AAAI 人工智能顶会",
        "documentclass": r"\documentclass[letterpaper]{aaai25}",
        "packages": [
            r"\usepackage[UTF8]{ctex}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{natbib}",
        ],
        "bib_style": r"\bibliographystyle{aaai25}",
        "preamble_extra": [
            r"\usepackage{times}",
            r"\usepackage{helvet}",
            r"\usepackage{courier}",
        ],
    },
        "gbt7714": {
        "name": "GB/T 7714",
        "description": "中国国家标准 GB/T 7714-2015（中文论文通用）",
        "documentclass": r"\documentclass[12pt,a4paper]{ctexart}",
        "packages": [
            r"\usepackage{geometry}\geometry{left=3cm,right=2.5cm,top=2.5cm,bottom=2.5cm}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{gbt7714}",
        ],
        "bib_style": r"\bibliographystyle{gbt7714-numerical}",
        "preamble_extra": [
            r"\ctexset{",
            r"  section/format+=\raggedright,",
            r"  subsection/format+=\raggedright,",
            r"}",
        ],
    },
    "acta-physica": {
        "name": "物理学报",
        "description": "中国物理学会 Chinese Physics B/物理学报",
        "documentclass": r"\documentclass[12pt,a4paper]{ctexart}",
        "packages": [
            r"\usepackage{geometry}\geometry{left=3cm,right=2.5cm,top=2.5cm,bottom=2.5cm}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{gbt7714}",
        ],
        "bib_style": r"\bibliographystyle{gbt7714-numerical}",
        "preamble_extra": [
            r"\usepackage{physics}",
        ],
    },
    "jcs": {
        "name": "计算机学报",
        "description": "中国计算机学会 CCF-A 中文期刊",
        "documentclass": r"\documentclass[12pt,a4paper,twocolumn]{ctexart}",
        "packages": [
            r"\usepackage{geometry}\geometry{left=2cm,right=2cm,top=2.5cm,bottom=2.5cm}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{gbt7714}",
        ],
        "bib_style": r"\bibliographystyle{gbt7714-numerical}",
    },
    "jsi": {
        "name": "软件学报",
        "description": "中国计算机学会 CCF-A 中文期刊",
        "documentclass": r"\documentclass[12pt,a4paper]{ctexart}",
        "packages": [
            r"\usepackage{geometry}\geometry{left=3cm,right=2.5cm,top=2.5cm,bottom=2.5cm}",
            r"\usepackage{graphicx}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{booktabs}",
            r"\usepackage{hyperref}",
            r"\usepackage{gbt7714}",
        ],
        "bib_style": r"\bibliographystyle{gbt7714-numerical}",
    },
}

# Backward compat alias for old template name keys
_COMPAT_ALIASES = {
    "acm": "acm-sigconf",
    "springer_lncs": "springer-svjour",
}


def _escape_latex(text: str) -> str:
    """转义 LaTeX 特殊字符（不破坏已有 LaTeX 命令及其参数）"""
    cmd_map = {}

    def _protect(m):
        cmd = m.group(0)
        key = f"\x00LC{len(cmd_map)}\x00"
        cmd_map[key] = cmd
        return key

    # 1. 保护完整的 LaTeX 命令（\cmd + 可选参数 + 花括号参数）
    #    匹配: \cite{...}, \textbf{...}, \begin{...}, \usepackage[...]{...},
    #           \section*{...}, \bye, \noindent, 等无参数/星号命令
    text = re.sub(
        r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*(?:\{[^}]*\})*",
        _protect, text
    )
    # 1b. 保护无参数命令如 \bye, \noindent, \smallskip 等
    text = re.sub(
        r"\\[a-zA-Z]+\*?(?![a-zA-Z{\[])",
        _protect, text
    )

    # 2. 转义剩余反斜杠
    text = text.replace("\\", r"\textbackslash{}")

    # 3. 转义其他特殊字符
    for char, escaped in [
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\^{}"),
    ]:
        text = text.replace(char, escaped)

    # 4. 恢复已保护的 LaTeX 命令
    for key, cmd in cmd_map.items():
        text = text.replace(key, cmd)

    return text


def markdown_to_latex(md_text: str) -> str:
    """将 Markdown 正文转换为 LaTeX

    支持的转换:
    - 标题 (## → \section, ### → \subsection)
    - 粗体/斜体
    - 行内代码
    - 引用 [n]
    - 表格 (markdown → tabular)
    - 公式 ($...$, $$...$$)
    - 列表
    - 分隔线
    """
    lines = md_text.split("\n")
    result = []
    in_table = False
    in_code_block = False
    table_header = False
    table_rows = []
    in_itemize = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 代码块处理
        if stripped.startswith("```"):
            if in_code_block:
                result.append(r"\end{verbatim}")
                result.append("")
                in_code_block = False
            else:
                result.append(r"\begin{verbatim}")
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            result.append(line)
            i += 1
            continue

        # 空行
        if not stripped:
            if in_table and table_rows:
                # 输出表格
                _render_table(result, table_rows, table_header)
                table_rows = []
                in_table = False
                table_header = False
            elif in_itemize:
                result.append(r"\end{itemize}")
                result.append("")
                in_itemize = False
            result.append("")
            i += 1
            continue

        # 分隔线
        if stripped in ("---", "***", "___"):
            result.append(r"\hrulefill")
            result.append("")
            i += 1
            continue

        # 标题
        h_match = re.match(r"^(#{1,6})\s+(.+)", stripped)
        if h_match:
            level = len(h_match.group(1))
            title = _escape_latex(h_match.group(2))
            if level == 1:
                result.append(f"\\title{{{title}}}")
            elif level == 2:
                result.append(f"\\section{{{title}}}")
            elif level == 3:
                result.append(f"\\subsection{{{title}}}")
            elif level == 4:
                result.append(f"\\subsubsection{{{title}}}")
            elif level == 5:
                result.append(f"\\paragraph{{{title}}}")
            else:
                result.append(f"\\subparagraph{{{title}}}")
            result.append("")
            if in_table and table_rows:
                _render_table(result, table_rows, table_header)
                table_rows = []
                in_table = False
                table_header = False
            i += 1
            continue

        # 表格
        if "|" in stripped and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if all(c and re.match(r"^[-:]+$", c.replace(" ", "")) for c in cells):
                table_header = True
                i += 1
                continue
            if not in_table:
                # 新建表格
                col_count = len(cells)
                col_spec = "c" * col_count
                result.append(f"\\begin{{tabular}}{{{col_spec}}}")
                in_table = True
            row_cells = " & ".join(_convert_inline_latex(c) for c in cells)
            table_rows.append(row_cells)
            i += 1
            continue

        # 无序列表
        if re.match(r"^[\-\*\+]\s", stripped):
            if not in_itemize:
                result.append(r"\begin{itemize}")
                in_itemize = True
            item_text = _convert_inline_latex(re.sub(r"^[\-\*\+]\s+", "", stripped))
            result.append(f"  \\item {item_text}")
            i += 1
            continue

        # 有序列表
        if re.match(r"^\d+\.\s", stripped):
            if in_itemize:
                result.append(r"\end{itemize}")
                result.append("")
                in_itemize = False
            item_text = _convert_inline_latex(re.sub(r"^\d+\.\s+", "", stripped))
            result.append(f"\\item {item_text}")
            i += 1
            continue

        # 图片
        if re.match(r"!\[.*\]\(.*\)", stripped):
            img_match = re.match(r"!\[(.*)\]\((.*)\)", stripped)
            if img_match:
                alt = img_match.group(1) or "figure"
                src = img_match.group(2)
                result.append(r"\begin{figure}[htbp]")
                result.append(r"  \centering")
                result.append(f"  \\includegraphics[width=0.8\\textwidth]{{{src}}}")
                result.append(f"  \\caption{{{_escape_latex(alt)}}}")
                result.append(r"\end{figure}")
            i += 1
            continue

        # 水平线
        if re.match(r"^={3,}$|^\-{3,}$", stripped):
            result.append(r"\hrulefill")
            result.append("")
            i += 1
            continue

        # 普通段落
        inlined = _convert_inline_latex(stripped)
        result.append(f"{inlined}\n")
        i += 1

    # 清理未关闭的环境
    if in_itemize:
        result.append(r"\end{itemize}")
    if in_table and table_rows:
        _render_table(result, table_rows, table_header)
    if in_code_block:
        result.append(r"\end{verbatim}")

    return "\n".join(result)


def _convert_inline_latex(text: str) -> str:
    """转换行内 Markdown → LaTeX（粗体、斜体、代码、引用、公式）"""
    # 保留已有的 LaTeX 公式 $$...$$ 和 $...$
    protected = {}

    def _protect_formulas(t):
        idx = [0]

        def _replace_display(m):
            key = f"__FORMULA_DISPLAY_{idx[0]}__"
            protected[key] = m.group(0)
            idx[0] += 1
            return key

        def _replace_inline(m):
            key = f"__FORMULA_INLINE_{idx[0]}__"
            protected[key] = m.group(0)
            idx[0] += 1
            return key

        t = re.sub(r"\$\$([^$]+?)\$\$", _replace_display, t)
        t = re.sub(r"\$(?P<c>[^$]+?)\$", _replace_inline, t)
        return t

    text = _protect_formulas(text)

    # 引用 [n] → \cite{refn}
    text = re.sub(r"\[(\d+)\]", r"\\cite{ref\1}", text)

    # 粗体 **text** → \textbf{text}
    text = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", text)

    # 斜体 *text* → \textit{text}
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\textit{\1}", text)

    # 行内代码 `text` → \texttt{text}
    text = re.sub(r"`([^`]+)`", r"\\texttt{\1}", text)

    # 转义特殊字符（在非公式、非命令区域）
    text = _escape_latex(text)

    # 恢复公式
    for key, value in protected.items():
        text = text.replace(key, value)

    # 清理被破坏的 LaTeX 命令
    text = text.replace(r"\textbackslash{}cite{", r"\cite{")
    text = text.replace(r"\textbackslash{}textbf{", r"\textbf{")
    text = text.replace(r"\textbackslash{}textit{", r"\textit{")
    text = text.replace(r"\textbackslash{}texttt{", r"\texttt{")

    return text


def _render_table(result: list, rows: list, header: bool = False):
    """将收集的表格行渲染为 LaTeX（booktabs 三线表：上下粗线 + 表头下细线，无竖线）"""
    result.append(r"\toprule")
    for idx, row in enumerate(rows):
        result.append(f"  {row} \\\\")
        if idx == 0 and header and len(rows) > 1:
            result.append(r"  \midrule")
    result.append(r"\bottomrule")
    result.append(r"\end{tabular}")
    result.append("")


def _format_authors_latex(papers: list, template: str) -> str:
    """将论文列表格式化为 LaTeX \bibitem"""
    entries = []
    for i, p in enumerate(papers, 1):
        title = getattr(p, "title", "")
        authors = getattr(p, "authors", [])
        year = getattr(p, "year", "")
        venue = getattr(p, "venue", "")
        doi = getattr(p, "doi", "")
        url = getattr(p, "url", "")

        if isinstance(authors, list):
            author_str = ", ".join(authors[:10])
        else:
            author_str = str(authors) if authors else ""

        entry = f"\\bibitem{{ref{i}}} {author_str}. "
        entry += f"{title}. "
        if venue:
            entry += f"\\textit{{{venue}}}, "
        if year:
            entry += f"{year}. "
        if doi:
            entry += f"\\url{{https://doi.org/{doi}}}"
        elif url:
            entry += f"\\url{{{url}}}"

        entries.append(entry)
    return "\n\n".join(entries)


# ═══════════════════════════════════════════════════════════════════
# 主入口：生成完整可编译的 .tex
# ═══════════════════════════════════════════════════════════════════

def format_export_latex(
    title: str,
    content: str,
    papers: list,
    template: str = "ieee",
    abstract: str = "",
    author: str = "",
    keywords: list[str] | None = None,
) -> str:
    """生成完整的可编译 LaTeX 文档

    Args:
        title: 论文标题
        content: 正文 Markdown 内容
        papers: 参考文献 PaperResult / ExportPaper 列表
        template: 模板名 ("ieee" | "acm" | "springer_lncs" | "gbt7714")
        abstract: 论文摘要
        author: 作者名
        keywords: 关键词列表

    Returns:
        完整的 .tex 文件内容
    """
    if template not in LATEX_TEMPLATES:
        # 旧格式兼容
        if template in _COMPAT_ALIASES:
            template = _COMPAT_ALIASES[template]
        else:
            logger.warning(f"Unknown template '{template}', falling back to ieee")
            template = "ieee"

    tpl = LATEX_TEMPLATES[template]
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")

    # 转义标题中的特殊字符
    safe_title = _escape_latex(title)
    safe_author = _escape_latex(author) if author else r"ScholarForge 生成"
    safe_abstract = _convert_inline_latex(abstract) if abstract else ""

    # 转换正文
    latex_body = markdown_to_latex(content)

    # 格式化参考文献
    bib_entries = _format_authors_latex(papers, template)

    # 关键词
    kw_text = ""
    if keywords:
        kws = ", ".join(_escape_latex(k) for k in keywords)
        kw_text = f"\\keywords{{{kws}}}"

    # 生成完整文档
    lines = []

    # 文档类
    lines.append(tpl["documentclass"])

    # 宏包
    for pkg in tpl.get("packages", []):
        lines.append(pkg)

    # 额外 preamble
    for extra in tpl.get("preamble_extra", []):
        lines.append(extra)

    lines.append("")
    lines.append(f"% Generated by ScholarForge on {date_str}")
    lines.append(f"% Template: {tpl['name']}")
    lines.append("")

    # 论文元信息
    lines.append(f"\\title{{{safe_title}}}")
    lines.append(f"\\author{{{safe_author}}}")
    lines.append(f"\\date{{{date_str}}}")
    lines.append("")

    # 文档开始
    lines.append(r"\begin{document}")
    lines.append("")
    lines.append(r"\maketitle")
    lines.append("")

    # 摘要
    if safe_abstract:
        lines.append(r"\begin{abstract}")
        lines.append(safe_abstract)
        lines.append(r"\end{abstract}")
        lines.append("")

    # 关键词
    if kw_text:
        lines.append(kw_text)
        lines.append("")

    # 正文
    lines.append(latex_body)
    lines.append("")

    # 参考文献
    if papers:
        lines.append(r"% ===== 参考文献 =====")
        lines.append("")
        lines.append(f"{tpl.get('bib_style', '')}")
        lines.append(r"\begin{thebibliography}{99}")
        lines.append("")
        lines.append(bib_entries)
        lines.append("")
        lines.append(r"\end{thebibliography}")
        lines.append("")

    # 文档结束
    lines.append(r"\end{document}")

    return "\n".join(lines)


def format_export_latex_section(
    title: str,
    content: str,
    template: str = "ieee",
) -> str:
    """生成单个章节的 LaTeX（不包含 documentclass/preamble）"""
    safe_title = _escape_latex(title)
    latex_body = markdown_to_latex(content)
    return f"\\section{{{safe_title}}}\n{latex_body}"
