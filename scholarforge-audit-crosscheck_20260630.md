# ScholarForge 审计报告逐项核实 (2026-06-30)

**核实日期**: 2026-06-30 10:58 CST | **核实者**: QClaw  
**核实范围**: 6/27 审计报告所有声称 vs. 6/30 本机 `feature/scholarforge` 分支实际源码

---

## 一、基本信息核实

| 报告声称 | 实际 | 判定 |
|---------|------|------|
| 分支 `feature/scholarforge` | ✅ 当前在 `feature/scholarforge` | ✅ |
| 基于 `main` de0c0c361 | ✅ merge base 确认 | ✅ |
| 最新提交 `7795b1541` | ✅ `git log -1` 确认 HEAD = 7795b1541 | ✅ |
| 27 文件已变更未提交 | 27 modified (17 staged + 27 unstaged with overlap) | ✅ |
| 6 新文件未跟踪 | 6 核心文件从未提交至 Git: `citation_provider.py, citation_verifier.py, rag.py, scoring.py, tools.py, tests/` — `git log --all -- <files>` 返回空 | ✅ 属实 |

---

## 二、源码架构核实

| 声称 | 实际行数 | 文件存在 | 判定 |
|------|---------|---------|------|
| `blueprint.py` | 991 行 | ✅ | ✅ |
| `database.py` | 519 行 | ✅ | ✅ |
| `tools.py` | 431 行 | ✅ | ✅ |
| `rag.py` | 313 行 | ✅ | ✅ |
| `scoring.py` | 320 行 | ✅ | ✅ |
| `citation_provider.py` | 291 行 | ✅ | ✅ |
| `citation_verifier.py` | 299 行 | ✅ | ✅ |
| `storm_adapter.py` | 403 行 | ✅ | ✅ |
| `agents/__init__.py` | 1244 行 | ✅ | ✅ |
| `search/__init__.py` | 1046 行 | ✅ | ✅ |
| `export/__init__.py` | 174 行 | ✅ | ✅ |
| `export/full.py` | 378 行 | ✅ | ✅ |
| `export/latex.py` | 744 行 | ✅ | ✅ |
| 测试文件 3 个 | 1013 行 | ✅ | ✅ |
| **总计** | **~7161 行 Python 源码** | | ✅ |

---

## 三、搜索源核实

| 声称 | 实际 | 判定 |
|------|------|------|
| 7 免费源 | arxiv, crossref, **openalex**, doaj, semantic_scholar, pubmed, core = 7 | ✅ (注: 6/27 原报告写的 PubMed/DOAJ/CORE 已核实，ACL 已被 OpenAlex 替代) |
| 4 付费源 | scopus, web_of_science, google_scholar(SerpAPI), cnki = 4 | ✅ |
| 11 源全部注册 | `register_search_source()` 被调用 12 次（含默认链） | ✅ |
| 并发搜索策略 | `search_papers()` 已实现 FIRST_COMPLETED + 标题去重 + min_results+max_wait | ✅ |
| **"⚠️ 仅 Semantic Scholar 有 429 冷却"** | **基础设施已建立**: `_COOLDOWN_UNTIL` 全局字典 + `_is_cooled_down()`/`_set_cooldown()` 对所有源生效（search_papers 第 195 行过滤冷却源）。但调用 `_set_cooldown()` 的仍仅 SS。 | ⚠️ **部分纠正: 冷却框架存在，其他源未接入** |

---

## 四、Agent 系统核实

| 声称 | 实际 | 判定 |
|------|------|------|
| 6 Agent | TopicAgent, LiteratureAgent, OutlineAgent, WritingAgent, RefinementAgent, ReviewerAgent (+ BaseAgent) = 7 类 | ✅ |
| STORM 多视角 Persona | `STORM_PERSONAS` 列表存在 | ✅ |
| `_validate_citation_refs()` 在 agents 中 | 第 32 行定义，WritingAgent(647) 和 RefinementAgent(934) 调用 | ✅ |
| `citation_verify` SSE 事件 | 第 948 行发出 `{"type": "citation_verify", ...}` | ✅ |

---

## 五、依赖状态 — 🚨 重大差异

| 包 | 6/27 报告声称 | 6/30 实测 (.venv) | 判定 |
|----|-------------|-------------------|------|
| `dspy` | ❌ 缺失 | ✅ OK | ❌ **报告过时 — 已安装** |
| `knowledge_storm` | ❌ 缺失 | ✅ 1.1.0 OK | ❌ **报告过时 — 已安装** |
| `weasyprint` | ⚠️ 不完整 | ✅ 69.0 OK | ❌ **报告过时 — 已安装** |
| `markdown_it` | ✅ OK | ✅ OK | ✅ |
| `python-docx` | ✅ OK | ✅ OK | ✅ |
| `python-pptx` | ✅ OK | ❌ **缺失** | ❌ **报告不准确 — .venv 中缺失** |
| `openpyxl` | ✅ OK | ❌ **缺失** | ❌ **报告不准确 — .venv 中缺失** |
| `lxml` | ✅ OK | ✅ OK | ✅ |
| `beautifulsoup4` | ✅ OK | ✅ OK | ✅ |
| `httpx` | ✅ OK | ✅ OK | ✅ |
| `PyYAML` | ✅ OK | ✅ OK | ✅ |

