# Vermes 涌现记忆自学习自进化 — 自我管理与自我改进愿景

> 对齐文章：[小米 Darwin Agent Team · HarnessX：可像搭乐高一样随便拼的 Harness](https://mp.weixin.qq.com/s/V3xPmsQzTiHql5bbCHsLQg)
> 日期：2026-08-02 · 状态：方向对齐 + 差距分析，待用户拍板从哪一步切入

---

## 0. 一句话结论

用户愿景（运行时利用涌现式记忆自学习自进化的正向飞轮进行**自我改造**，随时适应 AI 行业快速变化，减少不停对源码修修补补）与 HarnessX 的 **AEGIS 进化引擎**完全同构。

**关键判断：Vermes 已具备"自改写 + 审批 + 回滚 + 涌现 + 技能确认"的骨架（约 40–50% 到位），真正的缺口是——整个进化*策略*（阈值、自动处置规则、何时自改写）被焊死在 Python 源码里。这正是文章所批的"static harness 反模式"：每次想让 Vermes 行为不同，都要改源码 + `build.sh` 重建 DMG。**

打开这一道缝（策略外置），"自我改造无需重建"才成立，后续所有阶段才有地基。

---

## 1. 文章 north star 拆解（HarnessX / AEGIS）

| 文章概念 | 含义 | 对应 Vermes 目标 |
|---|---|---|
| **Harness 当乐高** | 运行时框架序列化、可哈希、可替换；8 生命周期钩子卡死控制点 | 进化*策略*（阈值/规则/白名单）外置为可运行时读取的"积木" |
| **AEGIS 四阶段** | ①压缩轨迹抓故障 ②规划踩过的坑 ③生成类型安全候选修改 ④Critic 闸门防 reward hacking + 确定性闸门（新方案不能让已解决任务变差） | 闭环提案引擎：看 outcomes/strategies/success_rate → 提案自改写/策略调整 → 带回滚安全网 |
| **变体隔离** | 多套 Harness 并行跑、任务自动路由，改一类不崩另一类 | 远期：进化实验沙箱 |
| **模型+Harness 协同** | 共享回放缓冲区，Harness 符号化编辑 + 模型 GRPO 微调 | 远期：记忆/策略随模型换代自适应 |

文章核心痛点（与本次 B 系列修复体感一致）：*提示词、工具、重试、记忆全搅在同一条代码里，改一处牵一发动全身，跨场景只能复制粘贴*——这正是"阈值硬编码在 `memory_reflection.py` 里、改它要重建"的写照。

---

## 2. 现状盘点（已实证，非臆测）

### 2.1 已经存在的"自管理"骨架 ✅

| 能力 | 证据（实读） | 状态 |
|---|---|---|
| **自我改写 + 人类审批 + 回滚** | `frontend/src/components/EvolutionPanel.vue` 调用 `/api/evolution/self_modify_history`、`/api/evolution/self_modify_rollback`、`/api/evolution/retract`；状态机 `committed/proposed/held/rejected/rolled_back/retracted/activated` | ✅ 已落地（最难的部分已有） |
| **技能涌现 + 采纳/忽略** | `/api/emergence/skills`、`/api/emergence/skill/{id}/confirm\|reject`；面板有"待确认技能"卡片 | ✅ 已落地 |
| **涌现状态可视化** | `fetchEmergence()`：health / continuity / richness（4 维密度条）/ clusters 状态 / capabilities | ✅ 已落地 |
| **进化数据层** | outcomes、strategies、success_rate、anti_patterns、DAG 关系、热门检索文档 — 后端 `/api/evolution/{status,achievements,dag}` | ✅ 数据齐全 |
| **前端进化面板** | `EvolutionPanel.vue`：状态/成就/DAG/策略表现/最近失败/涌现/技能/自我改写日志 全有 | ✅ 已落地 |

→ **结论：Vermes 已经能"改写自己 + 让人审 + 能回滚"，且数据层和展示层都在。** 这不是从零开始。

### 2.2 真正的缺口（root cause）❌

**进化*策略*全部焊死在源码**，改动必须走"编辑 Python → `build.sh` 重建 DMG → 重装覆盖 → 干净重启"：

| 策略参数 | 当前位置 | 问题 |
|---|---|---|
| 自动处置阈值 `duplicate>=0.9` / `outdated>=0.85` | `agent/memory_reflection.py:818-819, 828, 860` 硬编码字面量 | 改阈值要重建（即前文讨论的"阈值不能自己改"） |
| 哪些 flag 类型可自动处置 | 同文件：`contradiction`/`scope_creep` 永不自动（源码写死） | 策略不可配 |
| 自我改写触发条件 / 改什么 | `evolution_manager.py` / `raw_event.py` 内逻辑 | 行为边界在源码，无外置策略 |
| 涌现/聚类生命周期参数 | `cluster_lifecycle.py` 常量（如 `MIN_INTERVAL`） | 同上 |

→ **这正是"不停对源码修修补补"的根源，也是与 HarnessX 的最大差距：Vermes 的 Harness 还不是"乐高"，是"焊死的铁板"。**

### 2.3 与 AEGIS 的具体差距

| AEGIS 能力 | Vermes 现状 | 差距 |
|---|---|---|
| 四阶段闭环提案 | 有数据 + 有审批 UI，但**无"从轨迹自动提案改进"的引擎** | 缺闭环引擎 |
| Critic + 确定性闸门 | 有回滚（事后补救），无"提案前预检新方案不劣化已解决任务" | 缺预检闸门 |
| 变体隔离 | 无 | 远期 |
| 模型+Harness 协同 | 无 | 远期 |

---

## 3. 分阶段路径（每一阶段都减源码修补）

### Phase 1 — 策略外置（解"不能自改阈值"，地基）
- 把进化*策略参数*（auto_resolve 阈值、可自动处置的 flag 类型、MIN_INTERVAL 等）抽到 **`config.yaml` 的 `memory.autoResolve` 段 / 或 DB-backed settings 表**，函数运行时读取。
- 前端 EvolutionPanel 新增"策略视图"：展示当前阈值 + 允许用户/系统调参（写回 config/settings）。
- **收益**：此后调策略只动 config，不需重建 DMG。直接回应"减少源码修补"，且是 Phase 2/3 的前提。

### Phase 2 — 闭环提案引擎（真正"看轨迹越跑越聪明"）
- 在 daemon（B11 已有 `_reflection_daemon`）上加 **AEGIS 式四阶段**：压缩近期 outcomes/strategies/anti_patterns → 规划退化点 → 生成候选策略调整/自改写方案 → **Critic + 确定性闸门**（预检：新策略不使任意已解决任务成功率下降）→ 进入既有 self_modify 审批流（human-in-loop + 回滚）。
- **收益**：Vermes 从"执行硬编码规则"升级为"观察自身表现 → 提案自我改进"。

### Phase 3 — 前端进化历史检索 + 策略调优 UI
- 把当前"最近 50 条 self_modify_history"升级为**可检索、可筛选、可时间线回放的进化史**（用户原话"通过前端查看历史和检索进化"）。
- 策略调优面板：用户可见 Vermes 的当前策略、历次自动调整、效果对比，并手动覆盖。

### Phase 4（远期）— 变体隔离 + 模型协同
- 进化实验沙箱（多套策略并行、自动路由）；记忆/策略随基座模型换代自适应。

---

## 4. 建议起点

**Phase 1（策略外置）** 是性价比最高、风险最低、且直接命中用户核心诉求（减少源码修补 + 运行时自调）的切入点。它同时也是 Phase 2/3 不可跳过的前置。

> 注：Phase 1 本身仍需**一次** `build.sh` 重建把"读取 config"的代码打进 DMG；但那之后所有策略调整都只动 config，不再重建。

---

## 5. 待用户决策

- 从 Phase 1 切入？（策略外置：config 驱动 auto_resolve 阈值 + 可自动处置类型）
- 还是先只做 Phase 3 的前端历史检索（纯展示增强，不动策略逻辑）？
- 或先写 Phase 2 的闭环提案引擎设计稿（不急着落地）？
