# 工单：Vermes = 独立分叉 vs Hermes 发行版（判定与估算）

- 出具：2026-09-20
- 执行者：外部搭子（无本会话上下文，本文件即全部背景，**不要假设你有额外信息**）
- 性质：**只读调研**。不改代码、不 commit、不 push、不动 `~/.vermes` 与 `~/.hermes` 的运行配置。
- 时间盒：≤ 4 小时，分 3 批交付（每批 ≤1.5h，每批落盘一次，防超时丢结果）
- 落盘路径：`~/Projects/vermes-electron/reports/vermes-distribution-vs-fork_20260920.md`

---

## 0. 一句话任务

判断 Vermes 能否改为「Hermes 上游的一个发行版」——即**引擎跟随上游，Vermes 只保留插件层 + 产品层**；
并对三块核心差异化（记忆织物 / 工作流 / 自进化）逐一给出判定：**可插件化 / 需上游开口 / 只能 core patch**，附改造量估算与人日。

最终产出「走发行版（C）」还是「继续白名单移植（A）」的选择建议 —— **不要预设结论**。

---

## 1. 两个源码根（绝对路径，别搞混）

| 代号 | 路径 | 说明 |
|---|---|---|
| **Vermes** | `~/Projects/vermes-electron` | 中文产品化分叉，当前 `version.txt` = 2.5.0 |
| **上游** | `~/.hermes/hermes-agent` | 官方 Hermes 的本地 git 检出（**本机已存在，不要再去 GitHub 拉源码**），HEAD `5a0c2fb89e` |

> 注意：`~/projects` 是 `~/Projects` 的软链，是同一物理目录；`find ~` 这类全盘搜索在本机会被截断（137），
> **否定性结论禁止只靠 shell `find`/`grep`**，必须换第二种方法交叉验证（如 `ls -1` 全量列举、或按目录逐层 `ls`）。

---

## 2. 已确定事实（已实测，可直接引用；不要重新推导、不要质疑数字）

### 2.1 分歧规模（决定性：这不是"品牌改名"）

- Python 文件：Vermes **2423** / 上游 **6696** / 同名共有仅 **1167**
- **仅 Vermes 有**：**1256 个文件 / 496,707 行**（替换即消失）
  - `tests` 784 个 / 217,004 行 ｜ `vermes_cli` 258 个 / 167,031 行 ｜ `agent` 106 个 / 39,708 行 ｜ `gateway` 45 个 / 51,574 行
- **仅上游有**：5529 个文件 / 1,402,069 行
- 抽查两边**同名**测试文件 400 个 → **397 个内容不同（99%）**
- 同源文件行重合度（Jaccard）：`model_tools.py` 0.11 ｜ `toolsets.py` 0.09 ｜ `utils.py` 0.14 ｜ `agent/context_compressor.py` 0.08

### 2.2 上游官方扩展口（已实测，判定时的"现有口子"清单）

- provider ABC（`~/.hermes/hermes-agent/agent/` 下）：
  `memory_provider.py` → `class MemoryProvider(ABC)`（第 75 行）
  `context_engine.py` → `class ContextEngine(ABC)`（第 47 行）
  另有 `browser_provider.py` / `image_gen_provider.py` / `video_gen_provider.py` / `tts_provider.py` / `transcription_provider.py` / `web_search_provider.py` / `terminal_env_provider.py` / `provider_base.py` / `provider_registry.py`
- 插件类别目录（`~/.hermes/hermes-agent/plugins/`）：
  `browser` `context_engine` `cron_providers` `dashboard_auth` `disk-cleanup` `example-dashboard` `google_meet` `hermes-achievements` `image_gen` `kanban` `memory` `model-providers` `observability` `platforms`
- hooks 扩展点：`gateway/builtin_hooks/`（官方扩展点，故意不预置任何 hook）
- 品牌/UI 层机制：`skills/` `optional-skills/` `skins/` `desktop-plugins/` `tui-widgets/` `pets/`（目录存在，用户侧当前为空）
- 上游纪律原文（`~/.hermes/hermes-agent/AGENTS.md`）：
  - 「The core is a narrow waist; capability lives at the edges.」
  - 「Plugins work within the ABCs/hooks we provide; if one needs more, **widen the generic plugin surface, never special-case it in core**.」
  - 第三方**产品**不得进入核心树，必须以**独立插件仓 + pip entry point** 发布，在 Nous Research Discord `#plugins-skills-and-skins` 推广。

### 2.3 待判定的差异化资产（Vermes 独有，上游无对应物；行数为实测）

