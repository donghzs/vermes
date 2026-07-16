# ScholarForge 全量优化工作报告 (P0/P1/P2)

**分支**：`feature/scholarforge`（基于 main de0c0c361）
**终点提交**：`bac0d0902`
**构建状态**：✅ DMG 已生成（Vermes-2.3.0-arm64.dmg 244MB，17:05）
**总改动量**：36 文件，+10,427 / -343 行

---

## 一、P0 — 基础闭环（10 个提交）

### P0-1：完整独立链路（2f9bccad0）
前端 + 后端端到端闭环：Writer.vue ↔ /api/scholar/* ↔ agent pipeline
- 12 files, +545/-21

### P0-2：Agent 列表动态化（452a872ac）
Writer.vue 从 `/api/scholar/agents` 动态加载 6 个 Agent，不再硬编码
- 6 files, +1,286/-236

### P0-3：全链路端到端验证（2477a0c1d）
完整测试 topic→literature→outline→writing→refinement→reviewer 六步流程
- 1 file, +464/-50（test_e2e_pipeline.py）

### P0-4：导出功能联调（cd78e4fe2）
Markdown/BibTeX/参考文献 导出前后端联通
- 5 files, +150/-3

### P0-5：前端拆分（7795b1541）
Writer.vue 提取 ProjectList.vue，组件解耦
- 7 files, +336/-138

### P0-6：补齐未提交文件（8553dfee9）
提交 6 个关键未跟踪文件 + 同步构建产物
- 39 files, +6,185/-778
- 文件：citation_provider.py, citation_verifier.py, rag.py, scoring.py, tools.py, tests/

### 其他 P0 小提交
- `2f9bccad0` 完整独立链路初始版
- `a0e1d1524` 引用验证 + 导出模块 + 付费源
- `e74c8a5ac` 导出按钮 UI
- `348da7762` Sidebar 论文按钮修复
- `77dbe3826` database + storm_adapter 初版提交

---

## 二、P1 — 竞品竞争力补齐（1 个提交，192594ac5）

对标千笔AI/PaperRed 等竞品能力缺口：

| 功能 | 实现文件 | 说明 |
|------|----------|------|
| **知网多策略取全文** | `cnki_fetcher.py` (233行) | 3 条路径：meta→gateway→sci-hub fallback |
| **查重 + AIGC 检测** | `plagcheck.py` (356行) | 4 种方法：精确匹配/近重复/释义检测/AIGC 概率 + 报告面板 |
| **逐段修改（Inline Edit）** | Writer.vue + `blueprint.py` `/api/scholar/inline-edit` | 选中文字→浮动菜单→AI 润色/扩展/精简/改写 |
| **查重面板** | Writer.vue 右侧面板 | 结果列表 + 相似度柱状图 + 高亮定位 |
| **共识度分析面板** | Writer.vue + `blueprint.py` | 文献对论文论断的支持度分析 + 逐文献立场展开 |

- 15 files, +1,169/-194

---

## 三、P2 — 工程质量补齐（1 个提交，bac0d0902）

| 项 | 问题 | 修复 |
|----|------|------|
| **P2-1 STORM 引擎** | `dspy` + `knowledge_storm` 未安装 | `pip install dspy-ai knowledge-storm` ✅ |
| **P2-2 429 冷却** | 仅 Semantic Scholar 有，其余 6 源无保护 | `search/__init__.py` 全局冷却机制全覆盖 |
| **P2-3 凭证去重** | `_resolve_credentials()` 在 3 个文件各写一遍 | `__init__.py` 新增 `_load_vermes_config()` 公共入口 |
| **P2-4 PDF 导出** | WeasyPrint cffi 找不到 libgobject | brew gobject + symlink + env setdefault，实测 26KB PDF ✅ |

- 5 files, +454/-59

---

## 四、最终产物结构

```
hermes_cli/scholarforge/           # 后端核心（16 个 .py 文件）
├── __init__.py                    # _load_vermes_config() + Agent 工具注册入口
├── database.py                    # SQLite 6 表 + 12 种论文模板 + CRUD
├── blueprint.py                   # FastAPI /api/scholar/* 路由（~25 端点）
├── tools.py                       # Vermes Agent 工具: search/write/review
├── agents/__init__.py             # 6 Agent: topic/literature/outline/writing/refinement/reviewer
├── search/__init__.py             # 7 免费 + 4 付费学术源 + 429 冷却
├── rag.py                         # TF-IDF 语义检索（零依赖，中英文分词）
├── citation_provider.py           # DBLP/CrossRef/S2 真引用生成
├── citation_verifier.py           # 三层引用验证（范围→Fuzzy→LLM）
├── scoring.py                     # 三维度评分 + 共识度评分
├── storm_adapter.py               # STORM 引擎适配器
├── cnki_fetcher.py                # 知网 3 策略全文抓取
├── plagcheck.py                   # 查重 + AIGC 检测
├── export/
│   ├── __init__.py                # Markdown/BibTeX 导出
│   ├── full.py                    # PDF (WeasyPrint) + Word (pandoc→docx)
│   └── latex.py                   # 16 种 LaTeX 模板
└── tests/
    ├── test_e2e_pipeline.py       # 端到端测试（7 环节）
    ├── test_scoring_citation.py   # 评分+引用单元测试
    └── test_tools.py              # 工具注册测试

frontend/src/components/
├── Writer.vue                     # 主编辑器（~2,500 行）
│   ├── 项目选择 → ProjectList
│   ├── 大纲编排（拖拽/增删/重命名）
│   ├── 三栏视图（编辑/分屏/预览）
│   ├── Inline AI 编辑（选中文字 → 浮动菜单）
│   ├── 研究深度选择器（R1/R2/R3）
│   ├── STORM 全链路一键写作
│   ├── 导出面板（Markdown/BibTeX/PDF/Word/LaTeX）
│   ├── 查重面板（4 种检测方法）
│   └── 共识度分析面板（逐文献立场展开）
└── ProjectList.vue                # 论文项目列表组件
```

---

## 五、竞品对标

| 能力 | 千笔AI | PaperRed | ScholarForge |
|------|--------|----------|--------------|
| 全流程覆盖 | ✅ | ✅ | ✅ 6 Agent |
| 免费学术源搜索 | ❌ | ❌ | ✅ 7 源 + 4 付费 |
| STORM 深度研究 | ❌ | ❌ | ✅ dspy knowledge_storm |
| 知网文献取全文 | ✅ | ✅ | ✅ 3 策略 |
| 查重 + AIGC 检测 | ✅ | ✅ | ✅ 4 方法 |
| 真引用验证 | ❌ | ❌ | ✅ 三层验证 |
| 逐段 AI 修改 | ❌ | ❌ | ✅ Inline Edit |
| RAG 语义检索 | ❌ | ❌ | ✅ TF-IDF 零依赖 |
| 16 种 LaTeX 模板 | ❌ | ❌ | ✅ |
| PDF/Word 双导出 | ✅ | ✅ | ✅ |
| 论文类型模板 | 有限 | ✅ | ✅ 12 种 |
| 共识度分析 | ❌ | ❌ | ✅ |
| 本地运行 | 云 | 云 | ✅ 桌面 + 本机 |

---

## 六、构建结果

```
dist-electron/Vermes-2.3.0-arm64.dmg  244MB  2026-06-30 17:05
dist-electron/Vermes-2.3.0.dmg        249MB  2026-06-30 17:05
```

✅ 前端 Vite 构建 → web_dist 同步 → PyInstaller 后端 → electron-builder DMG 全链路通过
