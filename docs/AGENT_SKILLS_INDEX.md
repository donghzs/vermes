# 参与 Vermes 开发的 Agent 技能索引

> **为什么有这个文件**：每个 agent 的 skill 存在**自己的 root** 里，彼此不可见。
> 2026-09-20 实测事故：WorkBuddy 搜了 `~/.workbuddy/skills` + `~/.vermes/skills` + 仓库内三处，
> 判定 Hermes 的 `hermes-vermes-architecture` skill「不存在」——**实际在 `~/.hermes/skills/`**。
> 同一根因还导致它判错浏览器工具口径（正解就写在该 skill 第 27 行，它看不到）。
>
> **机制**：本文件只登记「路径 + 一句话摘要 + 适用场景」，**不复制正文**。
> 正文唯一真源 = 各 agent 自己的 skill。复制正文 = 两份真相。

---

## 一、Skill Root 一览（实证于 2026-09-20；MiMo 同日补登；**QClaw 同日由 Hermes 代登**）

| Agent | Root | 项数 | 谁能加载 |
|---|---|---|---|
| WorkBuddy | `~/.workbuddy/skills/` | 12 | WorkBuddy |
| Hermes | `~/.hermes/skills/` | 33（顶层技能目录口径）／`find -maxdepth 4 -name SKILL.md` 实测 167 | Hermes |
| Vermes 引擎 | `~/.vermes/skills/` | 76（顶层口径）／`find` 实测 237 | Vermes 运行时 |
| **QClaw** | `~/.qclaw/skills/` + 见下方「QClaw root」 | 59 顶层项／`find -maxdepth 4` 实测 **70** | QClaw / OpenClaw |
| **MiMo Desktop** | 见下方「MiMo 多 root」 | 见下方 | MiMoCode / MiMo Desktop |

> 口径提醒：本表「项数」混用两种口径（顶层目录数 vs `find` 计数），读之前先看列里的限定语；
> 要精确数字一律用 `find <root> -maxdepth 4 -name SKILL.md | wc -l` 自己数，不要跨 agent 比大小。

### MiMo Desktop 多 root（2026-09-20 实测，`find <root> -name SKILL.md`）

| Root | 用途 | SKILL.md 实测 | 备注 |
|---|---|---|---|
| `~/.config/mimocode/skills/` | 全局自装技能（MiMoCode 扫描） | 1 | 当前仅 `mimo-browser-use` + 指针 skill |
| `<project>/.mimocode/skills/` | 项目技能 | 0（本仓） | 建议 Vermes 专属工作流 skill 放这里 |
| `~/.local/share/mimocode/builtin_skills/<hash>/skills/` | 引擎内置（docx/xlsx/research 等） | 数十（含 workflows 子级） | 与 Vermes 开发弱相关 |
| `…/Xiaomi MiMo/engine-config/skills/` | 桌面引擎配置技能 | 8 | `mimo-skill-authoring` / `mimo-browser-use` 等 |

> MiMo 写路径约定（与 Hermes/WorkBuddy root 不同）：
> - 全局：`~/.config/mimocode/skills/<skill-id>/SKILL.md`
> - 项目：`/Users/dongzusheng/Projects/vermes-electron/.mimocode/skills/<skill-id>/SKILL.md`
> - **不要**写到 `~/.claude/skills` / `~/.opencode` / `~/.agents` / `~/.codex`（MiMo Desktop 不扫描）。
> - 搜 skill 时除上表外，勿漏 `~/.hermes/skills/` 与 `~/.workbuddy/skills/`。

> 搜「某个 skill 是否存在」时，**所有 root 都要列**（`ls -1` / `find … -name SKILL.md`，不要用 `ls | grep`，本会话有假阴性实证）。

---

## 二、WorkBuddy Root（`~/.workbuddy/skills/`）

| Skill | 一句话 | 什么时候该先读它 |
|---|---|---|
| `vermes-upstream-catchup` | 上游对齐工作流：取证纪律 / 核实他人报告 / 对照实验 / 环境坑 / 并发写协议 | 对比上游、核实别人的方案、多 agent 同场 |
| `vermes-route-audit` | 审计「已落地」声称 + R5 反向验证闭环 | 有人交了一份"X 已完成"的交付报告 |
| `vermes-dmg-build-verify` | DMG 构建 + 验真（新后端代码真进包，而非 datas 影子） | 要重打包 / 验证打包 |
| `vermes-llm-agnostic-principle` | 「无论接入何种 LLM 都发挥到极致」：能力感知路由，永不硬编码模型名 | 涉及模型选择 / 工具路由 / 新增 provider |
| `vermes-scholarforge-add-tool` | 给 scholarforge 加新工具：先查已有资产、纯函数核心、变异测试证明断言会红 | 论文写作模块加/修工具 |
| `vermes-ui-onboarding-audit` | 「点了没反应 / 不会用」：Playwright 驱动**真实构建产物**量化排查 | 页面上手体验问题 |
| `audit-completion-report` | 审计他人「已完成/已修复」报告（不建立在截断输出上） | 通用审计，非 Vermes 专属 |
| `agent-handoff-report` | 让零上下文的新 agent 接手在途任务的接力报告格式 | 换 agent 继续 |
| `mock-assertion-hardening` | 抓恒真/弱断言（哨兵值 + 变异测试） | 写/审含 mock 的测试 |
| `silent-design-decision` | 「A 处宽松 B 处严格」这类看不出是否有意的差异如何处置 | 代码审查中遇口径不一致 |

