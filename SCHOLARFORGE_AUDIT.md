# ScholarForge 学术垂直能力深度审计

> 审计时间：2026-08-11
> 审计范围：`vermes_cli/scholarforge/`（29 个模块 + 21 个测试文件）
> 审计目的：学术圈 launch 前的能力真实性核验
> 方法：源码逐行阅读 + **离线可复现的实跑验证**（不依赖测试套件，不接受"函数名叫 verify 就是在验证"）

---

## 0. 一句话结论

**ScholarForge 的学术骨架是真的，但"引用真实性"这条信任命门目前是断的——而且断在最不易察觉的地方：外观完全正常，编号规整，格式合规，唯独指向错误。**

按现状 launch，最可能的失败场景不是"用户觉得不好用"，而是**用户拿去投稿，被审稿人发现引用对不上号**。这一枪打中的是学术工具唯一不能失去的东西：可信度。

好消息：**7 个缺陷里有 4 个是 1–20 行的改动**。地基没塌，是几颗螺丝拧错了位置。

> **2026-08-12 更新**：F-2/F-3/F-4/F-5/F-6/F-7/F-20/F-21 已于 commit `e79d62f4e` 修复，本文 §8 记录逐条核验结果（全部通过，含索引对齐与 usage 捕获顺序两处易错点的额外核准）。
>
> 但复核中**新发现两个 P0**（F-23 无相关度阈值 → 强塞无关文献、F-24 跨语言匹配失效），**严重度高于已修的 F-2**。Launch 阻断项因此并未清零——修订后的门槛见 §9。

---

## 1. 缺陷总表

| ID | 缺陷 | 严重度 | 证据 | 修复量 |
|----|------|--------|------|--------|
| F-1 | 写作路径零文献支撑，`[n]` 是空头支票 | 🔴 P0 | `tools.py:566-760`、`:681` | 中 |
| F-2 | 引用替换级联串号（两套实现都有） | 🔴 P0 | `tools.py:1283`、`citation_provider.py:413` | 小 |
| F-3 | 未匹配占位符与真引用撞号 | 🔴 P0 | `tools.py:1271-1283` | 小 |
| F-4 | 替换后验证器传单元素列表 → 全量误报 | 🔴 P0 | `tools.py:1291-1301` | 1 行 |
| F-5 | 引用真实性闸门默认不联网 | 🔴 P0 | `tools.py:1324`、`quality_gate.py:138` | 1 行 |
| F-6 | `confidence=0.3` 恰好卡在 `<0.3` 边界外 | 🔴 P0 | `validators.py:192`、`quality_gate.py:140` | 1 字符 |
| F-7 | 参考文献被未引用文献注水（3 引用 → 20 条目） | 🟠 P1 | `citation_provider.py:294,417` | 小 |
| F-8 | 运行时验证器验错对象 | 🟠 P1 | `agent/vertical_validators.py` | 小 |
| F-9 | 查重无外部语料库，只能文内自比 | 🟠 P1 | `plagcheck.py:160` | 宣传口径 |
| F-10 | `check_aigc` 是启发式，非检测模型 | 🟠 P1 | `plagcheck.py:188` | 宣传口径 |
| F-11 | "在线查重"零 API 调用 | 🟠 P1 | `plagcheck.py:539` | 宣传口径 |
| F-12 | 中文检索被 registry 短路，百度学术永不执行 | 🟠 P1 | `search/__init__.py:275-283` vs `:63` | 中 |
| F-13 | `baidu_scholar` 实为 Crossref+OpenAlex 包装 | 🟠 P1 | `baidu_scholar_fetcher.py:4-11,253` | 宣传口径 |
| F-14 | 源名错配 → 静默降级 | 🟡 P2 | `search/__init__.py:59,288-290` | 小 |
| F-15 | `research_map` 纯 LLM 捏造学术地形 | 🟠 P1 | `research_map.py:59` | 中 |
| F-16 | 导出参考文献来自正则解析正文，不读库 | 🟡 P2 | `export/__init__.py:24,249` | 中 |
| F-17 | `quality.py` 大半是死代码 | 🟡 P2 | `quality.py:82,157,303,504,526` | 接线 |
| F-18 | `style_profile.py` 三函数零调用 | 🟡 P2 | `style_profile.py:179,255,305` | 接线 |
| F-19 | `_fallback_score` 硬编码 `originality=5.0` | 🟡 P2 | `scoring.py:165` | 小 |
| F-20 | f-string 缺失，`{label}` 泄漏进提示词 | 🟡 P2 | `tools.py:680` | 1 字符 |
| F-21 | `stream_call_llm` 零 token 记账 | 🟡 P2 | `tools.py:447,687,1715` | 中 |

