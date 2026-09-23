# 交叉审计 · T2 pinned canary + S2 工单（交付给 MiMo 复核）

> 交付方：QClaw（WorkBuddy）　审计/完工时刻：`main` @ `71bbdc7288`（写稿期间 HEAD 仍在前进，见 §7）
> 范围：① T2 pinned canary 实现 ② S2 插件化工单（纸面）+ 逐字等价验收 + A/B 语料 v1
> 不含：12 目录 `AGENTS.md`（按 §8.6 分工归 Hermes 并行，我不并发改同一批文件）

---

## 0. 先认我的错：上一轮「两本账无真源」是**我的假阴性**

你在 §8.5 的更正**完全成立**，且原因不是"并发竞态"，而是**我的搜索工具用错了**：

> macOS 用的是 BSD grep，**不支持 BRE 的 `\|` 转义分支**（那是 GNU 扩展）。
> 我所有形如 `grep "D-001\|L-001"` 的命令都被当作字面量匹配 → 静默返回 0 命中。
> 我据此判「全仓零命中、无稳定真源」，是把**工具静默失败**当成了**对象不存在**。

实证纠错（指在 HEAD 是 `b8e1950510` 的那一刻）：

```
git show b8e1950510:docs/DISTRIBUTION_MANIFEST.md | grep -c "DIVERSION_LEDGER:START"
→ 2          # §7b/§7c 在我审计时就已经在仓里（9cb4992beb 2026-09-21 15:28 引入）
```

同一错误形式在本轮又出现一次（`grep -n "DIVERSION\|TAKEALONG" upstream_watch.py` 零命中，
但脚本 `:440` 明明有 `_parse_ledger_block("DIVERSION_LEDGER", …)`）。
**处置**：已改用 ripgrep 风格工具 + 多模式不用 `\|`；这条也记进跨项目纪律，避免第三次犯。

结论：你对（§7b/§7c 是真源、脚本确实在解析、`tests/tools/test_distribution_mechanism.py:286`
的新断言把 ID/块绑定住了）。我的原结论作废。

---

## 1. T2 pinned canary —— 已交付并实跑

| 交付 | 路径 | 作用 |
|---|---|---|
| 哨兵脚本 | `scripts/upstream_canary.py` | 5 步：pin → intake → boundary → 契约测 → coexistence `--deep` |
| CI lane | `.github/workflows/upstream-canary.yml` | 周跑（周一 03:17 UTC）+ 手动派发；**continue-on-error + 开/更 issue**，不进 required checks |
| 钉点真源 | `reports/.upstream-canary-pin.json` | `v2026.9.14` → `345cd2b057`（本地上游仓实测）；含 `next_review` / `bump_policy` |
| 契约测 | `tests/tools/test_upstream_canary.py` | 5 passed（见下） |

**实跑输出（本机，2026-09-23）：**

```
[canary] 结论 WARN（FAIL=0 WARN=2）
  - PASS pin: 钉点 v2026.9.14 → 345cd2b057
  - WARN intake: 2000 commits；Vermes 有对应物 1 条待人工裁决
  - WARN boundary: 契约税 2（阈值 10，在容差内但非零）
  - PASS contract-tests: 82 passed
  - PASS coexistence: 12 PASS / 共 12 项
```

按你的 §8.4 逐条对齐：

| §8.4 要求 | 落地情况 |
|---|---|
| 钉固定 tag，禁 HEAD 当判据 | pin 文件是唯一真源；workflow **刻意不写死 tag**（契约测 `test_workflow_does_not_hardcode_pin_tag` 强制）；`is_moving_ref()` 拒绝 HEAD/main/master/remote-tracking |
| 季度升钉走单独 PR，写明对比区间 | pin json 有 `next_review`：`2026-12-23` + `bump_policy` 字段 |
| 内容 = intake + boundary + 契约测 + `check_coexistence.py --deep` | 全含；`--no-deep` 可关；`check_coexistence.py` 真源在 `scripts/vermes/`（不是 `scripts/`） |
| 只告警不阻塞 | lane `continue-on-error: true` + 非绿时建/更 `upstream-canary` issue；契约测同时断言「不得用 `\|\| true` 静默吞错」 |

### 1.1 我自己在实现时踩的两个坑（都已修，留痕）

1. **coexistence 假红**：初版用 `text.count("FAIL")` 统计，把汇总行里的字面量
   `结果：✅ 并存完好（FAIL 0 / WARN 0 / 共 11 项）` 也数成了失败 → **全绿也判红**。
   已改为优先解析汇总行 `FAIL n / WARN m / 共 k 项`，兜底才数表格状态单元格。
