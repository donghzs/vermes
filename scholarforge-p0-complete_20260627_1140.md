# ScholarForge P0 三项完成 & 全链路验证

**日期**: 2026-06-27 11:40 | **分支**: feature/scholarforge

---

## P0-1: 引用真实性验证 ✅

**文件**: `hermes_cli/scholarforge/agents/__init__.py`

新增两个函数：
- `_validate_citation_refs(text, papers)` — 扫描正文 [n] 引用，逐条验证是否对应真实文献池
- `_fuzzy_match_title(candidate, papers)` — rapidfuzz 模糊匹配（无依赖时 fallback substring）

集成点：
- `LiteratureAgent.run()` 生成综述后自动验证引用
- `WritingAgent.run()` 章节写完后验证引用
- 检测到无效引用时 yield `{"type": "warning"}` 事件
- prompt 中加了"只引用上方给出的文献，不要编造"

**测试结果**：`[5]` 被正确检测为无效引用（文献池仅 3 篇）

---

## P0-2: 导出模块 ✅

**文件**: `hermes_cli/scholarforge/export/__init__.py` (新建, 186 行)

导出端点（挂载在 blueprint）：
- `GET /api/scholar/export?format=markdown` — Markdown 论文（含参考文献列表）
- `GET /api/scholar/export?format=bibtex` — BibTeX 格式
- `GET /api/scholar/export?format=references` — 参考文献提取 + YAML + BibTeX
- 支持 `title` 参数自定义论文标题
- 预留 `format_type` 参数（cvpr/neurips/acl 模板格式）

**测试结果**：
- Markdown 导出: 91 chars ✅（空 session，正常）
- BibTeX 导出: 0 条 ✅（空 session，正常）
- 验证通过（HTTP 200，无 500）

---

## P0-3: 付费文献源可插拔接口 ✅

**文件**: `hermes_cli/scholarforge/search/__init__.py`

新增：
- `_PAID_SOURCE_DEFINITIONS` — 4 个付费源定义（Scopus/WoS/CORE/Google Scholar）
- `get_paid_source_configs()` — 返回付费源配置（隐藏 API Key）
- `activate_paid_source(name, key)` — 激活付费源（内存存储 API Key）
- 端点：`GET /api/scholar/sources` + `POST /api/scholar/sources/activate`

**测试结果**：
- 免费源: 3 个（arxiv/crossref/semantic_scholar）
- 付费源: 4 个定义
- CORE 激活成功 ✅

---

## 顺修

- arXiv: HTTP → HTTPS（修复 301 重定向）
- `export` 端点: `assembled_text` → `draft`（修复 500）
- web_server.py 改动量: **仅 2 行**（白名单 + blueprint 注册）

---

## 全链路验证记录

```
端点                          方法  状态
/api/scholar/agents           GET   200 ✅
/api/scholar/search           GET   200 ✅ (3 篇 Crossref)
/api/scholar/stream           POST  200 ✅ (选题分析 2000+ 字)
/api/scholar/sources          GET   200 ✅ (3 免费 + 4 付费)
/api/scholar/sources/activate POST  200 ✅ (CORE 激活)
/api/scholar/export           GET   200 ✅ (Markdown/BibTeX)
```

---

## 已知约束（未变）

- Semantic Scholar 持续 429
- CJK <3 字符 trigram 限制
- 前端导出按钮待加（后端端点已就绪）
- 付费源搜索函数待实际实现（Scopus API 调用）