---

## 三、Hermes Root（`~/.hermes/skills/`）—— WorkBuddy **看不到**，按需请 Hermes 代读

| Skill | 一句话 | 备注 |
|---|---|---|
| `hermes-vermes-architecture` | Hermes vs Vermes 架构对比知识库：量化对比方法（5 组只读命令）、双独有清单、行为级分歧定位法、迁移策略 | **Scripts 下已有 `diverge_metrics.py`（对应路线图 A4）与 `feishu_chat_timeline.py`（对应 E6，平台侧提示计数）—— 建同类工具前先查这里** |
| `hermes-desktop-plugins` | Hermes 桌面插件相关 | 未深读 |
| `software-development` / `software-engineering` / `devops` / `github` | 通用工程类 | 未深读 |

> 其余 28 项未登记（apple / creative / gaming / weather…），与 Vermes 开发无关。

---

## 四、Vermes 引擎 Root（`~/.vermes/skills/`，76 项）

与开发/审计相关的已知项：`audit-verification-workflow`、`delegate-task`、`agent-studio-template-engine`、
`computer-use`、`external-api-testing`、`common`、`daily`、`dogfood`。
**其余未枚举** —— 需要时 `ls -1 ~/.vermes/skills/` 自取。

---

## 四-bis、MiMo Desktop Root

### 全局 `~/.config/mimocode/skills/`

| Skill | 一句话 | 什么时候该先读它 |
|---|---|---|
| `vermes-dev-index` | **指针 skill**：登记本仓路线图 / 已知失败清单 / 跨 agent 技能索引的绝对路径 | 任何 MiMo 会话接手 Vermes 开发前 |

> MiMo 目前**没有**独立的 Vermes 架构/审计 skill（与 WorkBuddy/Hermes 互补关系：
> 工作流纪律读 WorkBuddy `vermes-upstream-catchup`；架构对比读 Hermes `hermes-vermes-architecture`）。
> 若 MiMo 后续沉淀 Vermes 专项 skill，**建在上表 root，并在本文件追加一行**。

### 引擎内置 / 桌面配置（与 Vermes 源码弱相关，仅列防盲区）

`engine-config/skills/`：`mimo-skill-authoring`、`mimo-browser-use`、`mimo-desktop-guide`、
`session-chat`、`visualizer`、`imagegen`、`figma`、`3d-creation`、`threejs-game-skills`。

---

## 四-ter、QClaw Root（`~/.qclaw/`，2026-09-20 由 Hermes 代登，**请 QClaw 自行更正/补全本段**）

| Root | 说明 | SKILL.md 实测 |
|---|---|---|
| `~/.qclaw/skills/` | 主 root（59 个顶层项） | — |
| `~/.qclaw/workspace/skills/` | 工作区 root | — |
| `~/.qclaw/workspace-<id>/skills/` | 会话/项目工作区 root（实测到 `workspace-rpqpolb2p4jtebn1`） | — |
| 合计 | `find ~/.qclaw -maxdepth 4 -name SKILL.md` | **70** |

已实测存在的条目（仅列路径，不下能力判断）：`vermes-build`（Vermes 构建相关）、`lark-setup`、
`baidu-ai-map`、`tencentmap-webservice-skill`、`weiyun`、`bdpan-storage`、`kdocs`、`another_them`、
`maomao-weather`、`competitorsmart`。**其余未枚举** —— 需要时 `ls -1 ~/.qclaw/skills/` 自取。

> 登记缘由：QClaw 是本仓早期建造者、且持有 `vermes-build` 构建能力（Windows 打包链相关），
> 但在本轮多 agent 协作面（本索引 + `TASK_BOARD_20260920.md`）里**此前零留痕** —— 属盲区，故补登。

---

## 五、登记规则（新增 skill 时请遵守）

1. 新 skill 建在**自己的 root**，正文不复制进仓库。
2. 在本文件加一行：路径 + 一句话摘要 + 适用场景。
3. 跨 agent 需要对方 skill 的内容 → **请对方代读并给摘要**，不要自己复制。
4. 本文件由各 agent 自行追加自己的行；**谁的 skill 谁维护自己的行**。
5. 跨引擎文档（路线图 / 失败清单）**唯一真源在本仓 `reports/`**；各 skill root 只放指针，不复制正文（E10）。

---

## 六、关联正文（唯一真源）

- 上游对齐路线图：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`
- gateway 已知失败清单：`reports/known-failures-gateway-20260920.md`
- **Vermes 发行版化评估工单（已移交外部搭子，勿重复做）**：`reports/vermes-ecosystem-assessment-TASK_20260920.md`
- 并行工单板：`docs/TASK_BOARD_20260920.md`
- 本索引：`docs/AGENT_SKILLS_INDEX.md`
