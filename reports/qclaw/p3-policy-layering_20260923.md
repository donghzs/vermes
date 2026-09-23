# P3 重构：策略单源 + 事实/策略分层 + 安全段分级 · 2026-09-23

> 状态：**已落地**。分支 `fix/p3-policy-layering`（worktree `/Users/dongzusheng/Projects/vermes-electron-p3r`）。
> 基线：`19383671ec`。采纳 Hermes 结构意见；驳回其两条事实断言（见 §3）。

---

## 1. 正确形状（已实现）

```
disabled_section_ids()              ← 唯一策略源（env 解析 + 安全段确认）
        │
        ├─ _resolve_section         装配：命中 → ("", "disabled", "")，不注入、无标记
        ├─ list_prompt_sections     清单：enabled=False + 保留真实 path/source/layer
        └─ missing_core_sections    诊断：排除被禁（禁用 ≠ 缺失）

load_all_processors()               ← 事实层，不过滤
register_plugin_processor()         ← 只登记事实，不拒登
```

| 边界 | 行为 |
|---|---|
| 非安全段 | env 直接可关（A/B 去段 / 关噪音 / 排障） |
| 安全段 `SAFETY_SECTION_IDS` | 须 `VERMES_DISABLE_PROMPT_SECTIONS_CONFIRM=1` + WARNING；否则**不禁用** |
| 未知 id | WARNING + fail-open，不阻塞启动 |
| 可见性三件套 | 不注入 + 启动打印「已按 env 禁用 N 段」+ doctor 可查（含真实 path） |
| A/B 纯度 | 禁用返回空串，**不**塞 `[disabled]` 标记 |

安全段（显式名单，不用 `risk_tier`——editing_guardrails 在 YAML 里是 L1 低风险，
与「禁用后的安全影响」不是同一维度）：`editing_guardrails`、`tool_use_enforcement`。

---

## 2. 与上一版（`19383671ec`）的差异

| 点 | 上一版 | 本版 |
|---|---|---|
| 过滤位置 | load_all 出口 | 装配侧 `_resolve_section` |
| 事实层 | 被策略污染 | 完整（清单/诊断见真章） |
| register | 拒登 | 照登（策略不进注册表） |
| 清单元数据 | 合成行 `path=env:…` | 真实 path/source + `disabled_by_env` |
| 安全段 | 无分级 | CONFIRM=1 二次确认 |
| 未知 id | 静默 | WARNING |

---

## 3. 对 Hermes 断言的判定（留档）

| 断言 | 判定 |
|---|---|
| 清单里被禁段整个消失 | **说重了**（已有补行）；元数据失真是真问题 → 本版修 |
| `missing_core_sections` 会把禁用报成 missing | **错**（`sid not in disabled` 一直在） |
| register 拒登污染事实层 | **对** → 本版去拒登 |
| load_all 出口不该滤 | **对** → 本版改装配侧 |
| 安全段要二次确认（env 继承扩散） | **对** → 本版 CONFIRM |
| 未知 id 警告 / 不塞 prompt 标记 | **对** → 本版落地 |

---

## 4. 测试口径

```
test_p3_disable_prompt_sections (12)
+ core_diverge + hermes_missing + s21 + s22 + s23
+ w_l4_l5 + s2_gold + env_hints_truth          →  90 passed
```

### 双探针

| 探针 | 期望 | 实测 |
|---|---|---|
| A `kanban` 禁用 | load_all 仍含 kanban；`_resolve_section` → `("","disabled")`；list 保留真实 path | ✅ |
| B `editing_guardrails` | 无 CONFIRM 不禁用且仍注入；`CONFIRM=1` 后 `("","disabled")` | ✅ |

### 门禁

| 门 | 结果 |
|---|---|
| `s2_snapshot --check` @ 仓根 | ✅ 17×3 逐字绿 |
| `s2_snapshot --check` @ `/tmp` | ✅ 同上 |

---

## 5. 改动清单

| 文件 | 摘要 |
|---|---|
| `agent/prompt_processor_loader.py` | 策略单源 / load_all 回事实层 / register 去拒登 / list 保真 / missing 排除禁用 |
| `agent/system_prompt.py` | `_resolve_section` 装配侧过滤 |
| `vermes_cli/doctor.py` | 安全段 CONFIRM 提示 + 真实 path 展示 |
| `tests/tools/test_p3_disable_prompt_sections.py` | 按新语义重写 12 条 |
| `tests/tools/test_core_diverge_ledger.py` | 措辞钉对齐新文案 |

— 完 —
