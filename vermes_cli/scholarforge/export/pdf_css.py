"""PDF 导出 CSS 样式 — 从 full.py 提取，便于维护"""

_PDF_CSS = """
@page {
    size: A4;
    margin: 2.5cm 2cm 2.5cm 2cm;
    @bottom-center {
        content: counter(page) " / " counter(pages);
        font-family: "PingFang SC", "Heiti SC", "DejaVu Sans", sans-serif;
        font-size: 9pt;
        color: #666;
    }
    @top-right {
        content: "ScholarForge";
        font-family: "PingFang SC", "Heiti SC", "DejaVu Sans", sans-serif;
        font-size: 8pt;
        color: #999;
    }
}
body {
    font-family: "PingFang SC", "Heiti SC", "STSong", "STKaiti", serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #222;
    text-align: justify;
}
h1 {
    font-size: 22pt;
    text-align: center;
    margin: 1.5em 0 1em 0;
    font-weight: bold;
    color: #1a1a1a;
    border-bottom: 2px solid #333;
    padding-bottom: 0.3em;
}
h2 {
    font-size: 15pt;
    margin: 1.5em 0 0.8em 0;
    font-weight: bold;
    color: #2a2a2a;
    border-bottom: 1px solid #ddd;
    padding-bottom: 0.2em;
}
h3 {
    font-size: 12.5pt;
    margin: 1.2em 0 0.6em 0;
    font-weight: bold;
    color: #333;
}
h4 {
    font-size: 11.5pt;
    margin: 1em 0 0.5em 0;
    font-weight: bold;
    color: #444;
}
p {
    margin: 0.5em 0;
    text-indent: 2em;
}
p:first-of-type {
    text-indent: 0;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 10pt;
}
th, td {
    border: 1px solid #ccc;
    padding: 6px 10px;
    text-align: left;
}
th {
    background: #f5f5f5;
    font-weight: bold;
}
code {
    font-family: "Menlo", "Monaco", monospace;
    font-size: 10pt;
    background: #f4f4f4;
    padding: 1px 4px;
    border-radius: 3px;
}
pre {
    font-family: "Menlo", "Monaco", monospace;
    font-size: 9.5pt;
    background: #f4f4f4;
    padding: 12px;
    border-radius: 4px;
    overflow-x: auto;
    line-height: 1.5;
}
pre code {
    background: none;
    padding: 0;
}
blockquote {
    border-left: 3px solid #ccc;
    margin: 0.5em 0;
    padding: 0.5em 1em;
    color: #666;
}
a {
    color: #2563eb;
    text-decoration: none;
}
a:hover {
    text-decoration: underline;
}
.references {
    font-size: 9.5pt;
    line-height: 1.5;
    margin-top: 2em;
}
.references p {
    text-indent: -2em;
    padding-left: 2em;
    margin: 0.2em 0;
}
"""