```
记忆织物   agent/memory_fabric.py 1377 · memory_reflection.py 1689 · memory_recall.py 958
           memory_aware_executor.py 395 · docmemory_provider.py 490 · memory_migration.py 287 · memory_budget.py 192
自进化     agent/evolution_manager.py 1378 · capability_evolver.py 485 · capability_registry.py 468
           evolution_injector.py 351 · emergence_critic.py 283 · feedback_learning.py 82
工作流     agent/workflow_scheduler.py 500 · workflow_runtime.py 291 · workflow_templates.py 255 · pipeline.py 305
中文平台   gateway/platforms/ 下的 weixin / yuanbao / feishu_comment 相关实现
产品层     vermes_cli/ 258 文件 / 167,031 行（桌面 dashboard、blueprints、Studio、ScholarForge）
打包链     vermes*.spec / vermes-inno-setup.iss / build-macos*.sh / build-windows*.bat
```

### 2.4 其它可直接引用的现状

- 运行时技能：`~/.vermes/skills` 245 个 `SKILL.md`；`~/.hermes/skills` 173 个
- 仓内技能：Vermes 81 / 上游 208（**仅 Vermes 有 3，仅上游有 130**）→ 技能库大部分来自上游
- 模型配置：Vermes = `agnes-3.0-flash @ agnes`；上游默认 profile = `deepseek-flash @ deepseek`；两侧 `reasoning_effort: high`；压缩阈值 Vermes 0.7 / 上游 0.5
- 已知失败台账（**直接引用，不要重跑全量测试**）：`reports/known-failures-gateway-20260920.md`（13 failed / 5820 passed，已用对照实验证明 pre-existing）
- 分歧度量基线：`reports/diverge-baseline-20260920.md`

---

## 3. 判定标准（必须按这个口径，否则结论不可比）

对每项能力给出**唯一判定**：

| 判定 | 判据（三条全满足才是"可插件化"） |
|---|---|
| **可插件化** | ① 上游存在对应 ABC 或插件类别；② 实现**不需要改** core 文件（`run_agent.py`、`agent/turn_*.py`、`agent/conversation_loop.py`、`agent/subagent_*`、非 ABC 的 `agent/*.py`）；③ 能给出上游范例实现可参照（如 `plugins/memory/` 下已有实现） |
| **需上游开口** | 能力必须**在 turn 中间介入**（注入/改写消息流、干预工具选择、在压缩点挂钩），而上游无对应 hook；但**可以抽象成一个通用 hook 面**（不是为本产品特判） |
| **只能 core patch** | 需要改 core 语义（压缩策略、消息角色不变式、提示装配顺序等），无法用 hook 表达 |

> 判"需上游开口"时，**必须写出建议的 hook 面签名**（例：`on_before_tool_round(context, messages) -> Optional[Messages]`）与插入位置（上游文件 + 函数名）。
> 拿不准就写「待核实」，**禁止为了凑结论而编造行号或接口**。

---

## 4. 逐项检查清单（按此顺序做，每项给出 file:line 证据）

### 批 1（约 1.5h）：记忆织物 + 上下文引擎 —— 最可能成立的两块

1. 读上游 `agent/memory_provider.py`：`MemoryProvider(ABC)` 的**全部抽象方法**（名字 + 签名 + docstring 里说明的调用时机），以及 `agent/memory_manager.py` 如何编排多个 provider。
2. 读上游 `plugins/memory/` 下**现有实现**（几个、各是什么形态）作为范例。
3. 读 Vermes `agent/memory_fabric.py` / `memory_recall.py` / `memory_reflection.py` / `memory_budget.py` 的**公开入口**（类名 + 对外方法），逐条映射到上游 ABC：哪些方法有对应、哪些无处安放。
4. 关键问题：`agent/memory_aware_executor.py` 是否需要在 turn 中间介入？如果是 → 归入"需上游开口"并给 hook 签名。
5. 上下文引擎：**两边同名文件** `agent/context_engine.py` → 直接对比 ABC 方法集合（`grep -n "def \|class "` 两侧各一次），列出上游有/Vermes 有/双方都有。

### 批 2（约 1.5h）：工作流 + 自进化 —— 决定成败的一块

6. 上游工作流能力盘点：`plugins/cron_providers/`、`plugins/kanban/`、`tools/delegate_tool.py`、`cron/scheduler*.py` 各自能表达什么（断点续跑 / 重试 / 上下游数据流 / 人工介入）。
7. Vermes `agent/workflow_{runtime,scheduler,templates}.py` + `pipeline.py` 的**能力清单**，逐条对上游现有能力做映射：能承载 / 需扩展 cron_provider / 需 core patch。
8. **自进化（关键路径）**：读 Vermes `agent/evolution_injector.py` 与 `evolution_manager.py`，定位**注入点到底在哪**：
   - 注入发生在 turn 的哪个阶段？（提示装配前 / 工具轮之间 / 回合结束后）
   - 它改写的是什么？（系统提示 / 消息 / 工具集 / 技能库）
   然后去上游对应位置确认是否有 hook。**这一项决定"发行版化"是否成立**，必须给出：注入点上游文件+函数名、建议 hook 面、影响的调用点数量。
