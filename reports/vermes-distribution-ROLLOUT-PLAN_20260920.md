# 全面报告：Vermes 发行版化路线（含"冻结当前状态"基线方案）

> 报告日期：2026-09-20 · 作者：Hermes（本机）
> 结论依据：本机实测 + 仓库取证（行号/文件均可复验，见 §6 证据索引）
> 配套交付物：`scripts/check_coexistence.py`（497 行）· `docs/SPEC_coexistence_invariants_20260920.md`（127 行）
> 前置文档：`reports/vermes-distribution-vs-fork_20260920.md`（rev2，判定报告）

---

## 0. 执行摘要

**决定链**：先冻结一版"当前状态"的 Vermes → 再做全面发行版化。**这个顺序是对的**，理由见 §1。

三条结论：

1. **并存当前完好，且现在可验证**：本机 5 个引擎安装同时运行，12 项不变量实测 **FAIL 0 / WARN 2**。已把"能不能并存"从感觉变成脚本 + 退出码（可作发布门禁）。
2. **发行版化技术上有路径**（rev2 已修正我审核出的 3 处硬错误）：自进化/记忆织物/上下文引擎 = **可插件化**；工作流 = 需上游开口；产品层 = 发行版自有。**初版"独立分叉 A"的决定性理由不成立**。
3. **冻结版不只是备份，它是后续每一刀的 A/B 对照基准**——没有它，每个阶段的验收只能靠感觉，而本项目已经因"感觉式结论"误判过两次（E9/E10）。

---

## 1. 为什么"先冻结一版"是正确决策

| 理由 | 说明 | 反面代价（若跳过冻结） |
|---|---|---|
| **可回退** | 发行版化要动注入载体、记忆写入路径、渠道装配；某阶段若发现不可逆（如召回质量下降），必须有"已知可用"的参照系 | 改造失败时手上只剩一个半成品 |
| **可对照（A/B）** | 每阶段验收需要"同一批任务在冻结版 vs 新版"的对比数据（尤其 P3 记忆召回、P4 渠道） | 验收退化为主观判断 → 重演 E9/E10 式误判 |
| **可交付** | 用户手上始终要有一个装得上、用得起的版本 | 唯一可用版本赌在改造期（周期 5–8 周） |

---

## 2. 冻结基线：精确定义"当前状态"

### 2.1 代码基线（⚠️ 必须先归位才能冻结）

| 项 | 实测 |
|---|---|
| HEAD | `ad58268ead`（build: web_dist for M7 threshold settings copy） |
| 与远端差距 | **ahead origin 59**（未 push） |
| 最新 git tag | **v2.4.9**（tags: v2.4.9/8/7/6/5） |
| 工作树脏项 | `M agent/memory_provider.py`、`M docs/AGENT_SKILLS_INDEX.md`、`M reports/qclaw/p2-coding-context-qclaw-终局立场_20260920.md` |
| 未跟踪 | 13 项（`docs/SPEC_coexistence_invariants_20260920.md`、`docs/AUDIT_SKILLS_INDEX_BY_QCLAW_20260920.md`、`docs/QCLAW_POINTER_SKILL_TO_PASTE.md`、`reports/p2-coding-context-execution-plan_20260920.md`、`reports/skill-routing-decision-brief_20260920.md`、`reports/qclaw/*`(5)、根目录 `SKILLS_INDEX_OPTIMIZATION_SPEC_2026-09-20.md`、`.dumate/`） |

**冻结前必须做的四件事**：
1. 把在途改动**提交或明确丢弃**（含他人文件 —— 归属人确认后再动，不碰他人未提交内容）
2. 统一版本号（见 2.2）
3. **commit + tag**，然后**从 tag 状态构建**——不得从脏工作树构建，否则"保留当前状态"不可复现
4. push（含 tag），使冻结版有可追溯的远端坐标

### 2.2 版本号（冻结版的命名）

