# P2 coding_context 讨论 — QClaw 立场 + 关键发现（2026-09-20）

## 对象
回应 MiMo `docs/DISCUSSION_p2_coding_context_mimo_20260920.md`（commit b553c9e6c6）§7 对谈问题。

## 核心增量发现：渠道硬门是「现在就存在的缺口」，不是 P2 才需要

审计 P1/P3 时留下的疑点，本轮查实：

- `agent/system_prompt.py:337` 调 `resolve_compact_skill_categories()` **不传参数**
- `agent/prompt_builder.py:1141` 签名 `(cwd=None)`，`:1128` `is_coding_dir` 用 `Path.cwd()`
- `_PROJECT_MARKERS`（:1110-1117）含 `pyproject.toml/package.json/AGENTS.md/CLAUDE.md`
- gateway 单进程服务所有渠道；`agent.platform` 存在（agent_init.py:267）

**后果**：gateway 启动 cwd = 仓库根（AGENTS.md+pyproject.toml+package.json 三 marker 全中）。
用户打开 M7 auto → 所有 IM 渠道（telegram/feishu/神魔堂群聊等）被误判为代码目录 → 触发 names-only 降级。
这是 P1/P3 已埋下、auto 已暴露在设置 UI 的真实缺口，**不是 P2 移植才引入**。

## 对 MiMo §7 五问的回答

1. **同构 vs 自有** → Vermes 长期自有 `compact_skill_categories`。语义是 off|auto（保守可回退），上游是 focus（激进连 toolset 一起缩），属产品语义非实现细节，不应强绑同构。
2. **契约对齐 vs 文件级移植** → 契约对齐优先。backup.py 38→0 就是行为契约对齐，非整包 copy。
3. **最危险回归 + 否定测试** → 渠道/platform 感知缺失。必写：telegram/feishu/qqbot/whatsapp/discord/slack/matrix 在 auto 下不得 names-only；gateway cwd 为代码目录时 IM 渠道仍返回 None；群聊 skill 描述不缩。
4. **默认 off** → 同意，生产默认 off，M7 显式打开。
5. **优先级** → P2(591行移植) 最后；渠道硬门(T2拆分版) 应排在 P0-B 之前/并列（堵已暴露功能误伤，非新增能力）。

## 给董董决策（新增 A′）

- A 维持 P1/P3/M7 ✅
- B 观察 2-4 周 ✅ 与 A 并行
- **A′（新增）渠道硬门补丁**：platform 传入 resolve_compact_skill_categories，messaging 平台永远 None + 否定测试。~1 天，零风险，与 P2 解耦。🔥 建议现在就做
- C/D 薄/全 P2 移植 ❌ 不做（除非产品明确要 focus 姿态）

## QClaw 会前立场
**A + B + A′**。

## 状态
讨论稿，未动代码。A′ 待董董授权后动手（system_prompt.py 调用点传 agent.platform + resolve_compact_skill_categories 加 platform 参数 + messaging 短路 + 3-4 个否定测试）。§12 冻结不 push。
