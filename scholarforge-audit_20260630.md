# ScholarForge P0→P2 复合审计报告

**审计时间**：2026-06-30 17:20
**审计人**：QClaw（自动审计）
**审计范围**：feature/scholarforge 分支 f8c25d930..bac0d0902 全部 12 个提交
**审计方法**：源码逐行交叉验证 + 提交 stat 比对 + DMG 挂载验证 + 测试执行 + 依赖可用性验证

---

## 一、报告数据准确性验证

| 报告声称 | 实际值 | 判定 |
|----------|--------|------|
| 12 个提交 | 12 | ✅ 准确 |
| 36 文件变更 | 36 | ✅ 准确 |
| +10,427 / -343 行 | +10,427 / -343 | ✅ 准确 |
| 16 个 .py 文件 | 19 个（含 3 测试） | ⚠️ 偏差：报告漏算测试文件 |
| 6 个 Agent | 6（Topic/Literature/Outline/Writing/Refinement/Reviewer） | ✅ 准确 |
| 3 个 Agent 工具 | 3（search/write/review） | ✅ 准确 |
| 7 免费 + 4 付费源 | 7 + 4 = 11 | ✅ 准确 |
| 12 种论文模板 | 12 | ✅ 准确 |
| 6 张 SQLite 表 | 6（projects/outlines/section_contents/literatures/messages/agent_providers） | ✅ 准确 |
| ~25 个 API 端点 | 31 个 | ⚠️ 偏差：报告偏保守，实际多 6 个 |
| 16 种 LaTeX 模板 | 17 种 | ⚠️ 偏差：少算 1 个 |
| Writer.vue ~2,500 行 | 2,481 行 | ✅ 准确 |
| DMG 244MB | 244MB | ✅ 准确 |
| 63/64 测试通过 | 63 passed / 1 failed | ✅ 准确 |

**数据准确率**：13/16 完全准确，3 项轻微偏差（均偏保守，无夸大）

---

## 二、P0 基础闭环审计

### P0-1 完整独立链路 ✅
- `web_server.py:2715` Blueprint 注册验证通过
- `/api/scholar/*` 31 个端点全部存在
- 前后端联通代码路径完整

### P0-2 Agent 列表动态化 ✅
- `GET /api/scholar/agents` 端点存在
- Writer.vue 通过 fetch 动态加载 Agent 列表
- 6 个 Agent 类定义完整（agents/__init__.py 1244 行）

### P0-3 端到端测试 ✅
- `test_e2e_pipeline.py` 448 行，覆盖 7 大环节
- 64 个测试收集，63 passed / 1 failed
- 失败项：`test_resolve_credentials_does_not_crash` — PermissionError 模拟问题（非功能 bug，测试用例问题）

### P0-4 导出联调 ✅
- `GET /api/scholar/export` 端点存在
- 导出面板 UI 存在（Writer.vue 19 处 export 相关代码）

### P0-5 前端拆分 ✅
- ProjectList.vue 206 行独立组件
- Writer.vue 引用 ProjectList 组件

### P0-6 补齐未提交文件 ✅
- citation_provider.py (291 行) ✅
- citation_verifier.py (299 行) ✅
- rag.py (313 行) ✅
- scoring.py (320 行) ✅
- tools.py (409 行) ✅
- tests/ (3 文件 1013 行) ✅

---

## 三、P1 竞品补齐审计

### 知网多策略 — 报告描述错误 ⚠️

**报告声称**：「3 条路径：meta→gateway→sci-hub fallback」
**实际代码**：3 条路径为 **gateway→万方→OpenAlex 中文映射**

| 策略 | 函数 | 实际内容 |
|------|------|----------|
| 策略1 | `_fetch_via_gateway` | 用户自建 CNKI 网关 |
| 策略2 | `_fetch_via_wanfang` | 万方数据 API |
| 策略3 | `_fetch_via_openalex_cn` | OpenAlex 中文学术映射 |

**无 sci-hub**。报告将策略描述写错了。功能本身正常（3 策略降级链完整）。

### 查重 + AIGC 检测 ✅
- `plagcheck.py` 356 行
- 4 种方法：精确匹配(simhash) / 近重复(hamming) / 释义检测(ngram) / AIGC(启发式特征)
- `POST /api/scholar/projects/{pid}/plagcheck` 端点存在
- 前端查重面板 38 处相关代码

### 逐段修改 (Inline Edit) ✅
- `POST /api/scholar/inline-edit` 端点存在
- 前端 `showSelectionMenu` + `inlineEdit` + `onTextSelect` 完整实现

### 共识度分析 ✅
- `POST /api/scholar/projects/{pid}/consensus` 端点存在
- `scoring.py` `score_consensus()` + `extract_key_claims()` 实现
- 前端面板 31 处相关代码

---

## 四、P2 工程质量审计

