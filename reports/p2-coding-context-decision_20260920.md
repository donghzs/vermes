# P2 编码模式 · 三方决策对比（Hermes 官方 vs QClaw/MiMo）· 2026-09-20

> **读者**：董董（拍板）/ QClaw / Hermes / WorkBuddy  
> **目的**：把分歧收成一张可勾选的表；**代码未改**，等拍板  
> **上游实证**：`~/.hermes/hermes-agent/agent/coding_context.py`（本机已核）

---

## 0. 三方共识（无争议）

| 项 | 一致结论 |
|---|---|
| 不整包移植 591 行 | ✅ |
| A′ 渠道硬门 fail-closed | ✅（`034d41bb69` + MiMo 点验） |
| Vermes 已有**模型族** L4（非场景） | ✅ `prompt_builder.py:427` 等，与场景简报正交 |
| 「像 Hermes」本质是**默认值 + auto 开哪几层** | ✅ |
| L5 优先桌面 Workspace 卡片，而非纯 prompt | ✅ |
| 空 platform 在 Vermes **拒绝**（严于上游） | ✅ |

---

## 1. 分歧只有一处：**auto 该放「加法」还是「减法」**

### 1.1 上游实证（Hermes 核出，MiMo 已回源码复核）

| 门槛 | 源码 | 含义 |
|---|---|---|
| `system_prompt_parts()` | `if not self.is_coding: return [], [], []` — **只看 profile，不看 mode** | **auto** 且代码目录 → **注入** 编码简报 + 模型编辑提示 + Workspace 快照 |
| `compact_skill_categories()` | `if not self.is_coding or self.config_mode != "focus": return frozenset()`；注释：*index changes under auto proved too surprising* | **auto 不降级**技能索引；**focus 才减法** |
| `toolset_selection()` | 仅 `config_mode == "focus"` | 工具集收窄只在 focus |

→ 上游产品语义：

```text
auto   = 加法（简报 + 快照）     ← 编码现场「更懂」
focus  = 减法（索引降级 + 工具收窄）← 显式更激进
```

### 1.2 QClaw / MiMo 原建议（与上游相反）

```text
auto = 减法（L1 技能 names-only）+ 硬门 L2
on   = 加法（L4 场景简报）
```

**Hermes 指出的产品倒挂**：

| | 上游 | 我们原建议 |
|---|---|---|
| 默认 auto 放什么 | **加法**（简报 ~2–3KB，用户体感收益大） | **减法**（索引变短，收益小且「看起来技能变少」） |
| 上游实证 | auto 下索引变化 **surprising** → 退回 focus | 我们把 surprising 项挂在 auto |

**数字（Hermes 提供口径）**：索引总量约 98KB 量级，L1 上限有限；L4 约 2–3KB 但改变**工作方式**。

### 1.3 QClaw 关切仍然成立

风险不在「要不要 L4」，而在 **web/desktop 日常聊天** 若 cwd 落在仓库根：

- A′ 可挡住 **messaging**
- **挡不住** web/desktop 上的「闲聊」
- 若 auto 注入满版工程师简报 → **人设剧变**（QClaw：高风险）

---

## 2. 两套拍板表（请董董二选一或调和）

### 方案 Q — QClaw/MiMo 原表（保守加法）

| 档位 | 技能 L1 | 简报 L4 | 工具 L3 | 说明 |
|---|---|---|---|---|
| off（默认） | — | — | — | 现状 |
| auto | names-only | **无** | 不变 | 仅列表变短 |
| on 手动 | names-only | **有** | 不变 | 完整编码姿态 |
| 迁移 | 保持 auto 降级 | | | |

**优点**：auto 绝不改语气；与现行 P1/M7 一致，零迁移。  
**缺点**：与上游实证相反；auto 体验价值低（只看得见「技能变少」）。

### 方案 H — Hermes 官方表（对齐上游实证）

