# Vermes「编码模式」产品规格（一页）· 2026-09-20

> **状态**：待董董拍板的产品规格；**不是** coding_context 591 行移植说明  
> **依据**：QClaw 核实意见 + MiMo P2 讨论稿 + P1/P3/M7/A′ 已落地代码  
> **一句话**：**能力分层对齐上游思路；默认值 Vermes 自定（off）；auto 只开低风险层；完整编码姿态 = 用户手动 on。**

---

## 1. 决策表（请董董勾选）

| 模式 | 系统提示词 | 技能索引 | 渠道 | 工具集 | 何时生效 |
|---|---|---|---|---|---|
| **off**（建议默认） | 无编码简报 | 全量描述 | — | 平台默认 | 出厂 / 用户关闭 |
| **auto**（建议） | **无** L4 简报 | L1 names-only | **L2 硬门后** 仅交互式平台 | 不变 | 交互式平台 ∧ 代码目录 ∧ 非 messaging |
| **on**（手动「编码模式」） | **注入** L4 场景简报（+可选 L5 Workspace） | L1 names-only | 仅 cli/web/desktop 等白名单；IM 不自动 | **首版不变**（L3 暂缓） | 用户显式打开（会话或全局） |

**默认值建议**：`off`。  
**auto 能力包**：仅 **L1 + L2**（技能降级 + 渠道门）。  
**on 能力包**：**L1 + L2 + L4**（+ L5 若做桌面卡片则与 on 联动）。  
**L3 工具集收窄**：**不进首版**；仅未来手动 focus 且产品明确要求时。

> 「像不像 Hermes」：**能力上能像（on），默认不抄（off）**。上游默认 auto 且 auto 就注入简报 —— 那是 CLI 单表面产品前提；Vermes 多表面 + gateway 单进程，照抄默认值即重演 A′ 前的误伤。

---

## 2. 模式语义（实现合同）

### 2.1 配置键

| 键 | 值 | 说明 |
|---|---|---|
| `agent.compact_skill_categories` | `off` \| `auto` \| `on` | **单一总开关**（M7 迁移见 §4） |
| （可选后续）`agent.coding_mode` | 同上 | 若需要与技能降级解耦，再拆；**首版不拆**，避免双口径 |

**on 语义**：等价「用户声明在写代码」→ 不依赖 cwd；但仍受 **L2 平台白名单** 约束（IM 上即使全局 on，也 **不**自动给 telegram 会话塞工程师简报，除非产品将来做「per-chat 手动 on」——首版不做）。

### 2.2 判定伪代码（auto）

```text
function should_demote_skills(platform, cwd, config):
    mode = config.agent.compact_skill_categories or "off"
    if mode == "off": return None
    if mode == "on":
        return platform in INTERACTIVE_WHITELIST ? DENY_LIST : None
    # auto
    if platform not in INTERACTIVE_WHITELIST: return None   # A′ 白名单，默认拒绝
    if not is_coding_dir(cwd): return None
    return DENY_LIST

function should_inject_coding_brief(platform, config, session_coding_flag):
    mode = config.agent.compact_skill_categories or "off"
    if platform not in INTERACTIVE_WHITELIST: return False
    if session_coding_flag: return True          # 会话内手动开
    if mode == "on": return True                 # 全局手动 on
    return False                                 # auto / off 一律不注入 L4
```

`INTERACTIVE_WHITELIST` = 已落地 A′：`cli, web, desktop, tui, acp, local, api, api_server`  
**messaging / 未知 / 空 platform → 永远 False/None**（QClaw：空 platform 在 Vermes 按拒绝处理，严于上游）。

### 2.3 L4 简报内容边界（on 才注入）

薄移植上游 `CODING_AGENT_GUIDANCE` 场景段，**可裁剪 Vermes 化**：

| 必含 | 可选 / Vermes 化 |
|---|---|
| 先读后改；禁止臆造文件/API | 服从仓内 AGENTS.md / CLAUDE.md |
| 用工具改代码，不靠聊天贴代码代替修改 | 遵守现有 `OPENAI_MODEL_EXECUTION_GUIDANCE` 等 **模型族 L4**（**叠加不替换**） |
| 改完跑测试/构建再宣称完成 | 神魔堂/多 Agent：peer 结果仍要可验证 |
| `path:line` 引用；不顺手重构 | 不碰密钥；默认不 commit/push |
| patch 连败限制、根因修复 | `coding_instructions` 用户附加段（可选） |

**正交关系（QClaw）**：Vermes 已有 **模型族** L4（gpt/codex/groq 等）；场景 L4 是另一维，**on 时两者可同时在 prompt 中**。

### 2.4 L5 Workspace（非首版必做）

- **产品形态**：桌面 Workspace 卡片（分支 / 脏区计数 / Verify 命令 / context files）  
- **数据**：可对齐上游 `project_facts_for()` 语义（manifests / package manager / verify commands）  
- **prompt**：若注入 Workspace 块，仅 **on**；auto 不注（避免 surprise + cache 抖动）  
- **验收**：卡片数据与一次 `git status` + 项目文件探测一致；**不**与 gateway 渠道会话耦合  

