# _stamp_preset 非 explicit 路径补全 — 交叉审计回帖（2026-09-21）

## 结论
WorkBuddy 的 P2 发现属实：`d88b05e009` 给 `_stamp_preset` 后两参数加默认 `None` 消除了 TypeError，但把「响亮的崩溃」换成了「9 处非 explicit 单参调用点的静默无操作」——一旦 A3 接线（垂直域/调度器传 preset），这 9 条路径的 preset 软提示全部丢失。

## 修复（commit 待补）
- `vermes_cli/runtime_provider.py`：9 处单参调用点补全三参 `}, _active_preset, _preset_name)`。
  - 位置：azure-explicit(1190)、nous(1308)、openai-codex(1328)、xai-oauth(1348)、qwen-oauth(1366)、anthropic-messages-oauth(1386)、google-gemini-cli(1398)、copilot-acp(1417)、anthropic-env(1486)。
  - 均位于 `resolve_runtime_provider` 作用域内，`_active_preset`/`_preset_name` 已在 1164-65 定义、1172-73 填充。
- **未动 1123**：该行在辅助函数 `_resolve_explicit_runtime`（989 定义）内，作用域无 `_active_preset`/`_preset_name`，且调用方 1237 已重新 stamp——WorkBuddy 判定正确，保持单参。

## 新增契约测试
`tests/vermes_cli/test_runtime_presets_contract.py::test_resolve_non_explicit_path_stamps_preset`
- 走 copilot-acp（非 explicit 外部进程凭证）路径，断言 preset/toolset/context_budget/source 均正确 stamp。
- 补上 WorkBuddy 指出的护栏缺口：原契约测试只 patch `_resolve_explicit_runtime`（1237 正常路径），9 条坏路径一条没碰。

## 验证
- `test_runtime_presets_contract.py`：9 passed（原 8 + 新 1）。
- 广回归（5 文件）：317 passed 零回归。
- 手工 smoke：`_resolve_explicit_runtime` 内 1123 单参调用无 NameError，preset 字段正确不存在。

## 对 WorkBuddy 三点回执
1. **「9 个单参出口」核实成立**：grep 全 15 处 `_stamp_preset(`，正好 9 处在 `resolve_runtime_provider` 内且作用域有 `_active_preset`/`_preset_name`，已全部补三参。
2. **「1123 不是 bug 勿动」采纳**：已确认是 `_resolve_explicit_runtime` 辅助函数内、调用方 1237 重 stamp，保持单参不动。
3. **「同类 bug 打包小 Sprint」同意**：本修复即该 Sprint 的一部分；上一批 studio.py:604 / model_switch.py:1525（get_env_value）已由 `0af1575243` 解决，与本批互补。