### 修复后复核新增（见 §8，commit `e79d62f4e` 之后）

| ID | 缺陷 | 严重度 | 证据 | 修复量 |
|----|------|--------|------|--------|
| F-22 | 参考文献编号跳号（修 F-7 引入） | 🟡 P2 | `citation_provider.py:411,424` | 小 |
| F-23 | **匹配无最低分阈值 → 强塞无关文献** | 🔴 **P0** | `citation_provider.py:394-406` | 约 6 行 |
| F-24 | **跨语言匹配完全失效（中文正文引英文文献）** | 🔴 **P0** | `citation_provider.py:343-368` | 中 |
| F-25 | 两条引用替换路径能力严重不对等 | 🟠 P1 | `tools.py:1253-1308` vs `citation_provider.py:394-424` | 架构 |

**F-1 ~ F-7 中的 F-2/F-3/F-4/F-5/F-6/F-7 及 F-20/F-21 已修并逐条核验通过**（§8.1）。但复核过程中发现 F-23/F-24 两个新的 P0——**它们比已修的 F-2 更严重**，因为 F-2 只在多引用时串号，而这两个在中文主力场景下**每次引用都可能指向不相关文献**。

---

## 2. P0 详解：引用真实性链路的七处断点

### F-1 写作路径零文献支撑

`_handle_scholarforge_write`（`tools.py:566-760`）**完全没有检索或注入任何文献**。全 handler 范围内 grep `list_literature|search_|rag|retrieve` —— 零命中。

提示词末尾（`tools.py:681`）直白写着：

```
引用文献时使用 [n] 标记（n为编号占位，用户后续会替换为真实文献）。
```

也就是说，**默认写作流程 = 让 LLM 在没有任何文献的情况下自由发挥，并把引用真实性的责任推给"用户后续"。** 而这个"后续"是一个独立可选工具 `scholarforge_replace_citations`（`tools.py:3645`）——不调就不会发生。

内容随后经 `save_section` 落库（`tools.py:704`，带回读校验）。所以：**指向虚无的引用标记会被持久化，并原样进入导出。**

RAG 能力是存在的（`rag.py`，被 `blueprint.py:549,793`、`literature_cards.py:204` 使用），**唯独写作主路径没接**。这是典型的「已实现但未接线」。

> 三态判定：能力存在 / 主路径未接线 / 用户不可见

### F-2 引用替换级联串号 —— 两套实现同病

系统里有**两套**引用替换：

| 路径 | 实现 | 触发方式 |
|------|------|----------|
| 独立工具 | `tools.py:1271-1283` | 用户/agent 主动调 |
| 一键成文（旗舰） | `citation_provider.py:407-413`，被 `blueprint.py:1171` 调用 | 自动 |

**两者用的是同一个有缺陷的算法**：在累积字符串上顺序执行全局 `str.replace()`。

```python
result_draft = draft
for m, nums in match_to_nums:
    result_draft = result_draft.replace(original, replacement)   # ← 已替换结果会被后续规则二次替换
```

实跑复现（旗舰路径）：