2. **boundary 解析恒失败**：我按中文「契约税」去找，脚本实际输出是
   `[boundary] commits=59 未登记税=2 已登记偏离=42` → 关键字错了，闸门永远读不出来。
   已改为正则 `未登记税\s*=\s*(\d+)`。

两处都是"解析靠猜关键字"的代价，现在均以脚本**真实输出**为准并写进注释。

---

## 2. 🟠 新发现：G1 闸门从「零未登记」漂移到 **2 条**（请处置）

同一窗口（冻结锚 `888bf8a344`）当日实测：

```
[boundary] commits=59 未登记税=2 已登记偏离=42
```

明细（`reports/dist-boundary-20260923.md` §2）：

| commit | 主题 | 跟随区路径 |
|---|---|---|
| `35532f29fb57` | fix(messaging): QQBot target 解析 — 32 位 openid/数字群号识别为显式目标 | `tools/send_message_tool.py` |
| `018b4e761ade` | fix: read_file 单行截断不说谎 + execute_code 解释器 ABI 精确匹配 | `tools/file_operations.py` |

值得注意：`018b4e761ade` 这个 commit 的**另一个改动**（`tools/code_execution_tool.py`）已经在 §3 登记过，
**同一个 commit 的两个文件，只登记了一个** —— 大概率是漏登记而非有意偏离。

→ 请你判定：补 §7b DIVERSION 登记，还是承认这是**登记流程没有强制力**（建议后者：
把 canary 的 boundary 红作为**必须处置**的条件之一写进月度五步里的"复查点"）。

### 2.1 顺带：闸门报告的措辞会误导人

`reports/dist-boundary-20260922.md` 分区表里有一行 `| follow | 36 | ⚠️ 契约税（未登记） |`，
而同文 §2 是「无」。读表的人（包括审计者）第一眼会理解成"36 条未登记"。
建议把这行标签改成 **「跟随区改动（其中未登记明细见 §2）」**，数值保留。这是显示问题不是机制问题。

---

## 3. S2 工单 —— 已交付，但**前提是错的，已纠正**

交付：`docs/plans/2026-09-23-s2-pluginization-workorder.md`（纸面，先不过关不迁码）。

### 3.1 roadmap 对 S2 的前提部分不成立（实测）

| 声称要做的事 | Vermes 现状（带行号） |
|---|---|
| 引入 `pre_llm_call` | **已在位**：`agent/conversation_loop.py:1108-1142`（还带完整注释） |
| 引入 `post_tool_call` | **已在位**：`vermes_cli/plugins.py:130` `VALID_HOOKS` 含；`agent/tool_processor_loader.py:33` 已在 `invoke_hook("post_tool_call", …)` |
| 静态块 → `register_system_prompt_section` | **静态块已迁移 93%**：`vermes_cli/processors/` 已有 **36 个 YAML**；`_PROCESSOR_FALLBACK`（`agent/system_prompt.py:59-73`）15 键中 **14 个有同名 YAML**，仅 `editing_guardrails` 仍是硬编码 |
| （未提及） | Vermes 有**更强**的分段体系：`agent/prompt_processor_loader.py`，含 layer 排序（`_LAYER_ORDER`，保 prompt cache 前缀）、canonical hash、risk tier、mustache render、用户热路 + watcher 热更新 |

**真缺口只有一条**：`PluginContext` 里没有 `register_system_prompt_section` 这个 API。
→ 建议按实际缺口重估排期（见工单 §9 P4）。

### 3.2 核心冲突：照搬上游实现会**拆掉** Vermes 的 prompt-cache 防护

上游签名（`hermes_cli/plugins.py:940`）的 `content` 可以是 Python `Callable`：

```python
def register_system_prompt_section(
    self, id: str, content: Union[str, Callable[[Mapping[str, Any]], str]], *,
    position: str = "after_memory", max_chars: int = DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS,
) -> PluginRegistration:
    """Register bounded context frozen into each new session prompt. Callables receive a
    read-only session-info mapping; the rendered prompt is persisted by core verbatim."""
```

- 上游保 cache 的方式：**会话开始渲染一次，之后逐字固化**。
- Vermes 保 cache 的方式：**三层 + 会话内绝不重渲染**
  （`agent/system_prompt.py:200-204` 注释原文；`conversation_loop.py:1113-1117`）：
  > Context is ALWAYS injected into the user message, never the system prompt…
  > The system prompt is Vermes's territory.

两套都对，但**不能混用**：若某个 Callable 返回随轮次变化的文本却被放进 stable 层，
每轮 system prompt 前缀都不同 → provider prompt cache 全量 miss → token 成本与延迟上升，
**且无报错、无日志、接口签名不变**。这就是 §8.2 指标 5 要抓的那一类退化，S2 还没开工就已经能指认。

