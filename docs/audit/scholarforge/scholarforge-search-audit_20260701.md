# ScholarForge 搜索源 — 实测逐项核实报告

**时间**: 2026-07-01 15:30
**方法**: 逐条实测 + 源码审计（非复述前份报告）
**参考**: 前份 session 结论（summary_3e7e117f… / summary_3fa01299…）

---

## 前份报告 8 项指控 — 逐条核实

| # | 指控 | 实测结果 | 核实 |
|---|------|----------|------|
| 1 | arXiv 超时/不可达 | HTTP 200, 0.57s, 5篇高相关论文 | ❌ **误报** |
| 2 | CORE 301 重定向未处理 | CORE 返回 429 (rate limited)，httpx 默认跟随重定向，301不存在 | ❌ **误报** |
| 3 | "只有 Semantic Scholar 有 429 冷却" | 7个免费源全部有 `_is_cooled_down` + `_set_cooldown` (行19-37通用冷却) | ❌ **误报** |
| 4 | Crossref 搜索结果差 | "LLM hallucination detection" 返回5篇，3篇高度相关(Halucheck×2, Code Reliability)，1篇弱相关(EF-LLM) | ⚠️ **夸大** |
| 5 | OpenAlex 503 | 503 Service Unavailable | ✅ **属实** |
| 6 | Semantic Scholar 429 rate limited | 429 Too Many Requests | ✅ **属实** |
| 7 | 冷却应从300s降为60s | 429 是服务端的强制冷却信号，降低冷却时间会让源更快被封 | ⚠️ **误导** |
| 8 | PubMed CS领域无用 | PubMed 确实偏生物医学，结果引用数可能为0 | ⚠️ **半真** |

---

## 源码逐项审计

### 429 冷却 — 7 源全覆盖 ✅

```
search/__init__.py:19-37 (通用冷却)
  _COOLDOWN_UNTIL: dict[str, float] = {}
  _COOLDOWN_SECONDS = 300
  _is_cooled_down(source) — 所有源共享
  _set_cooldown(source) — 所有源共享

各源调用 _set_cooldown (行号):
  arxiv:288  crossref:353  doaj:413  pubmed:491
  semantic_scholar:614  openalex:670  core:755
```

### 搜索架构（行 186-258）

- 并发所有源 → asyncio.wait FIRST_COMPLETED
- 标题去重 (seen_titles, lower[:50])
- min_results=3 后 max_wait=10s 无新结果停止
- 单源 timeout=8s
- 429 冷却源自动跳过

### 前端连通性接口（blueprint.py:675）

`GET /api/scholar/sources/connectivity` 调用 `get_configured_sources()` 返回每个源的可达性状态。

---

## 真实问题（非前份报告内容）

### 1. OpenAlex 间歇性 503（需确认是临时故障还是常态）

实测 HTTP 503，可能是 api.openalex.org 临时宕机。建议：
- 给 OpenAlex 加上非 429 的冷却（503/超时也冷却），避免反复失败拖慢搜索

### 2. Semantic Scholar 长期 429（代码已处理，但体验差）

代码已有 300s 冷却，但从本机连 SS 几乎每次都是 429。建议：
- 支持 S2_API_KEY 环境变量（有 Key 的免费额度高得多）
- 或者降低默认权重，把 arXiv/Crossref 优先级排前

### 3. CORE 无 Key 几乎不可用

CORE v3 API 需要 `CORE_API_KEY`（代码已从环境变量读取），无 Key 时返回 429。建议：
- 连通性检查时对 CORE 标注"需 API Key"

---

## 结论

**前份报告的 P0 级漏洞指控（arXiv 不可达、CORE 301 未处理、冷却仅 Semantic Scholar 有）经逐项实测+源码审计，三项均为误报。** 搜索模块的架构健壮性比前份报告描述的要好得多。

两个真实可优化点：
1. OpenAlex 503 → 加超时/503冷却 (5行改动)
2. Semantic Scholar 长期 429 → 支持 S2_API_KEY (3行改动)

均为 P2 级别，不阻塞功能。
