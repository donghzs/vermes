# ScholarForge 模块集成到 Vermes — 完成报告

**时间**: 2026-06-27 07:25-07:35 GMT+8  
**分支**: `feature/scholarforge`（从干净 `main` 创建）  
**Commit**: `f8c25d930` feat: ScholarForge 论文写作模块 - 完全隔离集成

## 隔离策略

| 维度 | 策略 |
|------|------|
| **端点隔离** | 全部端点 `/api/scholar/*`，不与其他路由冲突 |
| **代码隔离** | 新增 4 个文件在 `hermes_cli/scholarforge/` 目录 |
| **注册隔离** | web_server.py 仅 +9 行 try/except 注册，启动失败不影响主链路 |
| **LLM 复用** | 零侵入调用 `_get_chat_credentials()` + `_resolve_model_provider()` |

## 新增文件 (4)

```
hermes_cli/scholarforge/
├── __init__.py              (  2 行) 包声明
├── agents/__init__.py       (345 行) 5 Agent + STORM Pipeline + ProjectContext
├── search/__init__.py       (279 行) 多源文献搜索 (arXiv/Crossref/Semantic Scholar)
└── blueprint.py             (217 行) /api/scholar/* 路由注册
```

## 修改文件 (1)

```
hermes_cli/web_server.py     (+9 行) try/except 注册 ScholarForge blueprint
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/scholar/agents` | GET | 列出 5 个论文 Agent |
| `/api/scholar/search` | GET/POST | 多源文献搜索 |
| `/api/scholar/stream` | POST | 论文写作 SSE 流式接口 |
| `/api/scholar/sources` | GET | 列出可用搜索源 |

## 5 论文 Agent

- 💡 **TopicAgent** — 选题分析，可行性/创新性评估
- 📚 **LiteratureAgent** — STORM 多视角检索 + 文献综述生成
- 📋 **OutlineAgent** — 结构化论文大纲生成
- ✍️ **WritingAgent** — 逐节撰写，引用文献
- ✨ **RefinementAgent** — 学术语言审校，去 AI 味

## 搜索源

- arxiv（预印本）✅
- crossref（开放获取）✅
- semantic_scholar（AI 驱动，有 429 限流）✅

## 验证结果

- ✅ 导入零报错
- ✅ 端点全部 `/api/scholar/*` 前缀
- ✅ Vermes 核心代码零修改
- ✅ 注册失败不影响主链路 (try/except)

## 下一步

1. 构建前端 + 测试 SSE 流式接口
2. 验证 `/api/scholar/stream` 端到端
3. 添加论文写作 Writer.vue 前端组件