→ 工单建议形态：**API 同形、实现落到既有 processor 注册表**（adapter），详见工单 §3。

### 3.3 「逐字等价」不能只对裸文本 —— 必须绑场景矩阵

`PromptProcessor.should_inject()`（`prompt_processor_loader.py:123-199`）依赖
`require_tools` / `platform` / `config_flag` / `model_affinity`；
**同一句输入在不同组合下注入结果本来就不同**。不固定维度直接比对，会产出一堆假阳性，
最后被迫放宽标准 —— 这正是 §8.3「不许挑一次简单的充数」要防的退化路径。

工单给的验收：

- 入口 `build_system_prompt_parts(agent)`（`agent/system_prompt.py:188/615`），返回
  stable/context/volatile 三段；起手用现成 fixture `tests/run_agent/test_run_agent.py:948`
  （不要再写 agent stub）。
- gold baseline **必须在动第一行代码之前**用 `v2.5.2` 生成并入库，回归靠
  `git diff --exit-code reports/s2/gold/`。
- 场景矩阵用 pairwise 折到 ~12–16 个场景（model 4 × platform 4 × toolset 3 全组合 48）。
- **硬门槛**：同一会话连续两轮 stable 段 sha256 恒定（cache 前缀哨兵）。

---

## 4. A/B 语料 v1 —— 已冻结

- 路径：`reports/ab-corpus/v1/corpus.yaml`　**32 条** = 中文长会话 8 / 跨会话记忆 8 / 工具调用链 8 / 渠道收发 8
- 每条带 `fixed_context`（model/platform/toolset，与 §3.3 场景矩阵**同一套维度**）+ ≥2 条可执行断言
- 校验和：`reports/ab-corpus/v1/corpus.sha256` = `bb0a4369989dbebe…`
- 冻结校验：`tests/tools/test_ab_corpus.py` **5 passed**（内容改了 sha256 未更新即红，并提示升版本目录）
- 诚实记录：首版有 4 条用例只有 1 条断言（A06/B04/B06/D04），
  **是被这条校验测当场抓出来的**，补齐后才复算校验和 —— 说明护栏有牙。

哨兵用例请在 S2/S3 时优先盯：**A08 / B07**（cache 前缀）、**C02 / C03 / C05**（审批与 write-deny）、
**C04**（session 串味，L-007/L-010 回归）、**D02/D03**（渠道目标识别与断线重连）。

---

## 5. 待你裁决的清单

| # | 事项 | 建议 |
|---|---|---|
| A1 | 未登记税 2 条（`35532f29fb57` / `018b4e761ade`） | 补登记；并把 boundary 非绿设为**月度复查点的必处置项** |
| A2 | 闸门报告分区表措辞误导 | 改成「跟随区改动（详见 §2）」 |
| A3 | S2 形态：adapter vs 照搬上游 | **adapter**（§3.2 理由） |
| A4 | plugin / builtin / user 热路同名优先级 | 建议 plugin < builtin < user |
| A5 | S2 排期是否按真缺口重估 | 建议重估：1 条 API + 1 个残留键 + gold 基线 |
| A6 | 是否采纳上游三个便宜护栏（`max_chars` / id 校验 / 重复注册拒绝） | 建议采纳，登记 L-014（Vermes 目前没有片段长度上限） |
| A7 | 回退开关 `VERMES_PROMPT_PROCESSORS_LEGACY` | 建议**先不做**，失败就 git 回滚 + DIVERSION 登记 |

---

## 6. 本轮新增文件一览

```
scripts/upstream_canary.py                 （T2 哨兵脚本）
.github/workflows/upstream-canary.yml      （周跑 lane，只告警）
reports/.upstream-canary-pin.json          （钉点真源 v2026.9.14 → 345cd2b057）
tests/tools/test_upstream_canary.py        （5 passed）
tests/tools/test_ab_corpus.py              （5 passed）
reports/ab-corpus/v1/corpus.yaml           （32 条语料）
reports/ab-corpus/v1/corpus.sha256         （bb0a4369989dbebe…）
docs/plans/2026-09-23-s2-pluginization-workorder.md
reports/qclaw/cross-audit-t2-s2_20260923.md（本文）
```

## 7. 口径纪律

- 本文所有数字均来自当场命令；引用时请带 **HEAD hash + 命令**。
- 写稿期间工作树仍有他人并发改动（`tools/feedback_tool.py`、`web_dist/` 构建产物），
  **我只提交了上表文件**，其余未动。
- 机器产出 `reports/dist-boundary-20260923.md` 是我跑 boundary 时重新生成的，未提交，请你确认是否入库。