```
原文  : 方法A见[5]。方法B见[3]。
映射  : 5→3, 3→8
期望  : 方法A见[3]。方法B见[8]。
实际  : 方法A见[8]。方法B见[8]。     ❌
```

`[5]` 先变成 `[3]`，随后被 `[3]→[8]` 规则二次命中。**两处引用最终指向同一篇不相干的文献，而外观完全正常。**

这是本次审计中最危险的一个：它不报错、不告警、肉眼不可辨，只有审稿人逐条核对才会发现。

### F-3 未匹配占位符与真引用撞号

同一循环中，`if all(r is not None for r in mapped)` 不成立时占位符**原样保留**。若 `[1]` 搜不到文献而 `[2]→[1]`，正文里就出现两个 `[1]`——一个指向真实文献，一个是虚空。

实跑：参考文献列表只有 2 条，正文出现 2 个 `[1]`。读者无从区分。

### F-4 验证器传单元素列表 → 全量误报

`tools.py:1291-1301` 在替换后做"交叉验证"，但每次循环只构造**一个** `_P()` 对象传进去：

```python
result = _fuzzy_verify(ref["ref_num"], result_draft, [_P()])   # papers 长度恒为 1
```

而 `_fuzzy_verify` 第一件事就是范围检查：

```python
if ref_num < 1 or ref_num > len(papers):
    return CitationVerifyResult(ref_num, 0, "引用编号超出文献范围", False, method="range_only")
```

**凡 `ref_num >= 2` 一律直接返回 0 分、`accurate=False`。**

实跑：3 篇标题与上下文高度匹配的真实文献，`[2]` `[3]` 全部被打成"验证分数 0/10，建议人工核查"。

后果是双向的：用户看到满屏误报 → 学会忽略验证报告 → 真正的问题也被一起忽略。**一个总是喊狼来了的验证器，比没有验证器更糟。**

### F-5 引用真实性闸门默认不联网

`tools.py:1324` 硬编码：

```python
citation_gate_report, _ = await run_citation_gate(ref_list, mode="flag")
```

而 `quality_gate.py:138`：

```python
checks = await verify_citation_authenticity(papers, enable_online=(mode == "block"))
```

**`mode="flag"` ⇒ `enable_online=False` ⇒ 从不联网。**

`validators.py:314` 有一个货真价实的 Crossref DOI 核验实现——**它在默认配置下永远不会被调用**。

实跑：3 篇 100% 虚构文献（作者、期刊、DOI 全部编造），闸门返回空报告、`blocked=False`，用户看不到任何警告。

### F-6 `confidence=0.3` 卡在 `<0.3` 边界外 —— 最讽刺的一个

即使联网了，也拦不住。

`validators.py:192`，当在线验证**明确查无此文献**时：

```python
check.confidence = 0.3
check.issue = "在线验证未找到匹配文献，可能不存在或为虚构"
```

`quality_gate.py:140` 的判定：

```python
fake = [c for c in checks if not c.verified and c.confidence < 0.3]
```

`0.3 < 0.3` → `False`。

**"可能为虚构"是系统能给出的最强编造信号，而它恰好被边界条件排除在外。**

实跑（mock 两个权威源均正常响应"查无此文献"）：`run_citation_gate(mode="block")` 依然返回空报告、不拦截。改成 `<= 0.3` 立即命中。

同一 off-by-one 也出现在 `validators.py:389` 和 `:410`。

### F-7 参考文献被未引用文献注水

`citation_provider.py:294` 拉取 `limit=max(20, max_ref)` 篇，`:417` 把**全部拉取结果**写进参考文献列表：

```python
for i, c in enumerate(citations, 1):
    refs_text += f"[{i}] ..."
```

实跑：正文只引用 3 处，**参考文献列表 20 条，其中 17 条从未在正文出现。**

这在学术评审中是硬伤——未引用文献出现在参考文献列表，是典型的"凑数"信号。

---

## 3. P1：名实不符（宣传落差 = 法律风险）

### 学术诚信模块

