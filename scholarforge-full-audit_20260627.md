# ScholarForge 全面审计 & 优化路线图
**日期**: 2026-06-27 | **审计者**: QClaw | **范围**: 代码审计 + 竞品对标 + 全链路验证

---

## 一、当前实际状态（代码审计）

### 1.1 LLM 调用链
```
ScholarWriter.vue → POST /api/scholar/stream → blueprint._llm()
  → _get_chat_credentials()  ← 复用 Vermes config.yaml + .env
  → _resolve_model_provider() ← 复用 Vermes provider 路由
  → httpx.AsyncClient POST {provider_url}/chat/completions
```
✅ **模型来源**：跟随用户 Settings 中配置的默认模型（Agnes/DeepSeek/Claude等），不改用户设置就能直接用  
✅ **选题 Agent 实测通过**：返回 2000+ 字分析报告，质量好

### 1.2 文献搜索
| 源 | 状态 | API 限制 |
|----|------|---------|
| arXiv | ✅ 免费 | 无 API Key，偶尔限流 |
| Crossref | ✅ 免费 | 无 rate limit 文档说明，实测稳定 |
| Semantic Scholar | ⚠️ 不稳定 | 免费但持续 429 |
| **付费源接口** | ❌ **未实现** | PaidSearchAPI 基类仅存在于注释中 |

✅ **实测通过**：`large+language+models+education` 返回 5 篇 Crossref 真实文献  
❌ **缺少**：付费搜索源（Scopus/Web of Science/Google Scholar API）接入

### 1.3 Agent 引擎（5 Agent）
| Agent | 职责 | 关键能力 | 状态 |
|-------|------|---------|------|
| TopicAgent | 选题分析 | 创新性/可行性/学术贡献分析 | ✅ 实测通过 |
| LiteratureAgent | 文献检索+综述 | 关键词提取→多源搜索→综述生成 | ⚠️ 搜索质量依赖免费源 |
| OutlineAgent | 大纲生成 | 结构化章节+字数规划 | ✅ 逻辑完整 |
| WritingAgent | 章节写作 | 引用文献逐节撰写 | ⚠️ 未做引用验证 |
| RefinementAgent | 润色审校 | 去AI味+学术规范化 | ✅ 提示词质量好 |

### 1.4 前端 Writer.vue
- ✅ SSE 事件流接收（thinking/searching/writing/content/done）
- ✅ 5 Agent 按钮可独立或全链路执行
- ✅ 背景色已统一为 Vermes dark 模式
- ❌ 写作文本渲染用简单正则转 Markdown（不如 v-md-editor）
- ❌ 文献引用无法点击跳转
- ❌ 无导出功能（Word/LaTeX/PDF）

### 1.5 web_server.py 改动量
```
仅 2 行：
+    "/api/scholar",   # _PUBLIC_API_PATHS 白名单
+ blueprint 注册 (try/except 包裹)
```
零侵入，已验证。

---

## 二、竞品对标

### 2.1 头部论文写作工具矩阵

| 维度 | Consensus | Elicit | Jenni AI | PaperOrchestra (Google) | **ScholarForge 当前** |
|------|-----------|--------|----------|------------------------|----------------------|
| 文献数据库 | 2.5亿+ 篇 | Semantic Scholar | - | - | arXiv+Crossref+SS (~千万级) |
| 写作模式 | ❌ 仅搜索 | ❌ 仅搜索 | 逐句辅助 | 全自动 LaTeX | 全链路 5 Agent |
| 引用管理 | 半自动 | 表格导出 | 内联引用 | BibTeX 自动 | ❌ 无导出 |
| 证据提取 | ✅ 共识分析 | ✅ 表格提取 | ❌ | ❌ | ❌ |
| 多 Agent | ❌ | ❌ | ❌ | ✅ 5 Agent | ✅ 5 Agent |
| LaTeX | ❌ | ❌ | ❌ | ✅ | ❌ |
| 模板系统 | ❌ | ❌ | ❌ | ❌ | ❌ |
| 免费层 | 90% 功能免费 | 部分免费 | 付费 | 开源免费 | 全部免费 |

