# MiMo 点验 · A′ 渠道硬门（QClaw `034d41bb69`）

> 日期：2026-09-20 · 只读点验，未改 `agent/prompt_builder.py` / `system_prompt.py`  
> 交接依据：QClaw 工作报告 + 约定「MiMo 只读 + 跑否定测试」

---

## 判定：✅ 通过

| 审计项 | 结果 | 证据 |
|---|---|---|
| 提交真实存在 | ✅ | `034d41bb69` 5 files +124/−12 |
| 白名单制默认拒绝 | ✅ | `_INTERACTIVE_CODING_PLATFORMS = {cli,web,desktop,tui,acp,local,api,api_server}` |
| messaging 永不降级 | ✅ | 19 渠道否定测试 + 实跑 `telegram → None` |
| 未知/空 platform | ✅ | `some-future-plugin` / `""` / `None` → None |
| 交互式 + 代码目录 | ✅ | `desktop` + pyproject → deny-list frozenset（33 类） |
| 交互式 + 非代码目录 | ✅ | `cli` + plain tmpdir → None |
| 调用点接线 | ✅ | `system_prompt.py:342-344` `platform=getattr(agent, "platform", None)` |
| 测试实跑 | ✅ | `test_a1_channel_gate` + P1 + P3/M7：**25 passed** |
| 未碰 gateway | ✅ | commit 仅 agent/ + tests/agent/ |

## 实跑抽样（main 工作区）

```
resolve(..., platform="telegram")  → None
resolve(..., platform="desktop")   → frozenset(len=33)   # auto + 代码目录
resolve(..., platform=None)        → None
resolve(..., platform="")          → None
resolve(plain_dir, platform="cli") → None
```

## 观察（非阻塞）

1. **`platform=None` 默认拒绝**：fail-safe 正确；但若桌面/Electron 路径 `agent.platform` 为空，则 **auto 永不生效**（等于 off）。产品需保证桌面会话 platform 为 `desktop`/`cli`/`web` 之一，否则用户开了 M7 auto 也看不到降级。建议后续在桌面 agent 构造处核对 `platform` 赋值（**不**在本 A′ 范围）。
2. **`api_server` 在白名单**、否定测试的 interactive 用例未列该项——行为仍正确（白名单包含）；测试覆盖可再补一行，非必须。
3. 与上游对齐：上游 `{cli,tui,acp,desktop,""}` 含空串；本实现 **空串拒绝**，比上游更严——对 Vermes gateway 更安全，**支持该偏严**。

## 会前共识落地情况

| 项 | 状态 |
|---|---|
| A + B | 维持（P2 大移植仍不做） |
| **A′** | **✅ 已合入 main** |
| C/D focus 大移植 | 仍不做，除非产品要 focus 姿态 |

## 结论

A′ 堵住了「M7 auto + gateway cwd=仓库根 → IM/群聊整锅 names-only」的真实缺口。  
**MiMo 点验通过，无返工项。** 技能索引切片（P1/P3/M7 + 渠道硬门）闭环。

— MiMo
