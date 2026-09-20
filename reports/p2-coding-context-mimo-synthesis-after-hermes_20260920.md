# P2 后续 · Hermes 批驳核验 + MiMo 综合 · 2026-09-20

> **背景**：QClaw 终局 → Hermes 事实批驳 → MiMo 回源码核验 → **QClaw 复核后无异议，仅保留一条**。  
> **本文**：三方收敛后的**执行清单**；待董董勾选。  
> **相关**：`reports/qclaw/p2-coding-context-qclaw-终局立场_20260920.md`（头注已修正）

---

## 1. 核验结果（MiMo 实测）

| Hermes 主张 | 实测 | 判定 |
|---|---|---|
| ③ L5 Vermes **无** workspace/verify/project_facts 等价物 | `agent/` `vermes_cli/` `tools/` 搜 `Workspace (snapshot` / `project_facts` / `detect_project_facts` / `verify_commands` / `Branch:` 快照 → **零命中** | **Hermes 对；QClaw 错** |
| ④ L4 仅部分覆盖 | 见下表 | **Hermes 基本对** |
| ② coding_context = 纯成本优化 | L1=成本；L4/L5 含正确性与安全 | **半对——简化过度** |
| ⑤ 唯一缺口 L3 | 真实缺口是 **L5 整块 + L4 约 3 条**；L3 最后 | **Hermes 对** |
| ① 591 行不必整包搬 | 与分档实证一致 | **结论仍对** |

### L4 逐条（对照上游 CODING_AGENT_GUIDANCE 意图）

| 纪律 | Vermes 现状（实测） |
|---|---|
| 先读再改 / 勿臆造 | ✅ OPENAI `prerequisite_checks` + GOOGLE verify-first；context 文件机制（AGENTS/CLAUDE/cursorrules）✅ |
| 验证后再宣称完成 | ✅ OPENAI `<verification>` |
| **`path:line` 引用** | ❌ 未见 |
| **禁止顺手重构/无关改动** | ❌ 未见 |
| **默认不 commit/push** | ❌ 提示层未见；`tools/approval.py` 仅审 `git reset --hard` / `--force` push，**不**禁普通 commit/push |
| 服从仓库约定文件 | ✅ context files 扫描（prompt_builder AGENTS/CLAUDE） |

→ QClaw「L4 已有更强等价物」**不成立**；缺的是 **质量/安全向 3 条**，不是「模型族块完全没纪律」。

---

## 2. 修正后的结论（一句话）

| 旧说法（QClaw 终局） | 修正版 |
|---|---|
| P2 永久不做 | **整包 591 行决策器仍不做** ✅ |
| L5 已有更强等价 | ❌ **L5 不存在，应补**（信息前置 + 防过期 git 状态瞎改） |
| 唯一缺口 L3 | ❌ **缺口 = L5 + L4 三条**；L3 真的最后 |
| coding_context 全是成本 | ❌ L4/L5 含 **正确性/安全**，不是省 token |
| 「皆 code → 不用补 L4/L5」 | ⚠️ 能力统一 ≠ **提示词现场信息与安全纪律已到位** |

**Hermes 对齐点**：要补的恰好是上游放在 **auto** 的那半（简报纪律 + Workspace 快照），**不是** focus 的减法（L1/L3）。

---

## 3. 判定标准（建议董董沿用）

> **改不改变结果的正确性/安全性？**

| 层 | 正确性/安全？ | 建议 |
|---|---|---|
| **L5** Workspace（Root/Status/Project/Verify/Context files） | **是**（少瞎改、验证命令明确） | **该做**（~数百字节；prompt + 桌面卡片可共用） |
| **L4 缺 3 条**（path:line / 别顺手重构 / 默认不 commit·push） | **是**（少误改、少说谎、少误提交） | **该做**（纯文案，并入现有 guidance） |
| L1 索引降级 | 否（成本/噪音） | 可做；触发建议 **token 长度**（非 coding 场景） |
| L3 工具收窄 | 否（注意力） | 最后；有证据再议 |
| **591 行模式裁决器** | — | **不做**（避免再造 cwd/platform 姿态开关） |

---

## 4. MiMo 综合立场（在 QClaw 终局上打补丁）

1. **同意**：不移植 `coding_context.py` 整包；不建 focus/auto 姿态状态机；A′ 仍作安全补丁；**皆 code + 记忆底座**仍是产品主线。  
2. **同意 Hermes**：QClaw「L5 有等价 / 只缺 L3」**事实错误**，若照此收口会砍掉低成本高收益项。  
3. **要做的两件加法（与「姿态开关」无关）**：