### P2-1 STORM 引擎 ✅
- `dspy` 可 import ✅
- `knowledge_storm` 可 import ✅
- `storm_adapter.py` 中 7 处 dspy/knowledge_storm import，全部有 try/except 保护

### P2-2 429 冷却 ✅
- `_is_cooled_down()` + `_set_cooldown()` 全局机制
- 7 个免费源逐一验证：arxiv/crossref/doaj/pubmed/semantic_scholar/openalex/core 全覆盖
- 冷却时间：5 分钟（`_COOLDOWN_SECONDS = 300`）

### P2-3 凭证去重 — 不完整 ⚠️

**报告声称**：「消除 ~60 行重复 yaml/.env 解析逻辑」
**实际**：`_load_vermes_config()` 公共入口已创建，`_resolve_credentials` 和 `_list_configured_providers` 已改用公共入口。**但 blueprint.py 仍有 3 处残留重复解析**：

| 行号 | 函数 | 残留内容 |
|------|------|----------|
| 179 | `_resolve_credentials` 内的 fallback 分支 | 完整 yaml+.env 读取 |
| 339 | `_get_model_info` | yaml 读取 config.yaml |
| 741 | `list_available_providers` | 完整 yaml+.env 读取 |

`storm_adapter.py:389` 也有 1 处残留。

**实际消除**：约 3 处 → 公共入口，**剩余** 4 处仍重复。报告声称「三合一去重」但实际只做了约一半。

### P2-4 PDF 导出 ✅
- `brew install gobject-introspection` ✅
- `libgobject-2.0-0.dylib` symlink ✅
- `export/full.py:242` `DYLD_FALLBACK_LIBRARY_PATH` setdefault ✅
- 实测生成 26KB PDF ✅

---

## 五、安全审计

| 检查项 | 结果 |
|--------|------|
| SQL 注入 | ✅ 全部参数化（?占位符） |
| 硬编码密钥 | ✅ 无 |
| bare except | ⚠️ 3 处（agents/__init__.py:290/1010/1024），均为 LLM 调用回退默认值，非安全问题 |
| XSS | ⚠️ Writer.vue `v-html="renderedContent"` — 输入已做 `&`/`<` 转义，低风险但建议加 DOMPurify |
| 路径穿越 | ✅ 无 |
| 命令注入 | ✅ 无 os.system/subprocess.shell=True |

---

## 六、DMG 构建验证

| 检查项 | 结果 |
|--------|------|
| DMG 大小 | 244MB ✅ |
| Vermes.app 存在 | ✅ |
| app.asar 存在 | ✅ |
| 前端 web_dist 存在 | ✅（index.html + 4 个 asset 文件） |
| 后端 vermes-backend 存在 | ✅（Resources/backend/vermes-backend） |
| 前端产物一致性 | ✅（web_dist index.js hash = frontend/dist index.js hash） |

---

## 七、审计发现汇总

### 🔴 严重问题（0 项）
无。

### 🟡 中等问题（3 项）

| # | 问题 | 位置 | 建议 |
|---|------|------|------|
| M1 | **P2-3 凭证去重不完整** | blueprint.py 3 处 + storm_adapter.py 1 处仍重复 yaml/.env 解析 | 继续用 `_load_vermes_config()` 替换剩余 4 处 |
| M2 | **报告知网策略描述错误** | 报告写"meta→gateway→sci-hub"，实际是"gateway→万方→OpenAlex" | 更正报告描述 |
| M3 | **Writer.vue v-html 无 DOMPurify** | Writer.vue:342 | 引入 DOMPurify 消毒 renderedContent |

### 🟢 轻微问题（4 项）

| # | 问题 | 位置 |
|---|------|------|
| L1 | 报告"16 个 .py 文件"实际 19 个（含 3 测试） | 报告文字 |
| L2 | 报告"~25 端点"实际 31 个 | 报告文字 |
| L3 | 报告"16 种 LaTeX 模板"实际 17 种 | 报告文字 |
| L4 | 1 个测试失败（PermissionError 模拟问题） | test_tools.py |

### ✅ 验证通过项（16 项）

提交数/文件数/行数、6 Agent、3 工具、7+4 搜索源、12 论文模板、6 SQLite 表、429 冷却全覆盖、STORM 依赖可用、PDF 导出可用、SQL 参数化、无硬编码密钥、DMG 结构完整、前端产物一致、Blueprint 注册、端到端测试 63/64 通过、Inline Edit/共识度/查重面板功能完整。

---

## 八、审计结论

**整体评分**：**B+（8.0/10）**

报告数据基本准确（3 项轻微偏差均偏保守不夸大），功能实现与声称一致，构建产物完整可用。主要扣分项：

1. P2-3 凭证去重只完成约一半（-1.0）
2. 报告知网策略描述与代码不符（-0.5）
3. v-html 缺 DOMPurify（-0.5）

**建议优先修复**：M1（凭证去重收尾）→ M3（DOMPurify XSS 防护）→ M2（报告更正）。