| 声称 | 实际 | 证据 |
|------|------|------|
| 查重 | 只做**文内段落 SimHash 自比**，无任何外部语料库。抄袭已发表文献 100% 查不出 | `plagcheck.py:160` |
| AIGC 检测 | 8 维启发式（句长变异系数 / 连接词密度 / 四字套话密度 / 主语回避…），**无判别模型** | `plagcheck.py:188` |
| 在线查重 | `get_online_plag_services()` 只返回**静态 URL 字典**（PaperPass/大雅/知网官网链接），零 API 调用 | `plagcheck.py:539` |

值得肯定的是，结果里 `checked_sources` 已诚实标注为启发式（`plagcheck.py:481,527`）——**代码比宣传诚实**。

**但这是最高优先级的对外口径风险**：学生用户如果据此判断"能过检"，投稿被期刊查重打脸，后果不只是差评。

### 中文检索被短路

中文查询的默认源是 `["baidu_scholar", "cnki"]`（`search/__init__.py:62`），但路由逻辑（`:275-283`）是：

```python
registry_papers = await _search_via_registry(query, limit, sources)
if registry_papers:
    ...
    return          # ← 一有结果就返回
# 以下本地源（含 baidu_scholar）永不执行
```

而 `baidu_scholar` **不在 registry 中**，只存在于本地 `_SEARCH_SOURCES`（`:63`）。registry 里的 `semanticscholar`/`core` 先返回结果 → 直接 `return` → **百度学术分支成为不可达代码**。

叠加 F-13：`search_baidu_scholar` 本身其实是 Crossref + OpenAlex 的包装（`baidu_scholar_fetcher.py:4-11,253`），**根本没碰百度学术**。

所以"支持知网/百度学术"这句话，实际上：源不可达 + 即使可达也是英文库包装。**中文是主力场景，这个落差最伤。**

### `research_map` 捏造学术地形

`research_map.py:59` 的"研究共识 / 学术争议 / 研究空白"完全由 LLM 生成，**无任何文献支撑**。

这比编造一条引用更危险：编造引用可查，编造"领域共识"无从证伪，而它恰恰是用户最容易直接引用进论文的内容。

---

## 4. 真能用的部分（护城河仍在）

审计不是只找问题。以下能力经核验**确实可用**：

- **引用图谱**：走真实 Semantic Scholar API + 30 天缓存 —— `citation_graph.py:13,28`
- **Crossref DOI 核验**：实现正确、真联网 —— `validators.py:314`（只是默认没被触发，属 F-5）
- **LaTeX / BibTeX 导出**：`thebibliography` + `\bibitem{refN}`、`[n]→\cite{refN}` 语法合法 —— `export/latex.py:554,606,625`
- **CSL JSON**：字段符合规范 —— `export/__init__.py:291,309`
- **全文导出编号一致性**：正文与文末同源枚举，编号保证一致 —— `export/full.py:53,84`
- **摘要回填**：真实 S2 数据 + 缺失时优雅降级 —— `abstract_backfill.py:27,44-49`
- **论文打分**：LLM 驱动，`_make_llm` 已正确注入 —— `scoring.py:20` + `tools.py:2006`
- **文献卡片**：基于真实检索结果做 7 字段抽取，有事实锚点 —— `literature_cards.py:42,147,164`
- **默认零配置可用源约 7 个**：openalex / crossref / pubmed / arxiv / semanticscholar / europepmc / doaj —— `search/__init__.py:1198-1211`
- **知网降级**：无 key 时降级 OpenAlex CN，行为诚实 —— `cnki_fetcher.py:347,353-376`
- **写回校验**：`save_section` 回读校验 + 失败明确报错不假装成功 —— `tools.py:704-708`

**26 个工具的骨架、导出的格式合规性、检索的多源覆盖，这些是真的。** 问题集中在"验证"这一层——而验证恰恰是学术工具的立身之本。

---

## 5. 整改方案

