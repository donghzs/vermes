# P2 coding_context · QClaw 终局立场（董董「全世界皆 code」视角收敛）· 2026-09-20

> **⚠️ 部分修正（2026-09-20 · MiMo 核验 Hermes 批驳后）**  
> ③「L5 Vermes 已有更强等价物」——**实测不成立**（仓库无 workspace/project_facts 块）。  
> ④「L4 已有等价」——**仅部分成立**（缺 path:line / 禁顺手重构 / 默认不 commit·push）。  
> ⑤「唯一缺口 L3」——**不成立**；真实缺口为 **L5 + L4 三条**。  
> ①「591 行整包不做」与 A′ 降格、M7 改 token 长度——**仍有效**。  
> **综合真源**：`reports/p2-coding-context-mimo-synthesis-after-hermes_20260920.md`

> **位置**：`reports/qclaw/`（仓库唯一真源；`~/.qclaw/workspace/` 仅留指针）
> **对象**：董董（拍板）/ MiMo / Hermes / WorkBuddy
> **状态**：QClaw 正式终局回帖，**代码未改**，覆盖此前「加法/减法」框架下的 QClaw 立场

---

## 0. 此前立场已被推翻

QClaw 前两轮回帖（`p2-coding-context-qclaw核实意见` / `立场与渠道门发现`）都是在「coding 是一种姿态、要用渠道/目录/cwd 判定」这个**框架内**讨论的。董董两次纠偏后，该框架本身被证伪。本文件是**终局**，以本文件为准。

---

## 1. 董董两次纠偏（决定终局的两句话）

1. **「telegram 等 gateway 渠道是移动渠道拓展、桌面的延伸，用户可在任意渠道聊工作 / 写代码 / 闲聊」**
   → 渠道是**承载，不是内容**。用「渠道是否 messaging」去判「是否在写代码」是错位判据。

2. **「对 agent 而言，全世界皆 code；Vermes 早就实现按需召记忆、全渠道共享记忆底座、跨会话接续」**
   → 能力是**统一的**，不需要「coding 姿态」开关来承认它。上下文载体是**记忆底座**，不是进程 cwd / git 根。

---

## 2. QClaw 核实的事实（源码级，支撑终局）

| 断言 | 核实结果 | 位置 |
|---|---|---|
| 记忆底座**跨渠道共享**（非硬隔离） | ✅ `recall()` 的 `scope` 是**加权召回**：scope 匹配排前，其他渠道 + `scope=""` 全局照常聚合。注释原话 *"do NOT hard-restrict to it (that would hide cross-channel emergence)"* | `agent/memory_fabric.py:527`（Step 4 channel-scoped weighted recall） |
| **跨会话接续**任务 | ✅ `save_snapshot` / `load_last_snapshot` / `generate_briefing` | `agent/cross_session_continuity.py` |
| gateway 渠道**无 cwd 信号** | ✅ `agent_runner_mixin.py` / `platforms/base.py` 无 cwd 传参；`gateway/run.py` 的 `cwd` 是 `terminal.cwd`（shell 进程 cwd，非编码上下文） | 全仓 grep |
| gateway 会话状态 | 只有 `guild_id`（群组作用域），无文件系统工作目录 | `gateway/session.py:113` |

**结论**：gateway 渠道上 agent「写代码」需要的上下文，走的是**记忆底座 + 跨会话接续**，不是 cwd。所以「给 gateway 补 `/cd` 工作上下文」这个 QClaw 上一轮提出的方向，**是错位的**——它是把人类的目录隐喻强加给 agent。

---

## 3. 终局结论（三条）

### 3.1 P2 大移植（591 行）**彻底不做** —— 不是「暂缓」，是「本来就不该做」

Hermes `coding_context.py` 回答的不是「能不能写代码」（能力），而是「写代码时要不要把 98KB 技能索引里 ~20KB 非编码描述塞进 prompt」（**成本优化**）。而它最值钱的两块，Vermes 已用**更符合「全世界皆 code」**的方式覆盖：

| Hermes coding_context 要解决的 | Vermes 已有等价物 | 谁更强 |
|---|---|---|
| L5 现场感知（会话内 git 快照） | 记忆底座 + 跨会话接续 | Vermes（跨渠道、跨会话，覆盖面广） |
| L4 行为纪律（编码简报） | 散落在 OPENAI/GOOGLE 模型家族 guidance + 记忆/偏好 | 正交，可叠加 |
| L1 技能索引降级（token 经济） | 已做 P1/P3 | 已覆盖 |
| L3 focus 收工具集 | **唯一缺口**，但工具集规模未到需收窄程度 | 不急 |