### 2.2 差距分析

| 能力 | ScholarForge 差距 | 优先级 |
|------|------------------|--------|
| 文献质量 | 仅免费源，无 Scopus/WoS | **P0** |
| 导出格式 | 无 LaTeX/Word/BibTeX | **P0** |
| 引用验证 | 不验证 LLM 生成引用是否真实 | **P0** |
| 模板系统 | 无论文模板（会议/期刊格式） | **P1** |
| 证据提取 | 无 Consensus 式共识分析 | **P1** |
| 迭代修改 | 无多轮对话式修改 | **P1** |
| UI 专业性 | 简单 textarea 输入 | **P2** |

---

## 三、优化路线图

### P0（阻塞发版 — 3 天内）

#### P0-1: 引用真实性验证
**问题**: LiteratureAgent 让 LLM 提取引用，LLM 可能编造  
**方案**: 在 LiteratureAgent 中增加 `_validate_citation()` 步骤
- 论文搜索返回时自动匹配 title 相似度
- WritingAgent 写入引用时用正则提取 `[n]`，反向查 PaperCard 确认存在
- 不存在的引用标记 `⚠️` 供用户审查
**改动量**: ~50 行（agents/__init__.py）

#### P0-2: 付费文献源可插拔接口
**问题**: 无 Scopus/Web of Science，搜索覆盖面窄  
**方案**: 
- 实现 `PaidSearchAPI` 基类（用户填空 API Key → 接入 Scopus/WoS/CORE）
- Settings 页面添加"付费文献源"配置卡片
- 搜索时自动混入付费源结果
**改动量**: ~120 行（search/__init__.py + Settings.vue）

#### P0-3: Markdown/BibTeX 导出
**问题**: 写完无法导出，实操价值为零  
**方案**: 
- 前端加"导出"按钮 → 调后端生成 `.md` + `.bib`
- 后端用正则从 LLM 回复提取参考文献，生成 BibTeX
**改动量**: ~80 行（blueprint.py + Writer.vue）

### P1（发版前增强 — 1 周内）

#### P1-1: 论文模板系统
**问题**: 不同会议/期刊格式不同，现在全是通用格式  
**方案**: 在 OutlineAgent prompt 中注入模板规则
- 预置 5 模板：CVPR/NeurIPS/ACL/通用中文期刊/毕业论文
- 模板定义章节结构、字数要求、引用格式

#### P1-2: STORM 多视角深化
**问题**: LiteratureAgent 已有 `STORM_PERSONAS` 列表但未使用  
**方案**: 在 `LiteratureAgent.run()` 中实际并行 5 视角检索
- 每个 Persona 独立搜索+提问 → 聚合去重
- 综述生成时带入各视角发现

#### P1-3: 多轮对话迭代
**问题**: 每次发送都重新执行整个 Pipeline，无法逐段修改  
**方案**: 
- ProjectContext 支持 `append` 模式
- 用户输入"修改第二段引用" → 仅重写目标段落

### P2（持续优化）

- 文本编辑器从 textarea → v-md-editor/ProseMirror
- 文献卡片可视化（作者网络图）
- LaTeX 实时预览
- Overleaf 集成

---

## 四、结论

**ScholarForge 当前评分**: 62/100

| 维度 | 得分 | 说明 |
|------|------|------|
| 架构设计 | 8/10 | 5 Agent + STORM pipeline + Blueprint 隔离，设计优秀 |
| 核心功能 | 5/10 | 写作链路能跑，但引用验证、导出、搜索质量三块缺失 |
| 代码质量 | 7/10 | 结构清晰，但前端渲染简陋、文献搜索无分层 |
| 竞品差距 | 4/10 | 文献量级、导出格式、引用管理均落后头部工具 |
| 可扩展性 | 7/10 | 注册表模式+Blueprint 隔离，付费源可插拔 |

**核心判断**: 基础写作链路已跑通，但缺少三个 P0（引用验证 + 付费源 + 导出）就无法实际使用。填完 P0 后，对标 PaperOrchestra（Google）在 Agent 架构上已平齐，差距主要在文献数据库深度和 LaTeX 支持上。
