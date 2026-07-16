# ScholarForge P0 收尾完成

**日期**: 2026-06-27 11:45 | **分支**: feature/scholarforge

## P0-1: 引用真实性验证 ✅

`hermes_cli/scholarforge/agents/__init__.py` 新增:
- `_validate_citation_refs(text, papers)` — 扫描 [n] 引用，逐条对文献池验证
- `_fuzzy_match_title()` — rapidfuzz 模糊匹配
- LiteratureAgent + WritingAgent 生成后自动调用
- prompt 约束"只引用已有文献"
- 假引用 [5] 正确检测（3 篇池）

## P0-2: 导出模块 ✅

`hermes_cli/scholarforge/export/__init__.py` (174 行):
- `format_export_markdown()` — Markdown 论文
- `format_export_bibtex()` — BibTeX 格式
- `extract_references()` — 从正文提取参考文献列表
- 端点: `/api/scholar/export?format=markdown|bibtex|references`
- 前端: Writer.vue 右上角 📥 导出下拉菜单（Markdown/BibTeX/参考文献）

## P0-3: 付费源可插拔 ✅

`hermes_cli/scholarforge/search/__init__.py`:
- 4 付费源定义: Scopus / Web of Science / CORE / Google Scholar(SerpAPI)
- `get_paid_source_configs()` + `activate_paid_source()`
- 端点: `GET /api/scholar/sources` + `POST /api/scholar/sources/activate`
- CORE 激活验证通过

## 顺修
- arXiv: HTTP→HTTPS (301 重定向)
- export: assembled_text→draft (500)
- 前端同步 web_dist

## 全链路端点验证 (all HTTP 200)
搜索 | 付费源 | 激活 | 导出(MD/BibTeX/refs)