9. 若判定"需上游开口"，评估上游接受概率：这个 hook 面是否**通用**（任何插件都能用）？会不会触碰上游 invariant（提示缓存保真、消息角色严格交替）？

### 批 3（约 1h）：中文平台 + 产品层 + 结论

10. 中文平台：上游 `gateway/platforms/ADDING_A_PLATFORM.md` 的适配器约定 vs Vermes 的 weixin / yuanbao / feishu_comment 实现，判定是否为"纯插件移植"（预期：是）。
11. 产品层/打包：确认 `vermes_cli`、Electron 前端、`*.spec`/`*.iss` 是否属于"发行版层"（预期：是，且上游不提供等价物，必须保留）。
12. 产出三张表与结论（格式见 §5）。

---

## 5. 交付物格式（照抄这个结构，不要自由发挥）

```markdown
## 表 1 · 能力判定表
| 能力 | 规模(行) | 上游对应扩展口(文件::符号) | 判定 | 证据(file:line 或符号) | 待核实项 |
|---|---|---|---|---|---|

## 表 2 · 若走发行版（C）
### 2.1 插件包清单
| 插件包 | 落点(plugins/<类别>) | 依赖的上游 ABC | 改造量(人日) | 风险 |
### 2.2 薄核补丁清单（每个 hook 点）
| 注入点(上游 文件::函数) | 建议 hook 签名 | 影响调用点数 | 是否触碰上游 invariant | 上游接受概率(高/中/低+理由) |

## 表 3 · 若走白名单移植（A）
| 剩余移植项 | 单价(人日) | 依据(今天实测的同类项耗时) |

## 结论
- 推荐：A / C / 混合（分层）
- 一次性改造量：__ 人日；持续成本：__ 人日/月
- 若选 C 的**唯一否决条件**是什么（写清：哪一项落不下去就否决）
- 不确定项清单（明确标注「待核实」，不得混入结论）
```

> 注：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md` 是本项目的**唯一真源**路线图（含 errata E1–E11），
> 本工单结论产出后，应把摘要追加进该文档的 errata（E12+），不要另起一份互相矛盾的方案。

---

## 6. 纪律（违反即返工）

1. **只读**：不改任何代码、不 `git commit`、不 `git push`、不改运行配置；发现 `.git/index.lock` 存在时**不要删**，报告即可。
2. **不跑全量测试**（27 分钟、吃额度）：需要测试证据时，引用 `reports/known-failures-gateway-20260920.md`，或只跑单个测试文件。
3. **省额度**：不要索引 `node_modules/`、`dist*/`、`*_internal/`、`*.app/`、`venv/`（合计数 GB）；不要读超大文件全文，先 `grep` 定位符号再按行区间读。
4. **否定性结论必须双方法交叉验证**（例：`ls -1` 全量列举 + Grep 工具各一次）；单靠一次 `find`/`grep` 的"找不到"**不得作为结论**（本项目已有两次此类误判）。
5. **声称"已落盘"必须回读验证**：`ls -la` + `wc -c`，粘贴真实输出。
6. **每个判定必须带证据**（文件路径 + 符号名 + 行号；行号会随迭代漂移，以符号名为准）。
7. **禁止编造**：拿不到本机文件访问权时，只能基于 §2 的既有证据推理，并**必须在结论里显著标注"未做本机取证"**；不得生成未实际读到的行号、接口名或数字。
8. 分批落盘：每批结束就写入 `reports/vermes-distribution-vs-fork_20260920.md`（追加式），不要憋到最后一次性输出。

---

## 7. 验收（外部复核用）

- [ ] 表 1 覆盖 §2.3 的 5 类资产，每行均有上游扩展口证据（文件::符号）
- [ ] 自进化一项给出了明确的注入点（上游 文件+函数）与 hook 面建议，或明确写出"无法表达，须 core patch"及理由
- [ ] 表 2 的每个 hook 点标了影响调用点数与是否触碰上游 invariant（提示缓存保真 / 消息角色交替）
- [ ] 表 3 的单价引用了今天实测的同类项耗时（A1+A2 = 0.5~1 人日含 12 条契约测试）
- [ ] 结论给出唯一否决条件；不确定项单独列出，未混入结论
- [ ] 全文无「接近/大概/可能」式的模糊判定（不确定一律写「待核实」）