| 真源 | 值 |
|---|---|
| `version.txt` | **2.5.0** |
| `frontend/package.json` | **2.5.0** |
| `electron/package.json` | **2.5.0** |
| `pyproject.toml` | **2.5.0** |
| 最新 git tag | **v2.4.9** ← **落后于版本文件** |

→ 建议：冻结版定为 **v2.5.0** 并补打 tag（与四处真源一致）。工单板 §6 残留的 2.4.9 笔误一并清掉（§3/§5 已订正）。

### 2.3 能力清单（冻结版包含什么 —— 也是后续被改造对象）

| 差异化能力 | 实现 | 规模（实测行数/文件数） |
|---|---|---|
| 自进化 | `agent/capability_evolver.py` | 485 行 |
| 记忆织物 | `agent/memory_fabric.py` | 1,377 行 |
| 工作流 | `agent/workflow_runtime.py`（+ scheduler/pipeline） | 291 行 |
| 上下文压缩调度 | `agent/compression_scheduler.py` | 656 行 |
| 中文/多平台 | `gateway/platforms/` | 40 个文件（17 平台） |
| 技能库 | `~/.vermes/skills/` | 245 个 SKILL.md |
| 打包链 | `scripts/build-macos*.sh` / `build-windows.bat` / `build-windows-installer.py` / `build-win-ci.ps1` | 双平台 |

### 2.4 已知缺陷台账（带病发布 → 必须写进 release notes）

| # | 缺陷 | 状态 | 来源 |
|---|---|---|---|
| 1 | gateway 全量测试 **13 failed / 5820 passed / 13 skipped** | 已定性为 pre-existing（环境缺依赖 3 / 品牌重命名遗留 2 / 中文化遗留 1 / 工具集漂移 1 / reconnect·email·runner 7） | 引自 workbuddy 报告；**按纪律未复跑全量**，定性依据=失败原因+零引用 |
| 2 | SQLite 3.50.4 WAL-reset 损坏 bug（内嵌运行时） | 未修；`~/.hermes/state.db` 现 **532 MB** 仍在 WAL 模式（备份已就绪） | 本机实测（Hermes 侧；Vermes 侧需独立确认） |
| 3 | W2：`failed_platforms` entry 整体替换（`watcher_mixin.py:235` 持旧引用） | 未修 | 引自 known-failures 台账 |
| 4 | `ai.hermes.gateway` `last_exit=75`（异常退出后被 launchd 拉起） | 观察中 | 本次并存实测顺带发现（属 gateway-reliability） |

### 2.5 运行态基线（并存实测，冻结版发布前重跑对照）

```
python3 scripts/check_coexistence.py --deep
→ FAIL 0 / WARN 2 / 共 12 项，exit=0
```

| 项 | 实测 |
|---|---|
| 运行中安装 | 5：hermes / vermes / qclaw / workbuddy / mimo |
| 配置根 | `~/.hermes` · `~/.vermes` · `~/.qclaw` · `~/.workbuddy`（互不嵌套） |
| 监听端口 | 46 个，无冲突（Vermes :9119 dashboard / :9120 gateway） |
| 技能根 | 173 / 245 / 69 / 12 个 SKILL.md |
| launchd | 9 个相关服务，命名空间不重叠 |
| 代码根 | Hermes 引擎 `~/.hermes/hermes-agent` · Vermes 源码 `~/Projects/vermes-electron` · Vermes 装机后端 `/Applications/Vermes.app/Contents/Resources/backend` |
| 跨安装文件句柄 | **无**（真隔离） |
| WARN | 3 处跨安装二进制托管：**Hermes 的 MCP 跑在 QClaw 自带 Python 上**（2 进程，运行态真耦合）· QClaw 的 node 跑 Vermes 前端 dev server（开发态，可接受） |

---

## 3. 发行版化路线（分阶段 · 可停 · 每阶段可回退）