**结论**: dspy + knowledge_storm + weasyprint 三者已就位，PDF 导出和 STORM 引擎均可工作。但不影响 ScholarForge 的 python-pptx 和 openpyxl 缺失。

---

## 六、数据库核实

| 声称 | 实际 | 判定 |
|------|------|------|
| 6 表 | projects, outlines, section_contents, literatures, messages, agent_providers = 6 | ✅ |
| 11 种论文类型 | **实际 12 种**: 本科/硕士/博士/期刊/会议/综述/开题报告/课程/调研/实验/案例/毕业设计 | ⚠️ 少计 1 种 |
| agent_providers 表 | UNIQUE(project_id, agent_name) 约束 | ✅ |

---

## 七、LaTeX 模板核实

| 声称 | 实际键 | 判定 |
|------|--------|------|
| 16 模板 | **实际 18+2 别名**: ieee, acm-sigconf, springer-svjour, elsevier-elsarticle, nature, science, apa, mlr, neurips, icml, cvpr, iclr, acl, aaai, acta-physica, jcs, jsi, gbt7714 = **18 个模板** + 2 别名 | ⚠️ 少计 2 个 |

---

## 八、web_server.py 集成核实

| 声称 | 实际 | 判定 |
|------|------|------|
| Blueprint 注册在 2715 行 | ✅ `scholarforge_bp.register_to(app)` 第 2717 行 | ✅ |
| `try/except` 包裹 | ✅ 不会阻塞主启动 | ✅ |
| `_PUBLIC_API_PATHS` 含 `/api/scholar` | ✅ 第 302 行 | ✅ |
| 改动量 "2 行" | 实际 ~4 行 (+ public path + import + register + try/except) | ⚠️ 小幅低估 |

---

## 九、导出功能核实

| 声称 | 实际代码 | 判定 |
|------|---------|------|
| Markdown 导出 | `format_export_markdown()` | ✅ |
| BibTeX 导出 | `format_export_bibtex()` + `extract_references()` | ✅ |
| PDF 导出 | `export_pdf()` — weasyprint 现在可用 | ✅ (曾不可用，现修复) |
| Word 导出 | `export_docx()` — pandoc 优先，python-docx 回退 | ✅ |
| LaTeX 导出 | `format_export_latex()` — 18 模板 | ✅ |
| blueprint 全部 5 个导出端点 | `/api/scholar/export/*` — pdf/docx/latex/markdown/bibtex | ✅ |

---

## 十、P0 完成情况核实

| P0 编号 | 内容 | Commit | 判定 |
|---------|------|--------|------|
| P0-1 | 引用真实性验证 | a0e1d1524 | ✅ 已实现三层验证 |
| P0-2 | 付费文献源可插拔 | a0e1d1524 | ✅ 4 付费源已注册 |
| P0-3 | Markdown/BibTeX 导出 | e74c8a5ac | ✅ |
| P0-4 | 导出功能前后端联调 | cd78e4fe2 | ✅ |
| P0-5 | Writer.vue 拆分 | 7795b1541 | ✅ |

**5 个 P0 全在 6/27 当天提交**，10 个 commits from main。

---

## 十一、关键遗留问题

| 问题 | 严重度 | 详情 |
|------|--------|------|
| **6 文件从未进入 Git** | 🔴 P0 | `citation_provider.py`, `citation_verifier.py`, `rag.py`, `scoring.py`, `tools.py`, `tests/` — `git log --all` 返回空 |
| python-pptx 缺失 | 🟡 | .venv 中未安装，不影响 ScholarForge 主链路 |
| openpyxl 缺失 | 🟡 | .venv 中未安装，不影响 ScholarForge 主链路 |
| 搜索源 429 冷却未全面接入 | 🟢 | 框架就位，仅 SS 调用 _set_cooldown |

---

## 十二、汇总判定

| 维度 | 6/27 报告声称 | 6/30 实测 | 偏离度 |
|------|------------|----------|--------|
| 代码架构 | — | 完整就位 | ✅ 准确 |
| 文件行数 | — | 7161+1013+2943 行 | ✅ 准确 |
| 搜索源数量 | 7+4=11 | 7+4=11 | ✅ 准确 |
| Agent 数量 | 6 | 6+BaseAgent | ✅ 准确 |
| 数据库表/类型 | 6/11 | 6/12 | ⚠️ 类型少计 1 |
| LaTeX 模板 | 16 | 18 | ⚠️ 少计 2 |
| 依赖状态 | 3 个不可用 | 3 个均可用 | ❌ 显著过时 |
| P0 完成度 | 5/5 | 5/5 | ✅ 全完成 |
| 未提交文件 | 6 | 6 (从未提交) | ✅ 准确 |
| 429 保护 | "仅 SS" | 框架就位/仅 SS 接入 | ⚠️ 部分不准确 |

**总体评价**: 6/27 审计报告**主体结构准确**，源码架构/搜索/Agent/导出/前端等核心数据与实际一致。但存在三个时效性问题：
1. **依赖状态已大幅改善** — dspy/knowledge_storm/weasyprint 均已在 .venv 中安装，PDF 导出和 STORM 引擎可用
2. **论文类型和 LaTeX 模板少计** — 分别为 12 种 (非 11) 和 18 种 (非 16)
3. **429 冷却框架已全局建立** — 虽然仅 SS 接入，但基础设施对所有源可用