| 档位 | 技能 L1 | 简报 L4 | 快照 L5 | 工具 L3 | 说明 |
|---|---|---|---|---|---|
| off（默认） | — | — | — | — | 同 Q |
| auto | **无**（零减法） | **L4 弱版** + 模式指示器 | 可选 prompt/卡片 | 不变 | **加法**；why + 一键退 |
| on 手动 | names-only | **L4 满版** | 是 | 可 L3（后置） | ≈ 上游 focus 语义 |
| 迁移 | **M7 的 auto 降级改为仅 on 生效** | | | | 一个判断改动，纠正「比上游更激进」 |

**优点**：与上游实证一致；auto 主收益（像工程师）默认可达；减法留给显式档。  
**缺点**：桌面闲聊人设风险需用 **弱简报 + 指示器 + 可退出** 兜住；M7 auto 语义要迁移。

### 方案 H′ — 调和（MiMo 倾向拍板用）

| 档位 | L1 | L4 | L5 | 指示器 |
|---|---|---|---|---|
| off | — | — | — | — |
| auto | 无 | **仅弱简报 2 条**（验证后再宣称完成；`path:line`） | 桌面卡片可选 | **必须**：「编码姿态 · 因 cwd 在 git 项目 · 本会话可关」 |
| on | names-only | 满版简报 | 是 | 可选 |

迁移同 H：**L1 降级只挂 on**（与上游 focus 对齐）。

---

## 3. MiMo 立场（供拍板，非最终）

| 问题 | 立场 |
|---|---|
| 是否接受 Hermes 的上游实证？ | **是** — 我们把「auto=减法」设计反了，应纠正 |
| 是否放弃 QClaw 的风险关切？ | **否** — 用弱简报 + 指示器 + 会话级关，而不是把 L4 永久藏进 on |
| 推荐 | **方案 H′**：auto=加法（弱）+ 零减法；on=完整 focus 语义（含 L1） |
| 默认 | 仍 **off**（三方一致） |
| 动代码前 | **先做 5 分钟 A/B**（Hermes 建议），用体感决定 auto 是否上弱简报 |

---

## 4. 建议的 5 分钟 A/B（不改代码）

1. 桌面在 **含 `pyproject.toml` 的目录** 里开一会话  
2. 问同一句闲聊（如「今天有点累」）  
3. 对比：**当前 off** vs **临时在 prompt 里手贴弱简报两条** 的回复风格  
4. 记录：是否「突然工程师腔」到不可接受  
5. 结论写回本文件 §6，再选 Q / H / H′

---

## 5. 协作面：文档唯一真源

| 文件 | 处置 |
|---|---|
| QClaw 原文 | 见 `reports/qclaw/p2-coding-context-*_20260920.md`（已从 `~/.qclaw/workspace/` **拷入仓库**） |
| 本决策 | **本文件** = 决策真源 |
| 产品规格 | `docs/SPEC_coding_mode_vermes_20260920.md` — **待按拍板结果修订**（当前仍偏 Q 表） |
| 指针 skill / QClaw root | 只放路径指针，不复制正文 |

---

## 6. A/B 与拍板记录（待填）

| 项 | 结论 |
|---|---|
| A/B 日期 / 人 | |
| 弱简报是否可接受 | |
| 拍板方案 | Q / H / H′ / 其他： |
| 董董签字 | |

---

## 7. 若拍板 H′：实现要点（仍不写代码，直至签字）

1. `resolve_compact_skill_categories`：**仅 mode/on 返回 deny-list**；auto 返回 None（迁移）  
2. 新增 `resolve_coding_brief(platform, cwd, mode, session_flag) -> str`：auto 弱 / on 满；平台白名单同 A′  
3. `system_prompt` 注入 brief（byte-stable 可缓存）  
4. M7：三态 off/auto/on，文案区分「自动=工作方式提示」vs「编码模式=完整」  
5. 指示器：桌面会话 UI 显示 why + 关闭  
6. §5 否定测试按 H′ 改写（auto **不**再断言 names-only）

---

— MiMo 整理 · 待董董在 §6 勾选
