# ScholarForge P0/P1 修复记录 (2026-07-01)

## P0 修复（4项全部完成）

### P0-1: STORM 引擎死代码 ✅
- 前端"STORM 全链路"按钮 → "全链路写作"（含 tooltip："6阶段：选题→文献→大纲→写作→润色→审稿"）
- 后端 `blueprint.py` 删除不可达的 `agent=storm` 代码路径（~50行）
- 前端 `runStormPipeline()` 内部文字统一修改

### P0-2: 在线查星空壳 ✅
- 已在之前 session 修复：`check_online_paperyy()`/`check_online_dachagao()` → `ONLINE_PLAG_SERVICES` + `get_online_plag_services()` 诚实引导

### P0-3: PDF 导出中文豆腐块 ✅
- `export/full.py` `_PDF_CSS` 字体栈修改：
  - body: `"PingFang SC", "Heiti SC", "STSong", "STKaiti", serif`
  - 页眉/页脚: `"PingFang SC", "Heiti SC", "DejaVu Sans", sans-serif`
- 原因：macOS 内置 PingFang SC/Heiti SC，原 `Noto Serif CJK SC` 未安装

### P0-4: CNKI 搜索需配 Key → 文档澄清（低优先级）
- `cnki_fetcher.py` 三种策略全需 API Key（自建网关/万方/OpenAlex），无法零 Key 免费可用
- 搜索页面已有"需配 Key"标注，无需额外改动

---

## P1 修复（5/6 项完成）

### P1-1: 搜索结果空无提示 ✅
- `Writer.vue` `searchLiterature()`: newPapers.length===0 时 push warning 事件
- 事件类型新增 `warning`（橙色图标 ⚠️）
- 提示内容："未找到相关文献。建议：改用英文关键词 / 尝试其他搜索源"

### P1-2: 引用验证不持久化 ✅
- 新增 DB 表 `citation_verifications` (project_id, ref_num, score, reason, verified_at)
- `database.py`: `save_citation_verifications(pid, results)` + `get_citation_verifications(pid)`
- `agents/__init__.py` RefinementAgent: 验证结果实时写 DB
- `blueprint.py`: 新增 `GET /api/scholar/projects/{pid}/citation-verifications` 端点
- `Writer.vue` `switchProject()`: 加载项目时从 DB 恢复引用验证结果

### P1-3: 评分 fallback 割裂 ✅
- `scoring.py` `_fallback_score()` 重写：不再基于 content_len/1000 虚高，统一保守估算
- 原创性固定 5.0，逻辑性基于章节数 max 7.0，引用完整性基于 ref_count+paper_count max 7.0
- 所有 reasoning 标注"⚠️ 启发式估算（非 LLM 评估）"
- 新增 `_is_fallback: True` 标记
- 前端评分面板：fallback 时显示 amber 警告条

### P1-4: RAG 截断不透明 ✅
- `agents/__init__.py` WritingAgent: 每次 RAG 检索发出 SSE searching/thinking 事件
- 成功时："RAG: 从 N 篇文献中匹配 M 篇 (前3相关度: 0.85, 0.72, 0.61)"
- 未匹配时：thinking 事件说明原因
- 无检索器时：thinking 事件告知使用前 N 篇

### P1-5: RAG 重排后引用编号错位 ✅
- `rag_ref_map` 中引用编号从 `[文献{i+1}]` 改为 `[{全局编号}]`
- 与 `papers_text` 中的 `[self.ctx.papers.index(p) + 1]` 一致
- AI 引用时全局编号固定，不再因 RAG 重排而错位

### P1-6: 论文类型模板缺 5 种 → 已在之前 session 修复 ✅
- 前端 paperTypes 已补全 12 种

---

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `vermes_cli/scholarforge/export/full.py` | P0-3: PDF 字体改为系统内置 |
| `vermes_cli/scholarforge/blueprint.py` | P0-1: 删除 storm 死代码; P1-2: 引用验证端点 |
| `vermes_cli/scholarforge/database.py` | P1-2: citation_verifications 表 + CRUD |
| `vermes_cli/scholarforge/agents/__init__.py` | P1-2: 持久化; P1-4: RAG 透明; P1-5: 编号修正 |
| `vermes_cli/scholarforge/scoring.py` | P1-3: fallback 评分保守化 |
| `frontend/src/components/Writer.vue` | P0-1: STORM→全链路; P1-1: 空结果提示; P1-2: 加载持久化; P1-3: fallback 警告; P1-5: 引用编号全局化 |

## 构建状态
✅ npm run build 通过 (1.34s)
