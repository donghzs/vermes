# ScholarForge 细节功能差距审计 — 2026-07-01

## 审计范围
全部 18 个源文件，~5500 行 Python + ~2600 行 Vue，逐模块过。

---

## 🔴 P0 — 真缺陷（功能点存在但不可用）

### 1. STORM 引擎完全不可用
- `storm_adapter.py` 402 行代码，`dspy` + `knowledge_storm` 均未安装
- 前端 STORM 全链路按钮 → 必然抛异常
- **影响**：storm_adapter.py 的所有代码为死代码

### 2. 在线查重全是空壳
- `check_online_paperyy()` → `return None`
- `check_online_dachagao()` → `return None`
- 只有本地 SimHash 管用，但用户看到的是"在线查重"
- **影响**：`check_online=True` 参数无意义

### 3. PDF 导出中文依赖 Noto CJK 字体
- CSS 硬编码 `font-family: "Noto Serif CJK SC"` 但没检查系统是否安装
- macOS 默认无此字体 → PDF 中文全是豆腐块 □□□
- **影响**：PDF 导出在大多数电脑上不可用

### 4. CNKI 搜索三种策略全需用户手动配 Key
- 策略1: CNKI_GATEWAY_URL + CNKI_API_KEY
- 策略2: WANFANG_API_KEY  
- 策略3: OpenAlex（这个倒是免费但中文论文极少）
- 无一开箱可用
- **影响**：号称"支持 CNKI"但实际不配 key 用不了

---

## 🟡 P1 — 体验糟糕（能用但不好用/有隐患）

### 5. 搜索结果为空时前端无友好提示
- 搜一个不存在的词，返回空数组 `[]`
- 前端无 "未找到相关文献" 的提示，用户以为搜索坏了
- **修复**：前端检测空结果 + 后端加 `"message"` 字段

### 6. 引用验证结果未持久化
- RefinementAgent 做的引用验证在 SSE 流中一次性推送
- 刷新页面后验证结果全部丢失
- 用户需要重新跑 STORM pipeline 才能看到验证结果
- **修复**：将 verify_results 写入 DB，加 GET endpoint

### 7. 评分/共识度 fallback 与实际评分差距过大
- `_fallback_score()` 用 `内容长度/1000` 算原创性，几乎随机
- 用户看到 3 分 vs LLM 评 8 分，体验割裂
- **建议**：无 LLM 时明确告知"需配置 API Key"，不显示假分

### 8. 写作时 RAG 文献截断不透明
- WritingAgent 只拿 top_k=5 篇文献给 LLM
- 如果文献池有 28 篇，用户不知道有 23 篇被丢弃了
- **修复**：加事件日志 "从 28 篇文献中语义检索到 5 篇"

### 9. RAG 重排后引用编号与全局文献池不对应
- RAG 把 top_k 文献重新排序了，但 prompt 里的编号是新的临时编号
- LLM 输出 [1] 可能不是全局文献池的 [1]
- **修复**：参考之前的 fix — 使用全局索引

### 10. 论文类型模板缺 5 种（前次 session 已修复）
- ✅ 已修复

---

## 🟢 P2 — 锦上添花（可用但有改善空间）

### 11. RAG 索引每次写作重建
- `PageRetriever.load_papers()` 每次都重建 TF-IDF
- 28 篇文献时无所谓，但 100+ 篇时有感知延迟
- **优化**：按 project_id 缓存

### 12. 评分只给分数不给改写建议
- `score_paper()` 返回 `{score, reasoning}` 但不给具体修改方案
- 用户知道"逻辑性 6 分"但不知道怎么改到 8 分
- **建议**：加 `suggestions` 字段，LLM prompt 要求给具体改进点

### 13. 查重报告缺可视化
- 只有 JSON 统计 + 简单文本提示
- 没有"红色高亮重复段落"（对标 PaperPass/知网查重的标红功能）
- **优化**：返回重复段落的起止位置，前端高亮渲染

### 14. LaTeX 导出假定了模板 cls/sty 已安装
- `ieeetran.cls`, `acmart.cls`, `neurips_2024.sty` 等均需用户手动安装
- 用户导出后无法编译，体验断裂
- **建议**：导出时加检测 + 提示，或提供最小化模板

### 15. BibTeX cite_key 碰撞风险
- `format_bibtex()` 用 `firstAuthorYear + chr(96+index)` 
- 同作者同年多篇 → key 冲突
- **修复**：加标题首词 → `firstAuthorYearFirstTitleWord`

### 16. 导出内容中的 `@image#1` 占位符未处理
- AI 生成内容偶尔带 `@image#1-5` 标记但无替换逻辑
- 用户反馈过此问题
- **修复**：前端/buildFullPaper() 过滤或后端 post-process

### 17. 前端搜索框无"搜索中"加载态
- 用户点搜索后无反馈，等几秒才出结果
- **修复**：加 spinner + "正在搜索 arXiv/CrossRef..." 状态

### 18. 文献卡片没有"已引用/未引用"标记
- 28 篇文献，哪些已被 AI 在正文中引用，哪些没有？
- 用户无法一目了然
- **优化**：搜索正文 `[n]` 标记，前端点亮已引用文献

---

## 📊 统计

| 级别 | 数量 | 说明 |
|------|------|------|
| P0 真缺陷 | 4 | STORM不可用/在线查星空壳/PDF中文依赖/CNKI需配Key |
| P1 体验差 | 6 | 空结果/未持久化/fallback假分/截断不透明/引用编号/模板缺 |
| P2 锦上添花 | 8 | 索引缓存/改写建议/标红/LaTeX检测/BibTeX/占位符/加载态/引用点亮 |

**总代码量**：~5,500 行 Python + ~2,600 行 Vue

**骨架评价**：架构设计合理，模块边界清晰，agent pipeline 完整
**细节评价**：18 个细节问题中 10 个是 P0/P1 级别，"能用"和"好用"之间还有明显差距