### 第一梯队：4 个改动 ≤ 20 行，先把血止住

| 改动 | 文件 | 内容 | 效果 |
|------|------|------|------|
| ① | `quality_gate.py:140` | `< 0.3` → `<= 0.3` | "查无此文献"终于会报警（**1 字符**） |
| ② | `quality_gate.py:138` | `enable_online=(mode != "off")` | flag 模式也联网核验（**1 行**） |
| ③ | `tools.py:1291-1301` | 传完整 `papers` 列表而非 `[_P()]` | 消除全量误报（**1 行**） |
| ④ | `tools.py:680` | 补 `f` 前缀 | `{label}` 不再泄漏（**1 字符**） |

同步修 `validators.py:389,410` 的同类边界。

### 第二梯队：消除级联串号（两处同改）

把顺序 `str.replace()` 换成**单次正则回调替换**：

```python
def _sub(m):
    nums = expand_citation(m.group(0))
    mapped = [num_to_ref.get(n) for n in nums]
    if all(r is not None for r in mapped):
        return f"[{','.join(map(str, mapped))}]"
    return f"[?{m.group(0)[1:-1]}]"        # 未匹配显式标记，杜绝撞号
result_draft = cite_pattern.sub(_sub, draft)
```

一次扫描、位置精确、已替换内容不再被二次命中，顺带解决 F-3。
需同时改 `tools.py:1271-1283` 与 `citation_provider.py:407-413`。

**必须配 R5 反向验证**：把新测试拷到修复前的 commit 上跑，确认它会失败——否则无法区分"通过是因为修好了"还是"根本没测到"。

### 第三梯队：写作路径接文献（F-1）

两个选项：

- **A（轻）**：`write` 前先 `list_literature(project_id)`，把已有文献注入提示词，要求 LLM 只引用给定列表中的编号。改动小，立竿见影。
- **B（重）**：接 `rag.py` 的 `PaperRetriever` 做 per-section 语义检索（`blueprint.py:549` 已有先例）。质量更高，工作量大。

建议先 A 后 B。同时把参考文献列表改为**只列被正文实际引用的条目**（修 F-7）。

### 第四梯队：对外口径（launch 前必须定稿）

这不是代码问题，是**责任问题**：

- 「查重」→ 改为「**文内重复度自查**」，明确标注"不比对外部已发表文献库"
- 「AIGC 检测」→ 改为「**AI 写作风格自检（启发式）**」，明确"不等同于任何期刊使用的 AIGC 检测服务"
- 「支持知网 / 百度学术」→ 修好 F-12 再说；未修复前不得出现在宣传材料
- `research_map` 输出加显式免责：「以下为模型推断，未经文献验证，请勿直接引用」

---

## 6. Launch 门槛判定

**阻断项（不修不能发）**

- F-4 / F-5 / F-6 —— 三个 1 行内改动，不修等于"验证功能是装饰品"
- F-2 / F-3 —— 引用串号，直接摧毁可信度
- 宣传口径四条 —— 法律与口碑风险

**强烈建议修（发布首周内）**

- F-1 写作接文献 —— 这是产品的核心叙事「本地 + 垂直 + 可验证」能否成立的前提
- F-7 参考文献注水
- F-12 中文检索短路 —— 中文是主力场景

**可延后**

- F-15/16/17/18/19（死代码接线、导出数据源、硬编码兜底分）
- F-21 `stream_call_llm` token 记账

**F-21 补充核查（本轮实证）**：全仓 grep（不截断、排除 `__pycache__`）确认 `stream_call_llm` 的调用方**只有两个**——`write`(`tools.py:687`) 和 `polish`(`:1715`)，恰是全模块最烧 token 的两个入口。而 G4a 的记账埋点 `_accumulate_llm_usage` **只存在于 `_call_llm`（`:412`）**，`stream_call_llm`（`:447-513`）全函数体内零调用，请求 body（`:471-475`）也没有 `stream_options`。

