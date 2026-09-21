# approval.py 形状对比（Vermes vs 上游）—— 取长策略裁决前置

> 触发：Hermes 建议「啃 approval 第 1 条时顺手做形状对比，10 条集中在一个文件，结构对齐收益摊薄 10 次」。
> 本文只出纸面结论，不写迁移代码。数据源：Vermes `tools/approval.py` vs 上游 `/Users/dongzusheng/.hermes/hermes-agent/tools/approval.py`（HEAD 5a0c2fb89e）。

## 一、量化形状差异（决定性）

| 维度 | Vermes | 上游 | 差异 |
|---|---|---|---|
| 行数 | 2083 | 1353 | Vermes 大 **+54%**（多 730 行） |
| 模块级锁 | `_lock = threading.Lock()` + `_pending: dict[str,dict]` + `_ApprovalEntry`(event) | `_lock` + 状态经 `_persist_choice`/`_approved()` 管理 | 状态容器不同 |
| 审批流抽象 | `_ApprovalEntry` 事件驱动 + `_await_gateway_decision`（threading.Event.wait 5 分钟） | `_GateSpec` 规格驱动 + `_Unattended` + `_smart_gate` + `_human_decision` + `_run_approval_gate` | **两套完全不同的架构** |
| server→client 往返协议 | 单向 `register_gateway_notify` + `_emit`（无 withdraw/settle/ack） | `withdraw_gateway_approval` / `register_gateway_settle` / `ack_gateway_approval` / `list_gateway_approvals` / `get_pending_gateway_approval` | Vermes 无往返协议 |
| 分层能力 | tier 分层（`effective_tier`）+ privileged grant（`_privileged_*`）+ sudo stdin 守卫 + hardline 检测 | `_Unattended` 无人值守上下文 + `_denial_breaker` + `_should_skip_container_guards` | 各有一套独有分层 |

## 二、函数级交集

**同名交集（约 18 个）**：`register_gateway_notify` / `unregister_gateway_notify` / `resolve_gateway_approval` / `has_blocking_approval` / `submit_pending` / `approve_session` / `enable_session_yolo` / `disable_session_yolo` / `clear_session` / `is_session_yolo_enabled` / `is_current_session_yolo_enabled` / `is_approved` / `approve_permanent` / `load_permanent` / `load_permanent_allowlist` / `save_permanent_allowlist` / `check_dangerous_command` / `check_all_command_guards` / `_format_tirith_description`。

**上游独有（约 30 个）**：`withdraw_gateway_approval`、`list_gateway_approvals`、`register_gateway_settle`、`ack_gateway_approval`、`pending_gateway_approval_count`、`get_pending_gateway_approval`、`_release_permission_mode_dependents`、`_yolo_active`、`_is_permanently_approved`、`_persist_choice`、`_read_permanent_allowlist`、`_baseline_key`、`is_approval_bypass_active*`、`_approved`、`_user_summary`、`_denied`、`_blocked`、`_user_approved`、`_gateway_notify_cb`、`_pending_result`、**`_Unattended`**、`_unattended_contexts`、`_unattended_deny`、**`_GateSpec`**、`_smart_gate`、`_human_decision`、`_presence`、`_run_approval_gate`、`_should_skip_container_guards`、`_user_deny_block`、`_floor_block`、`request_tool_approval`、`_tirith_scan`、`check_execute_code_guard`。

**Vermes 独有（约 35 个）**：`_fire_approval_hook`、`set/reset/get_current_session_key`、`_get_session_platform`、`_is_gateway_approval_context`、`_check_sudo_stdin_guard`、`detect_hardline_command`、`_hardline_block_result`、`_sudo_stdin_block_result`、`_legacy_pattern_key`、`_approval_key_aliases`、`_normalize_command_for_detection`、`detect_dangerous_command`、**`_ApprovalEntry`**、`request_gateway_approval`、`is_config_level_target`、`classify_component_swap`、`_source_modify_always_confirm`、`_processor_modify_always_confirm`、`_resolve_processor_tier`、`get_tier_mode`、`effective_tier`、`_privileged_*`（5 个）、`approve_privileged_action`、`_normalize_approval_mode`、`_get_approval_config`、`_get_approval_mode`、`_get_approval_timeout`、`_get_cron_approval_mode`、`_strip_shell_comments`、`_strip_line_comment`、`_smart_approve`、**`_await_gateway_decision`**。

## 三、裁决结论

**approval.py 是「结构对齐」战场，不是「逐条取长」战场。**

1. 上游 30 天的 10 条 approval fix，多数作用在 `_GateSpec`/`_Unattended`/`_smart_gate`/`_run_approval_gate` 这套**规格驱动新抽象**上，而这套抽象在 Vermes 完全不存在。
2. Vermes 走的是 `_ApprovalEntry` 事件驱动 + tier/privileged 分层，与上游是两套独立演化的审批架构。
3. 因此：
   - **逐条搬 = 写 10 次「重写」**，且每条都要为不存在的 `_GateSpec` 找 Vermes 等价面，成本不摊薄。
   - **先对齐形状 = 把上游 `_GateSpec`/`_Unattended` 抽象整体搬入**，是 walking skeleton 级别的项目（重写整个审批流 + 调用方 diff + 验收夹具），风险高、需独立立项，**不是 P0 消化队列能做的**。

## 四、可执行的中间态（建议）

把 10 条候选按「是否依赖 `_GateSpec`/往返协议」二分：

**可直接取长（作用在 Vermes 等价面，不依赖新抽象）**：
- `2afb405337c3` 锁内提交竞态 ✅ 已合入 L-004
- `05e7e891751c` pattern-key allowlist 在 unattended 时生效 —— 待核实 Vermes `_smart_approve`/`_await_gateway_decision` 是否有等价 unattended 分支
- `2a630671d7bc` cron 上下文永不交互 —— 待核实 Vermes `_get_cron_approval_mode`

**依赖 `_GateSpec`/`withdraw`/`settle`/`ack`（记入 walking skeleton 前置，暂不取长）**：
- `1e2cb5797362` / `6332216384b7` / `2dfb795cb78f`（withdraw/settle/ack 往返协议）
- `dd68d175674d` / `9e232a7ff5c1` / `3fc1a184f8c0`（GUI 栈 / 插件分类惰性求值，作用在 `_smart_gate`）

## 五、给董董的决策点

approval 是否值得投入 walking skeleton 级重构？两个选项：
- **A（保守）**：只取「作用在 Vermes 等价面」的 2-3 条，其余记 T 表挂起，维持 Vermes 自有审批架构。成本低、零风险，但上游 approval 安全修复持续漏进来。
- **B（结构对齐）**：把 `_GateSpec`/`_Unattended` 抽象作为 walking skeleton 试点整体搬入（ContextEngine 之外的第二候选）。一次对齐后，后续 approval fix 可「意图移植」低成本跟进。

建议先做 A（把 05e7e891751c / 2a630671d7bc 核实后取长），同时把 B 列入 walking skeleton 候选评估（与 ContextEngine 并列比较 ROI）。