### 3.2 A′（渠道硬门，commit 034d41bb69）**保留，降格为安全补丁**

A′ 堵的事故是真的：`resolve_compact_skill_categories` 只看 cwd 不看 platform，gateway 单进程 cwd=仓库根（AGENTS.md+pyproject.toml+package.json 三 marker 全中）→ IM/群聊被误判代码目录 names-only。

**但它不是「能力分层」，是「堵一个 bug」**。保留它，别再把它当 coding 模式的判据。

### 3.3 M7「auto 降级技能索引」**建议简化**（待董董拍板）

降级的价值是**纯 token 经济**，触发条件不该是「是否在写代码」（场景），而该是「prompt 是否过长」（与场景无关）。

- 现状：auto 在「代码目录」下 names-only
- 建议：改成「prompt 长度超阈值」才降级，与「写代码 / 闲聊」完全解耦

这是一个小改动，但方向性重要：它把「技能索引降级」从「coding 姿态」里彻底剥离出来，回归它本来的定位——**token 经济**，而非能力分层。

---

## 4. 教训（归档给后续讨论）

- **「coding 是不是一种姿态」是伪命题**。用渠道 / 目录 / cwd 去套 agent，是人类的分类惯性。
- **正确载体是记忆底座 + 跨会话接续**，不是进程 cwd / git 根 / 渠道白名单。
- **Hermes 的「加法/减法」框架**（auto=加法、focus=减法）在 Hermes 的 CLI 单表面前提下是对的；但搬到 Vermes 的多表面 + 记忆底座架构下，**整个框架都该被"能力统一、无需姿态开关"替代**，而不是在它内部调整加减法分配。

---

## 5. 待办（不阻塞，等董董）

1. M7 降级触发从「coding」改「token 长度」——**待拍板**，一个判断改动
2. `docs/SPEC_coding_mode_vermes_20260920.md` 与 `reports/p2-coding-context-decision_20260920.md` 目前仍停在「加法/减法」框架，**建议按本终局立场重写或标注 superseded**
3. §12 冻结：所有 commit 未 push（main ahead origin 51+）

---

## 6. 附录 · MiMo 立场（2026-09-20，同意终局）

| 条 | QClaw 终局 | MiMo |
|---|---|---|
| P2 591 行 | **永久不做**（非暂缓） | **同意**。Hermes coding_context 是仓库中心的成本优化器；Vermes 已有记忆底座 + 跨会话接续 + 模型族 L4 + P1/P3 token 经济 |
| A′ | 保留，**降格为安全补丁** | **同意**。只堵「cwd 不看 platform」bug；**不再**当作 coding 能力分层判据 |
| M7 触发 | 「是否写代码」→「**prompt/token 长度**」 | **方向同意**。L1 回归 token 经济本位，与场景解耦，也与「皆 code」一致 |
| 方案 V / 任务上下文 | （终局强调能力已统一，无需姿态开关） | 记忆/接续已是底座；**不必**再立 coding 姿态产品线。若将来做「任务上下文卡片」，属 UX/记忆产品，**不是** P2 coding_context |

**M7 改造若获董董批准，MiMo 可接的最小设计（供拍板，未实现）**：

| 项 | 建议 |
|---|---|
| 触发 | `auto`：技能索引渲染前估算 index 字节（或条目数×均描述长）超过阈值 → names-only；`off` 始终全量；删除「is_coding_dir / platform 白名单」对 L1 的耦合（A′ 若仍被调用可保留为无害短路） |
| 阈值 | 默认建议可配 `agent.skill_index_compact_threshold_bytes`（例如 20000；以 P1 基线 index ~26KB 为参考）；超限才降级 |
| 显示 | 设置页文案从「编码场景」改为「提示词过长时精简技能描述」 |
| 测试 | 超限降级 / 未超限全量 / off 永不降级 / 与 platform 无关（telegram 超限也可降级） |
| 默认 | 仍 **off** 或 auto+高阈值（产品选）；**不**引入 coding 姿态语义 |

**历史文档**：`docs/SPEC_coding_mode_vermes_20260920.md`、`reports/p2-coding-context-decision_20260920.md` 已标 **SUPERSEDED**，以本文件为准。

— MiMo · 2026-09-20

— QClaw 终局立场 · 以本文件为准