| 阶段 | 内容 | 改动面 | 工期 | 验收（硬） | 回退 | 上游依赖 |
|---|---|---|---|---|---|---|
| **P0 冻结** | 出当前状态一版（v2.5.0）：DMG + Windows NSIS | 无代码改动 | 天级 | ① 从 tag 构建 ② 并存脚本 FAIL 0 ③ release notes 含 §2.4 缺陷 | — | 无 |
| **P1 自进化插件化** | 静态块→`register_system_prompt_section`；动态块→`pre_llm_call`；工具后→`post_tool_call` | 改注入载体 + 新增 `plugins/` | 1–2 周 | ① 注入文本与冻结版**逐字相同** ② `git diff --stat` 仅 `plugins/` ③ 并存脚本 FAIL 0 ④ **记录一次上游升级的 core diff 行数/人工小时** | `xxx_backend: legacy\|plugin` 开关 | **不需要**（三个 hook 已存在） |
| **P2 上下文引擎** | 跟随上游 or 写成插件；压缩阈值是否保留 Vermes 0.7 需拍板 | 小 | 2–3 天 | 长会话压缩时机与冻结版对比（阈值不变则零感知） | 配置回退 | 无 |
| **P3 记忆织物→MemoryProvider** | 实现 4 个 `@abstractmethod` + 迁移 15 个内部 API；`conversation_loop.py` 2 处硬编码改走 `MemoryManager` | 中 | 2–3 周 | **先做召回 A/B**：同一批问题，冻结版 vs 新版召回内容并排；变差即停 | 双后端开关 | 无（有 hindsight/honcho 范例） |
| **P4 中文平台→plugins/platforms** | 40 个文件迁插件路径，补齐 `connect(is_reconnect=)` + plugin.yaml | 中 | 2–3 周 | **一平台一验收**；每平台必测"断线重连不丢消息"；平台插件不得改写 `os.environ`（上游纪律 `plugins/AGENTS.md:70-71`） | 逐平台开关 | 无 |
| **P5 形态 B（可选）** | 引擎作为依赖安装，Vermes 仓库只留 plugins+品牌+打包 | 大（重做打包链） | 视情况 | 追上游 = 版本号 +1 | 保留 P0/P1 形态 | 无 |

**贯穿全程的硬规则**：
1. **core diff = 0 闸门**：`git diff --stat` 只允许出现在 `plugins/` 下（计划内薄核补丁除外并登记）
2. **每阶段跑并存脚本**：FAIL 即回退（`docs/SPEC_coexistence_invariants_20260920.md` §6 已把每阶段映射到需复验的不变量）
3. **上游 canary CI**：定时拉上游 main → 跑插件契约测试 + 关键 E2E → 红了告警（把"契约税"变可控；现在还没有）
4. **行为契约测试**：防"接口没变但行为变了"的静默漂移（唯一分叉模式占优的地方）

---

## 4. 风险登记册

| # | 风险 | 概率 | 影响 | **用户可见度** | 对策 |
|---|---|---|---|---|---|
| 1 | P3 召回质量下降（分层 L0–L4 压成单个 prefetch 字符串） | 中 | 高 | 中（"它怎么不记得了"） | 先 A/B 召回内容再迁；双后端开关 |
| 2 | P4 渠道掉线 / **断线期间消息丢弃**（漏 `is_reconnect=`） | 中 | 高 | **高**（用户直接不能用） | 一平台一验收；断线重连专项测试；排最后做 |
| 3 | 平台插件改写 `os.environ` → 密钥/白名单串 profile | 中 | 高 | 低但难查 | 遵守上游纪律（YAML→`PlatformConfig.extra`，门控→`platform_gate_env`）|
| 4 | 行为漂移（接口未变、行为变，无编译错误） | 中 | 中 | 低-中 | 行为契约测试 + canary CI |
| 5 | 失去"版本多样性保险"（同一 bug 双命中两端） | 高 | 中 | 中 | 上游能修就能 pull；canary 提前暴露 |
| 6 | P1 注入位置改变导致 agent 姿态变化（system prompt→user message 权重更高） | 中 | 中 | 中 | 注入文本逐字等价 + 真实会话 A/B |
| 7 | 打包链适配（P5） | 中 | 中 | 无（发布前） | P5 可选、延后 |
| 8 | 技能索引/路由改动误伤 IM（**已在发生**） | — | 中 | 中 | 见 §5 的 D3：M7 阈值 20 KB vs 实测索引快照 **131,834 字节**（同日更早一次测量为 98,198）→ auto 恒触发（死开关）；A′ 渠道硬门在 auto 路径被摘 |