结论：**G4a「ScholarForge token 落 `tool_usage` 表」在两条主力写作路径上记账恒为 0。** 不是少算，是完全没有——用户看到的 ScholarForge 成本会系统性偏低。

修法（约 10 行，需 fail-open）：

```python
body = {..., "stream": True, "stream_options": {"include_usage": True}}
...
_final_usage = None
async for line in resp.aiter_lines():
    chunk = json.loads(payload)
    if chunk.get("usage"):          # 末帧携带
        _final_usage = chunk["usage"]
    ...
# 流结束后
if _final_usage:
    _accumulate_llm_usage(_final_usage, creds["provider"], effective_model,
                          creds.get("base_url"), creds.get("api_key"))
```

注意：`stream_options` 并非所有 OpenAI 兼容 provider 都支持，部分会 400 拒收 → 必须保留「无 usage 则跳过」的既有语义，且建议对 400 做一次去掉该字段的重试，否则修记账反而打断写作主路径。

---

## 7. 一个判断

这次审计里，**没有一个缺陷是因为能力不足**。骨架、多源检索、导出合规、DOI 核验、图谱缓存——难的部分都做对了。

出问题的全是"最后一厘米"：一个 `<` 少写了个 `=`，一个列表传成了单元素，一个默认参数让联网核验永远关着，一个 `str.replace` 图省事没考虑级联。

**这类缺陷有个共同特征：它们让功能"看起来在工作"。** 验证器有输出、闸门有返回、引用有编号、格式规整——每一层都在正常运转，只是没在做它声称在做的事。

这也解释了为什么 21 个测试文件没抓住它们：测试验证的是"代码按自己的想法运行"，而这些 bug 恰恰是"想法本身错了"。

**修完这些，ScholarForge 就配得上「可验证」这个卖点。** 而在修完之前，这个卖点是负资产——它会把用户的信任放大成失望。

---

## 8. 修复后复核（commit `e79d62f4e`）—— 7 项确认 + 4 项新发现

> 方法：不采信完工报告，逐条静态对账 + 真实调用实跑。

### 8.1 已修复项逐条对账

| ID | 核验方式 | 结论 |
|----|----------|------|
| F-6 | 全仓 grep `confidence < 0.3` | ✅ 零残留；三处（`quality_gate.py:140`、`validators.py:389,410`）全改 `<= 0.3` |
| F-5 | `quality_gate.py:138` | ✅ `enable_online=(mode != "off")` |
| F-4 | `tools.py:1337` | ✅ 传完整 `all_papers`。**并核准索引对齐**：`_fuzzy_verify` 用 `papers[ref_num-1]` 严格位置索引，而 `next_ref_num` 从 1 起逐条 +1（`:1246/1295/1308`），故精确对应——**未引入错位** |
| F-2/F-3 | 两套实现均查 | ✅ `tools.py:1321` 与 `citation_provider.py:414` 都改为单次正则回调，`[?n]` 标记均在（`:1320`/`:413`）。`agents/__init__.py:1389` 残留的 `draft.replace` 经核为「标记无效编号」语义：字面量 `[5]` 不命中 `[15]`、产物 `[?5?]` 不被二次命中，**幂等安全，非漏改** |
| F-7 | 真实调用 | ✅ 注水消除（20 条 → 只列被引用的 3 条） |
| F-20 | `:688`/`:718` | ✅ 两处 f 前缀齐备 |
| F-21 | `:479/:498/:534` | ✅ `stream_options` + 400 去字段**真重发**（`resp2`）+ usage 捕获。**关键顺序正确**：usage 捕获置于取 `delta` 之前——OpenAI 末帧 `choices` 为空数组会抛 IndexError 被 except 吞掉，顺序颠倒则 usage 永久丢失 |

### 8.2 F-22 参考文献编号跳号（P2，修 F-7 引入）

`citation_provider.py:411,424` 正文与列表都用 `citations.index(c)+1`（**全量池**索引），指向一致但编号不连续。

