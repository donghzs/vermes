# 参与 Vermes 开发的 Agent 技能索引

> **为什么有这个文件**：每个 agent 的 skill 存在**自己的 root** 里，彼此不可见。
> 2026-09-20 实测事故：WorkBuddy 搜了 `~/.workbuddy/skills` + `~/.vermes/skills` + 仓库内三处，
> 判定 Hermes 的 `hermes-vermes-architecture` skill「不存在」——**实际在 `~/.hermes/skills/`**。
> 同一根因还导致它判错浏览器工具口径（正解就写在该 skill 第 27 行，它看不到）。
>
> **机制**：本文件只登记「路径 + 一句话摘要 + 适用场景」，**不复制正文**。
> 正文唯一真源 = 各 agent 自己的 skill。复制正文 = 两份真相。

---

## 一、Skill Root 一览（实证于 2026-09-20）

| Agent | Root | 项数 | 谁能加载 |
|---|---|---|---|
| WorkBuddy | `~/.workbuddy/skills/` | 12 | WorkBuddy |
| Hermes | `~/.hermes/skills/` | 33 | Hermes |
| Vermes 引擎 | `~/.vermes/skills/` | 76 | Vermes 运行时 |

> 搜「某个 skill 是否存在」时，**三个 root 都要列**（`ls -1`，不要用 `ls | grep`，本会话有假阴性实证）。

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

## 五、登记规则（新增 skill 时请遵守）

1. 新 skill 建在**自己的 root**，正文不复制进仓库。
2. 在本文件加一行：路径 + 一句话摘要 + 适用场景。
3. 跨 agent 需要对方 skill 的内容 → **请对方代读并给摘要**，不要自己复制。
4. 本文件由各 agent 自行追加自己的行；**谁的 skill 谁维护自己的行**。

---

## 六、关联正文（唯一真源）

- 上游对齐路线图：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`
- gateway 已知失败清单：`reports/known-failures-gateway-20260920.md`