---

## 5. 待拍板决策清单

| # | 决策 | 现状/依据 | 建议 |
|---|---|---|---|
| D1 | 冻结版版本号 = **v2.5.0**？ | 四处真源 2.5.0，最新 tag 却 v2.4.9 | 定 v2.5.0 + 补 tag + push |
| D2 | 在途改动如何处理 | 3 个 M + 13 未跟踪（含他人文件、qclaw 审计报告） | 归属人确认后提交；`reports/` 与 `docs/` 一并入库，保证冻结版文档可追溯 |
| D3 | M7 的 auto 死开关 + A′ 渠道门（**冻结前修还是冻结后修**） | 阈值 20,000 字节 vs 实测索引快照 **131,834 字节**（另一次测量 98,198）→ 切 auto 即永远触发；`resolve_compact_skill_categories` 的 auto 路径已不走 platform 白名单 → IM 也 names-only | **冻结前修**（半小时级）：阈值改比例（索引÷模型窗口）；渠道门拆成独立开关，默认 IM 不降级 |
| D4 | P2 是否保留 Vermes 压缩阈值 0.7 | 上游默认 0.5 | 保留（零感知），只换实现 |
| D5 | P4 先试点哪 1–2 个平台 | 飞书已有评论/注解增强 | 先飞书 + 一个轻量平台 |
| D6 | 是否现在建上游 canary CI | 无 | 建（成本一个 yml，收益是"契约税可控"）|
| D7 | 2 处跨安装托管是否现在修 | Hermes MCP 跑在 QClaw Python 上 | 修：把 MCP 解释器钉到 Hermes 自己的 venv（并在该 venv 装依赖） |

---

## 6. 证据索引

| 结论 | 证据 |
|---|---|
| 并存 12 项 FAIL 0 | `scripts/check_coexistence.py --deep` 实跑（本机 2026-09-20） |
| 9 条不变量 + 3 条禁令 + 每阶段复验映射 | `docs/SPEC_coexistence_invariants_20260920.md` |
| 发行版化判定（可插件化/需上游开口） | `reports/vermes-distribution-vs-fork_20260920.md` rev2（§0.1 hooks: `plugins.py:109/111/940`、`system_prompt.py:125`、`tool_executor.py:278`、`turn_context.py:1010` 均已复核命中） |
| 上游平台插件纪律原文 | `plugins/AGENTS.md:70-71`（Platform plugins never mutate `os.environ`…） |
| 代码/tag/版本状态 | `git log` HEAD `ad58268ead` · `git status` · `git tag`（v2.4.9）· `version.txt`/`frontend|electron/package.json`/`pyproject.toml`=2.5.0 |
| 13 条 pre-existing 测试失败 | 引自 workbuddy gateway 全量结果（未复跑全量，按纪律） |
| W2 / 版本号矛盾台账 | `docs/TASK_BOARD_20260920.md`、`reports/known-failures-gateway-20260920.md` |
| 技能索引体量（**131,834 字节**，另一次测量 98,198） | `~/.vermes/.skills_prompt_snapshot.json` 实测 |
| 规模对比（1167 同名文件 / 49.7 万行自有 / Jaccard 0.08–0.14） | 本机两仓库实测（见 FINAL 路线图 §规模） |

---

## 附：本报告未做的事（口径声明）

- 未跑全量测试（27 分钟，吃额度）——按项目纪律
- 未复跑 gateway 全量以复核 13 条失败——引用现成台账并标注来源
- 未改任何代码/配置/进程；本报告与两份交付物均为只读产出 + 新增文件
- 未验证 Vermes 侧 SQLite 内嵌运行时版本（§2.4 第 2 项标注"需独立确认"）