实跑：正文 `[11][12][13]`，列表也从 `[11]` 起——缺 `[1]`–`[10]`。

无造假风险，但期刊排版要求连续编号。修法：建 `idx_to_seq = {old: new for new, old in enumerate(cited_indices, 1)}`，正文与列表都走映射（需把 `cited_indices` 提到 `_sub_citation` 之前计算）。

对照：`tools.py` 用 `next_ref_num` 连续分配，**无此问题**。

### 8.3 🔴 F-23 匹配无最低分阈值 → 强塞无关文献（P0，新发现，比已修的 F-2 更严重）

`citation_provider.py:394-406`：

```python
best_idx, best_score = -1, -1.0
for i, c in enumerate(citations):
    if i in used_citations: continue
    score = score_relevance(c, kw)
    if score > best_score:
        best_score, best_idx = score, i
if best_idx >= 0:                      # ← 没有任何最低分门槛
    num_to_citation[n] = citations[best_idx]
```

`best_score` 初始 `-1.0` ⇒ **任何 score ≥ 0 都会当选**。全部候选相关度为 0 时，胜者由 `difflib` 浮点噪声决定。

实跑（候选池刻意只放三篇毫不相关的）：

```
正文  : Adversarial training improves NMT robustness [3].
引用  : [3] Medieval Manuscript Preservation. V3, 2020. DOI: 10.1/3
```

**「对抗训练提升 NMT 鲁棒性」引用了「中世纪手稿保护」，配格式合规的 DOI。**

比 F-2 更危险：F-2 是两个引用撞同一篇（尚可察觉），这里每个引用都指向一篇**真实存在、格式完美、内容毫不相关**的文献。审稿人逐条核对即构成学术不端指控。

对照：`tools.py:1277` 有 `if best_score < 0.3: failed.append(n); continue` + 明确告警。**旗舰路径缺这道闸门。**

修法（约 6 行）：加阈值，未达标就**不放进** `num_to_citation`——F-3 的修复已让未匹配项自动输出 `[?n]`，正好接上：

```python
MIN_RELEVANCE = 0.3
if best_idx >= 0 and best_score >= MIN_RELEVANCE:
    num_to_citation[n] = citations[best_idx]
    used_citations.add(best_idx)
else:
    logger.warning(f"[{n}] 无相关文献（最高分 {best_score:.2f}），保留占位符")
```

### 8.4 🔴 F-24 跨语言匹配完全失效（P0，新发现，命中中文主力场景）

`score_relevance`（`:343-368`）四因子全是**同语言字面比对**：专有名词正则只取 `[A-Z][A-Za-z0-9]{2,}`、token 重叠按字面交集、`difflib` 按字符相似、venue 按子串。中文正文 vs 英文标题 ⇒ 四项**全部恒为 0**。

实跑（池中放了一篇完美匹配的）：

```
正文  : 对抗训练可提升神经机器翻译鲁棒性[1]。
池中  : ["Unrelated Database Survey",
         "Adversarial Training for Neural Machine Translation Robustness",  ← 完美匹配
         "Quantum Annealing Methods"]
选中  : [1] Unrelated Database Survey        ❌
```

英文正文同池**能选对**（已实跑对照）——所以算法本身没坏，是跨语言这条路根本没铺。

**中文论文引用英文文献是学术常态**，正是增长战略定为尖刀的中文学术用户的默认场景。叠加 F-23 无阈值 ⇒ 旗舰「一键成文」对中文用户**几乎必然产出错误引用**，且外观完全正常。

对照：`tools.py:1271` 走 `llm_rerank`，LLM 天然跨语言，**独立工具路径无此问题**。

### 8.5 F-25 两条路径能力严重不对等（架构层，P1）