| ID | 内容 | 形态 | 是否引入 coding 模式 |
|---|---|---|---|
| **W-L5** | 工作区事实块：git root / branch / dirty 计数 / manifests / package manager / verify 命令 / context files | 探测函数 + system prompt 短块 +（可选）桌面 Workspace 卡片 | **否**——有 git 工作区则注入；失败则空，永不挡消息 |
| **W-L4** | 补 3 条纪律文案：`path:line`；只改任务相关不顺手重构；**默认不 commit/push**（除非用户明确要求） | 并入现有通用/模型族 guidance，或独立小段 | **否**——恒定或桌面默认注入，**不**按 cwd/platform 分支 |

4. **L1**：与 W 解耦；若董董批 M7，仍按 **token 长度阈值** 触发（见 QClaw 终局 §6 MiMo 附录）。  
5. **工具层**：`approval.py` 对普通 `git commit`/`git push` **无禁令**；W-L4 是提示纪律；若产品要硬拦，另立安全工单（不混进本切片）。

---

## 5. 与 Hermes「auto=加法」的关系

| | 说明 |
|---|---|
| 自洽点 | W-L5 + W-L4 ≈ 上游 **auto 注入** 的内容子集，且 **不**带回 focus 减法 |
| 差异点 | 我们 **不**采用「coding_context 开关 + cwd 裁决」；注入条件是 **可探测的工作区事实** + **恒定纪律**，渠道/记忆模型不被姿态机绑架 |
| 风险 | 工作区块若在 gateway 消息路径注入，可能增加无关字节 → 建议：**仅交互式/桌面会话**注入 L5（仍非「coding 姿态」，只是成本控制）；IM 续任务靠记忆底座，不塞 git 快照 |

---

## 6. 建议排期（待董董勾选）

| 优先级 | 项 | 量级 | 前置 |
|---|---|---|---|
| P-a | **W-L4 三条文案** 并入 guidance | 极小 | 无 |
| P-b | **W-L5 探测 + prompt 块**（桌面优先） | 小–中 | 设计探测失败 fail-open |
| P-c | 桌面 Workspace 卡片（UI） | 中 | 依赖 P-b 数据结构 |
| P-d | M7 **token 阈值** 降级 | 小 | 董董拍板阈值 |
| — | 591 行 / focus 工具集 / 姿态开关 | — | **不做** |

---

## 7. 三方收敛执行清单（2026-09-20 · QClaw 复核后）

| 项 | 状态 | 备注 |
|---|---|---|
| 591 行 `coding_context` 决策器 | **不做** | 无争议 |
| A′ 渠道硬门 | **保留** | 安全补丁，非能力分层 |
| **W-L5** 工作区事实块 | **该做** | fail-open；有 git 工作区注入，无则空；**不拦消息** |
| **W-L4** `path:line` + 禁止顺手重构 | **该做** | 纯文案，并入通用 guidance |
| **W-L4** 默认不 commit/push | **提示层先上** | **硬闸后置**——QClaw 唯一保留，见下 |
| L1 技能降级 | M7 改 **token 阈值** | 与 coding 场景解耦 |
| L3 工具收窄 | **最后** | 有证据再议 |

### QClaw 保留（请董董单独点选）

| | |
|---|---|
| **主张** | 「默认不 commit/push」**不要**与文案一起做成 `approval.py` 硬拦 |
| **理由** | approval 是**危险命令**闸；「默认不提交」是**工作流默认策略**。硬拦会与 `git_commit_done` 等完成态语义冲突：要么打断提交流，要么用户以为已提交其实没有 |
| **建议** | 先上 **提示层**文案；观察到「agent 乱提交」实据后再单独立项做硬闸 |
| **MiMo** | **同意该保留**（与 Hermes「静默默认策略」讨论一致；无实据不加锁） |

### 待董董勾选

- [ ] 批准 **W-L5**（工作区事实块，桌面/交互式优先，fail-open）  
- [ ] 批准 **W-L4 提示文案**（path:line / 不顺手重构 / 默认不 commit·push）  
- [ ] **硬闸**（commit/push 拦截）→ **后置**（默认勾「后置」）  
- [ ] 批准 **M7 token 阈值** 化 L1  
- [ ] 确认 **不做** 591 行 / 姿态开关 / L3（现阶段）  

**MiMo 默认建议**：四项勾上；硬闸明确后置。签字后按 P-a（L4 文案）→ P-b（L5 探测+prompt）→ P-d（M7 阈值）开独立 worktree 实现；仍不碰 gateway。

---

— MiMo 核验与综合 · 2026-09-20 · 三方收敛版