### 2.5 L3 工具集

首版 **不做**。触发条件另立产品单：仅手动 focus/on + 明确「收窄工具」预期 + 否定测试（不得砍用户 pinned toolset）。

---

## 3. 与已落地能力的映射

| 层 | 状态 | 代码 |
|---|---|---|
| L1 技能 names-only | ✅ P1+P3 | `prompt_builder.resolve_compact_skill_categories` + 渲染 |
| L2 渠道硬门 | ✅ A′ | 白名单 + system_prompt 传 `agent.platform` |
| 模型族 L4（非场景） | ✅ 既有 | `OPENAI_MODEL_EXECUTION_GUIDANCE` 等，**保留** |
| 场景 L4 编码简报 | ⏳ 本规格「on」 | 待实现（薄文案 + 调用点） |
| L5 桌面 Workspace | ⏳ 建议独立 UI 项 | 待实现 |
| L3 focus 工具集 | ⏸ 不做 | — |
| P2 上游 591 行整包 | ❌ 不做 | 除非出现「与上游行为合同硬对齐」需求 |

---

## 4. M7 迁移

| 现状 | 目标 |
|---|---|
| 设置 → 安全 →「编码场景技能索引」：**关闭 / 自动** | 同一枚控件改为 **关闭 / 自动 / 编码模式（on）** |
| 文案 | off：全量技能描述；auto：代码项目仅交互式会话 names-only；**on：编码模式 — 技能列表精简 + 注入编码工作简报** |
| 存储 | 继续 `PATCH /api/config` → `agent.compact_skill_categories` |
| 旧配置 | `off`/`auto` 不变；新增合法值 `on` |
| 展示 | 当前模式 + 一行解释「auto 不会改聊天助手人设；on 才会」 |

---

## 5. 否定测试清单（实现 on 时必写）

| # | 场景 | 期望 |
|---|---|---|
| 1 | `auto` + telegram/feishu/qqbot/… + cwd=仓库根 | 无 names-only、**无** L4 简报 |
| 2 | `auto` + desktop + 代码目录 | 有 names-only；**无** L4 简报 |
| 3 | `on` + telegram | 无 L4 / 无 names-only（平台门） |
| 4 | `on` + desktop + 任意 cwd | 有 L4 简报 + names-only |
| 5 | `off` + desktop + 代码目录 | 全量技能；无 L4 |
| 6 | 空/未知 platform + `on`/`auto` | 一律不降级、不注入 |
| 7 | 模型族 L4 与场景 L4 叠加 | on 时两套 guidance 字节同时存在，互不覆盖 |
| 8 | None 基线 | `off` 时 system prompt 与改造前一致（回归） |

---

## 6. 验收（产品）

| 项 | 标准 |
|---|---|
| 默认安装 | 未改配置 → 行为与今日 P1 默认 off 一致 |
| 打开 auto | 仅交互式 + 代码目录技能列表缩短；**聊天语气不变** |
| 打开 on（桌面） | 立刻出现编码工作简报 + 技能 names-only；IM 渠道会话不受影响 |
| IM | 任意配置下不会「突然工程师腔」 |
| 可回退 | 任一模式改回 off，下一会话 prompt 回到基线 |

---

## 7. 建议排期（仍与 W/QClaw 并行解耦）

| 步骤 | 内容 | 归属建议 |
|---|---|---|
| 0 | 董董确认 §1 决策表 | 产品 |
| 1 | `on` 语义 + L4 文案薄实现 + §5 测试 | MiMo（`agent/prompt_builder` + `system_prompt`）—— **等 QClaw 让开或指定窗口** |
| 2 | M7 三态 UI + 文案 | MiMo（`frontend/Settings.vue`） |
| 3 | （可选）L5 Workspace 桌面卡片 | 前端 + 只读探测 API |
| 4 | 实测：on 下 coding 会话 vs 聊天会话 prompt 差 | 度量脚本扩展 |

---

## 8. 明确不做（本规格）

- 不移植 `agent/coding_context.py` 整包  
- 不把场景 L4 挂到 **auto**  
- 不在 messaging 上自动编码姿态  
- 不在首版做 L3 工具集收窄  
- 不把「与上游文件同构」写进验收  

---

## 9. 给决策者的三句话

1. **默认 off** —— 多表面产品不能抄 CLI-only 的 auto 默认。  
2. **auto = 只缩列表（L1+L2）** —— 可逆、低风险、A′ 已兜住 IM。  
3. **on = 完整「像工程师」（+L4）** —— 体感靠手动编码模式，不靠 cwd 猜测。

**同意本表 → 我出实现拆步（仍先测试后代码）；要改默认或 auto 层范围 → 只改 §1 表，不必重开 P2 移植辩论。**

— MiMo 起草 · 待董董拍板