| 能力 | 独立工具 `tools.py` | 旗舰一键成文 `citation_provider.py` |
|------|---------------------|--------------------------------------|
| 相关度阈值 | ✅ `< 0.3` 跳过 + 告警（`:1277`） | ❌ 无（F-23） |
| LLM 精排 | ✅ `llm_rerank` top-5（`:1271`） | ❌ 仅四因子启发式（F-24） |
| 本地文献库 | ✅ 合并进候选池（`:1253`） | ❌ 不使用 |
| 重复引用合并 | ✅ 按标题归并到同一编号（`:1281`） | ⚠️ 仅 `used_citations` 防复用 |
| 编号连续性 | ✅ `next_ref_num`（`:1308`） | ❌ 跳号（F-22） |
| 匹配过程可见 | ✅ `match_log` 逐条 ✅/⚠️ | ❌ 仅 `logger.debug` |

**用户默认走的是能力更弱的那条。** 建议不要给 `citation_provider` 逐项打补丁，而是把 `tools.py` 那套已验证的管线（粗排 → LLM 精排 → 阈值 → 连续编号 → match_log）抽成公共函数供两处共用——一改同时消灭 F-22/F-23/F-24，且复用的是已过 R5 的实现。

### 8.6 R5 测试为何没抓到这些

`tests/scholarforge/test_sf_p0_fixes.py` 的 F-7 用例有两处方法论问题：

1. `test_cited_only_in_source`（`:222-226`）是**源码字符串断言**：`assert "cited_indices" in src`。改个变量名就红，逻辑写错反而绿——验证的是「代码长什么样」，不是「代码做什么」。
2. `test_no_uncited_refs`（`:257-259`）只断言**条数** `<= len(cited_nums) + 1`，不查编号连续性、不查所选文献与论述是否相关。

「测试镜像实现而非校验契约」的又一变体。补测建议改为断言**行为契约**：(a) 编号必须 `1..N` 连续；(b) 候选池全无关时必须输出 `[?n]` 而非任何 `[数字]`；(c) 池中存在完美匹配时必须选中它（含中文正文 + 英文文献的跨语言用例）。

---

## 9. 修订后的 Launch 门槛

**阻断项（不修不能发）**

- ~~F-4 / F-5 / F-6 / F-2 / F-3~~ ✅ 已修并核验（`e79d62f4e`）
- 🔴 **F-23 无阈值强塞无关文献** —— 每次引用都可能指向不相关文献
- 🔴 **F-24 跨语言匹配失效** —— 中文用户主力场景几乎必然出错
- 宣传口径四条 —— 法律与口碑风险

**强烈建议（发布首周内）**

- F-25 两路径能力对齐（抽公共匹配管线，一并解 F-22/23/24）
- F-1 写作接文献
- F-12 中文检索短路

**可延后**：F-15/16/17/18/19、F-22（若已随 F-25 一并解决则关闭）

---

## 附录：复现脚本

所有结论均可离线复现（不调 LLM、不联网）：

| 脚本 | 覆盖 |
|------|------|
| `/tmp/sf_repro.py` | F-3 撞号、F-4 全量误报 |
| `/tmp/sf_repro2.py` | F-5 闸门对编造文献失明 |
| `/tmp/sf_repro3.py` | F-6 confidence 边界（mock 权威源返回"查无此文献"） |
| `/tmp/sf_repro4.py` | F-7 参考文献注水、F-2 旗舰路径级联串号 |
| `/tmp/sf_verify_f7.py` | F-22 编号跳号（复算编号逻辑） |
| `/tmp/sf_verify_f22.py` | F-22 真实调用验证跳号 |
| `/tmp/sf_verify_f23.py` | **F-23 强塞无关文献 + F-24 跨语言失效**（三组对照实验） |

运行：`PYTHONPATH=$(pwd) .venv/bin/python /tmp/<脚本>`
（注意：缺 `PYTHONPATH` 会报 `ModuleNotFoundError: No module named 'vermes_cli'`）

建议整改时把这些脚本转成正式回归测试，并按 R5 纪律做反向验证——**且断言行为契约而非源码字符串**（见 8.6）。
