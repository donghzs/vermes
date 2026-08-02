# Vermes 审批分层计划（L0 / L1 / L2）

> 起因：进化系统的审批触点「太多且不分轻重」——小白看不懂 diff 只能盲批（等于没审），极客被频繁打断。P2 闭环提案引擎若再加一层待审队列会让问题更糟。
> 结论先行：**实证核查后，问题的定性需要修正——不是「全都要人审」，而是「分层反了」**。详见 §1。
> 关联：`vermes-p2-closed-loop-proposal-engine-design_20260802.md`（v3 已按本文分层调整分流逻辑）。

---

## 1. 实证盘点：现状不是「打扰太多」，是「分层反了」

原始判断是「所有触点都要人审 → 打扰太多」。逐条核查真实代码后，**方向性反转**：

### 1.1 决定性事实：桌面端默认 YOLO，最高风险动作静默放行

```python
# tools/approval.py:612-635  approve_privileged_action（self_modify / rollback 的闸门）
def approve_privileged_action(session_key, approval_data, *, surface="gateway") -> bool:
    if (is_truthy_value(os.getenv("VERMES_YOLO_MODE"))
            or is_session_yolo_enabled(session_key)      # ← 桌面端恒真
            or _get_approval_mode() == "off"):
        return True                                       # ← 直接放行，不弹窗
    result = request_gateway_approval(...)
```

```python
# vermes_cli/blueprints/chat.py:1033-1042  桌面端会话初始化
_yolo_enabled = True
_yolo_enabled = _cfg.get("approvals", {}).get("yolo_default", True)   # ← 默认 True
if _yolo_enabled:
    enable_session_yolo(_gui_sk)                                       # ← 每个桌面会话都开 YOLO
```

**推论**：桌面端默认状态下，`self_modify`（源码改写）、`self_modify_rollback`（回滚）**根本不弹窗，全部自动放行**。`chat.py:1044-1049` 的注释本身就承认了这点：「只有显式走 `request_gateway_approval` 的特权动作（如 self_modify 自我改写）会弹窗 —— **YOLO 模式下也会被自动放行**」。

更糟的是 **`yolo_default` 这个键根本不在默认配置里**——`vermes_cli/config.py:1529-1550` 的 `approvals` 段只有 `mode / timeout / cron_mode / mcp_reload_confirm / destructive_slash_confirm` 五个键。`yolo_default` 靠 `.get(..., True)` 的硬编码 fallback 生效，用户想关必须手写一个**未文档化**的键。

### 1.2 与之相反：能力激活没有 YOLO 短路，必弹

```python
# agent/raw_event.py:390-430  _bg_activate
decision = request_gateway_approval(session_key, {...})   # 直连，不经 approve_privileged_action
```

`request_gateway_approval`（`approval.py:585-609`）**只检查 session_key 与 notify 回调，没有任何 YOLO 判断**，fail-closed 到 deny。所以能力激活是**真的每次都弹**，且提示语统一写「may run pip install」——不分「装不装包」，无风险分级。

### 1.3 修正后的现状表

| 触点 | 代码位置 | 用户原判断 | **实证结果** |
|---|---|---|---|
| self_modify 源码改写 | `tools/self_modify_tool.py` → `approve_privileged_action` | ❌ 完全不懂，每次弹 | **反了：桌面端默认 YOLO → 零弹窗静默放行**（应 L2 实为 L0）|
| self_modify_rollback 回滚 | 同上 | ❌ 弹确认 | **同上，静默放行**；另 `rollback_change(initiator='user')` 用户主动回滚本就跳闸门 |
| capability_activate 能力激活 | `agent/raw_event.py:390` → `request_gateway_approval` | ❌ 批准安装 | **属实，且无 YOLO 短路必弹**；但**不分是否真装包**（应 L1/L2 分级，实为全 L2）|
| 技能确认 emergence/skills | 全部 `status='pending'` | ⚠️ 采纳/忽略 | **属实**：无自动采纳路径，全堆在面板；且前端 confirm 入口此前已查明缺失 |
| EvolutionPanel 撤回能力 | `/api/evolution/retract` | ⚠️ 可理解 | 属实，保留 |
| MCP reload 确认 | `gateway/slash_handlers/capability_handlers.py:33-58` | ❌ 不懂为何重载 | **属实但已有 opt-out**：三选项 Approve Once / **Always Approve** / Cancel，点 Always 会 `save_config_value("approvals.mcp_reload_confirm", False)` 永久静默 |
| 危险命令 rm/chmod | `tools/approval.py:316+ / 1060+` | ⚠️ 可理解 | **已是二档**：HARDLINE（无恢复路径，YOLO 也拦）vs DANGEROUS（YOLO 可过）。且桌面端 YOLO 默认开 → DANGEROUS 实际不弹 |
| P2 evolution_proposals 队列 | 本次新增 | ❌❌ 最难懂 | 属实——**正因如此 v3 已改为「双闸门过 → 自动 apply，不进队列」** |

### 1.4 真正的问题陈述（修正版）

> 不是「审批太多」，而是**风险与审批强度错配**：
> - **最高风险的源码改写（self_modify）默认零通知自动放行** —— 用户既没被打扰，也完全不知道 Agent 改了什么；
> - **中等风险的能力激活每次硬弹** —— 唯一真正打扰用户的触点，却是风险相对可控的那个；
> - **低风险的技能采纳无限堆积** —— 不打扰但形成「待办债」，最终被整体忽略；
> - 用户唯一的总开关是 YOLO，**全有或全无**：关掉 YOLO 会让 self_modify 变成每次弹窗（真打扰），开着 YOLO 则源码改写完全失控。

**这正是「全有或全无」的具体形态**——原始判断的方向对，只是当前默认落在「全无（YOLO 全过）」这一侧，而不是「全有」。分层的价值因此更大：它同时解决「危险的没拦住」和「安全的老在问」两个反向问题。

---

## 2. 分层模型（L0 / L1 / L2）

```
           低风险 ←──────────────────────────────→ 高风险
  ┌────────────────┬────────────────────┬────────────────────┐
  │ L0 自动处置     │ L1 静默执行         │ L2 必须人工         │
  │ 不通知          │ 通知 + 24h 可撤回   │ 弹窗审批            │
  ├────────────────┼────────────────────┼────────────────────┤
  │ 孤儿 flag 清理  │ P2 B1 config 提案   │ 源码改写 self_modify│
  │ config 微调±10% │ (双闸门过)          │ 文件删除 / rollback │
  │ 高置信技能采纳  │ 无 pip 的能力激活   │ 带 pip 的能力激活   │
  │                │ config 调整 10~20%  │ config 调整 >20%    │
  │                │                    │ HARDLINE 命令       │
  └────────────────┴────────────────────┴────────────────────┘
```

**核心原则：事后撤回优于事前审批。** 用户不忙时看到通知，觉得不对再撤，不打断工作流；只有**不可逆或高代价**的动作才值得阻塞。

**判定风险的三个客观维度**（不靠 LLM 主观判断）：
1. **可逆性** —— 有 `.bak` 快照能一键还原？→ 可降级到 L1。不可逆（删文件、装包）→ L2。
2. **爆炸半径** —— 只改一个 config dial（影响面已被确定性闸门量化）→ L1；改源码（影响面无法离线量化）→ L2。
3. **幅度** —— config 相对变化 ≤10% → L0；10~20% → L1；>20% → L2。这条纯算术，可直接实现。

---

## 3. 现成底座（不需要新造轮子）

分层落地的四个机械前提**代码里已经有了**，这是本计划成本低的原因：

| 底座 | 位置 | 用途 |
|---|---|---|
| `approvals` 配置段 | `vermes_cli/config.py:1529-1550` + `save_config_value("approvals.X", ...)` | 每个触点的档位直接加键，运行时可调（**无需重建**）|
| 三选项确认原语 | `tools/slash_confirm`（Approve Once / Always Approve / Cancel），已用于 mcp_reload / destructive_slash | L2 弹窗的统一 UI；「Always」自动降级该触点到 L1/L0 |
| `.bak` 快照 + 免审回滚 | `EmergentChangePipeline.apply_change` 写前生成 `<target>.bak.<ts>`；`rollback_change(initiator='user')` **跳过闸门** | **L1「24h 撤回」的机械底座** |
| 危险命令二档 | `approval.py` HARDLINE vs DANGEROUS | 证明分层范式在本仓已被接受，可照搬 |

**缺的只有三件**：① 每个触点读档位的那几行；② L1 的「通知 + 撤回窗口」记账；③ 前端展示入口。

---

## 4. 按触点的改造清单

### T1. self_modify 源码改写 —— 从「实际 L0」拉回 L2（**最高优先级**）

现状是最危险的：源码改写静默放行。

- **改法**：`approve_privileged_action` 增加**动作类型感知**——源码级改写（`target_path` 非 config）**不受 session YOLO 豁免**，必须走 `request_gateway_approval`；YOLO 只豁免 config 级与 shell 命令。
- 配置键：`approvals.source_modify_always_confirm`（默认 `True`，即便 YOLO 也拦）。
- 理由：YOLO 的语义是「我信任你跑命令」，不该被隐式扩展成「我信任你改自己的源码」。且源码改写必须重建 DMG 才生效，本就不是高频动作，拦截成本极低。

### T2. capability_activate 能力激活 —— 从「全 L2」拆成 L1 / L2

- **改法**：激活前判定该能力是否需要 `pip install` / 写系统路径：
  - **不需要** → L1：直接激活 + 通知「已启用能力 X（可撤回）」，复用 `/api/evolution/retract`；
  - **需要** → L2：保持弹窗，提示语明确写出**将安装的包名**（而不是笼统的 "may run pip install"）。
- 配置键：`approvals.capability_activate`（`auto` / `tiered`（默认）/ `always_confirm`）。

### T3. 技能采纳 —— 引入 L0 自动采纳，止住待办债

- **改法**：`pending` 技能若同时满足「置信度 ≥ 阈值」且「近 N 天使用频次 ≥ 阈值」→ 自动 `active`，不进 pending 列表；否则保留 pending。
- 阈值走 P1 的外置范式（`memory.skillAdopt.*`），复用 `>0` 注入护栏。
- 与 P2 一致：**自动采纳的技能也记一条可撤回记录**，用户能在面板里看到「自动采纳 N 个」并撤。

### T4. P2 B1 config 提案 —— L1（已在 v3 设计稿落定）

- 双闸门（Critic `safe` + confidence≥0.7，确定性 precision 不降且 `count_delta ≤ 1.5x`）过 → **自动 apply + 通知 + 24h 撤回**；
- 任一闸门没过 → 进 `proposed` 队列（L2）。
- 幅度护栏：即便双闸门过，若相对变化 **>20%** 仍强制降级到 L2 队列（§2 维度 3）。

### T5. 统一「变更通知中心」—— L1 的唯一新增机制

L1 成立的前提是「用户事后能看见」。当前**没有**统一的变更流。

- 新增轻量表 `agent_changes`（或复用 `evolution_proposals` + 视图）：`kind / summary / detail / bak_path / retract_deadline / status / created`。
- 所有 L0/L1 动作写一条记录；L0 只记录不推送，L1 记录 + 推送一条通知。
- 前端：EvolutionPanel 顶部改为 **「已自动调整 N 项（可展开 / 24h 内可撤回）」+「待审 N 条」**，取代当前只显示待审数的做法。

### T6. 总开关重构 —— 把 YOLO 从「二元」改成「档位」

- `approvals.tier_mode`：`conservative`（多数触点 L2）/ `balanced`（**默认**，本文分层）/ `autonomous`（多数触点降到 L0/L1，仅保留不可逆动作 L2）。
- 保留 `yolo_default` 作向后兼容别名，但**显式写进 `config.py` 默认段并加注释**（消除未文档化的隐式 True）。

---

## 5. 实施顺序

| 阶段 | 内容 | 是否需重建 DMG |
|---|---|---|
| **S1（安全修复，最高优先）** | T1：源码改写不再被 YOLO 豁免 + `yolo_default` 写进默认配置段并文档化 | 需重建（改 Python） |
| **S2** | T5：`agent_changes` 变更记录 + 通知写入（L1 底座） | 需重建 |
| **S3** | T4：P2 分流接上 L1（自动 apply + 撤回记账 + >20% 降级） | 需重建 |
| **S4** | T2 + T3：能力激活分级、技能自动采纳 | 需重建 |
| **S5** | T6 + 前端：档位总开关 + EvolutionPanel「已自动调整 / 待审」双区 | 需重建（前端同样冻结） |

**合批建议**：S1–S4 都是 Python 改动，应**合并进同一次 `build.sh` 重建**；S5 前端改动可一并纳入。按部署铁律，冻结的 `/Applications/Vermes.app` 不读仓库源码，任何一项都要重建 DMG + 重装 + 干净重启才进运行态。唯一例外是**已外置的配置键**——档位一旦落地，后续调档只改 `~/.vermes/config.yaml`，不再重建。

---

## 6. 实施进展（2026-08-02 更新）

### 已落地（源码，**尚未进运行态**——按部署铁律需 `build.sh` 重建 DMG）

| 项 | 内容 | 位置 |
|---|---|---|
| **T1 ✅** | 源码级改写不再被 YOLO 豁免 | `tools/approval.py` 新增 `is_config_level_target()` + `_source_modify_always_confirm()`；`approve_privileged_action` 改为分层判定 |
| **T1 ✅** | `yolo_default` 写进默认配置段并注释 | `vermes_cli/config.py` `approvals` 段（消除未文档化的隐式 True）|
| **T1 ✅** | 新增开关 `approvals.source_modify_always_confirm`（默认 `True`）| 同上 |
| **T4 ✅** | 幅度护栏：>20% 强制降级 L2 | `agent/memory_reflection.py` `_exceeds_magnitude()`，阈值外置 `evolution.autoApplyMaxDelta`（沿用 >0 注入护栏）|
| **T7 ✅（顺带发现）** | autoResolve 配置键名前后端不一致导致**外置失效** | 见下 |

**分层后的判定规则**（`approve_privileged_action`）：

- 目标是 `config.yaml` / `.yml` / `.json` / `.toml` → **config 级**，YOLO 照旧直接放行（可逆：`.bak` + 面板撤回）；
- 目标是 `.py` / 脚本 / 空路径 → **源码级**，即便 `VERMES_YOLO_MODE` / 会话 `/yolo` / `approvals.mode=off` 也**必须弹窗**，弹窗描述前置一行说明为何这次没被 YOLO 放过；
- 读配置异常、无 session、无 notify 回调 → **fail-closed 拒绝**，绝不静默通过。

### T7：`memory.autoResolve.*` 的配置外置此前是**失效的**

核查 T4 基准值时发现：`config.py` 默认段写的是 `duplicate_confidence / outdated_confidence / cluster_min_interval_s / merge_cleanup_confidence`，而**唯一的读取方** `memory_reflection.py:_load_auto_resolve_config()` 只查 `duplicate / outdated / cluster_min_interval / merge_cleanup`。全仓 `duplicate_confidence` 仅出现在 config.py 那一行——**用户照着默认配置改这些键，等于什么都没改**，P1「策略外置」对这几个 dial 只完成了一半。

与 `yolo_default` 是同一个反模式：**真正生效的键未文档化，文档化的键不生效**。

- 修法：默认段键名对齐读取方（短名），`_load_auto_resolve_config` 同时接受长名作向后兼容别名。
- 连带：`_exceeds_magnitude` 的基准值走 `_load_auto_resolve_config()` 而非裸查 config，否则用户没显式写该键时会被误判成「新增 dial 无基准」而全部降级 L2。

### T1b：「必须确认」≠「必须反复问」（弹窗预算，2026-08-02 追加）

用户拍板：**「弹一两次用户确认就够了，弹多了就是效率低下。非必要不弹，必须弹才弹。」**

这不推翻 T1，而是给它配一个弹窗预算。原实现每次源码改写都独立走一遍 Gateway 审批——一次任务改 5 个文件就弹 5 次，这才是真正的效率损失。改成**批准是有记忆的**：

| 用户的回答 | 授予范围 | 复用现有机制 |
|---|---|---|
| 拒绝 | 无（下次照旧问） | — |
| 仅本次 | 该动作类别 **30 分钟**内不再问 | 新增 `_privileged_grants`（TTL 通行证）|
| 本次会话允许 | 到会话结束 | `approve_session()` |
| 始终允许 | 永久，写入 config | `approve_permanent()` + `save_permanent_allowlist()` |

关键设计：

- **TTL 通行证是兜底**。桌面弹窗当前只有「拒绝/仅本次/本次会话允许」三个按钮，即便用户永远只点「仅本次」，30 分钟窗口也已经把「一轮工作弹 5 次」压成「弹 1 次」。前端不改也生效。
- **授权按动作类别隔离**：`self_modify`（改源码）与 `self_modify_rollback`（回滚/删文件）各自授权，批准前者不顺带放行后者。scope key 带 `privileged:` 前缀，与命令 allowlist 的 pattern key 天然不冲突。
- **grant 检查放在 YOLO 判定之前**：用户之前答过「始终允许」，就不该因为当下 YOLO 开关状态不同而再被问一遍。
- **拒绝 / 超时不产生授权**（`resolved=False` 或 `choice=deny` → 不写 grant）。
- **会话结束清空 TTL 通行证**（`clear_session` 一并清），新会话重新确认。
- `approvals.privileged_grant_ttl_minutes` 默认 30，**`0` = 每次都弹**。注意这里刻意**不套** P1 的「>0 才生效」注入护栏：在 P1 那里 0 意味着放宽 agent 自身权限（危险），在这里 0 只意味着更多弹窗（收紧），所以按写的值原样尊重。读配置失败 → 不发通行证（宁可多问一次）。

前端同步（`ApprovalDialog.vue`）：补「始终允许」按钮（仅当后端下发 `scope_options` 含 `always` 时显示）、高亮推荐项、加一行说明「批准后 N 分钟内同类操作不再询问」——否则用户会把「仅本次」当成唯一安全选项，反而自找弹窗。

净效果：T1 的安全性不变（源码改写仍然必须有一次人工确认，YOLO 不豁免），但**稳态弹窗次数从「每次改写一次」降到「每轮工作一次」，用户点一次「本次会话允许」后归零**。

### 测试

`tests/tools/test_approval_tiering.py`（**22 条**：T1 10 条 + T1b 弹窗预算 12 条）+ `tests/agent/test_aegis_proposals.py`（30 条，含 T4 新增 9 条）**全绿**。
既有失败 `test_approval.py::test_gateway_runner_binds_session_key_to_context_before_agent_run` 属预存技术债——它 AST 断言 `gateway/run.py` 里存在 `run_sync` 与 session-key 绑定，而该文件里这三个符号一个都不存在，且本次未改动该文件。

### 未做

- **T5 变更通知中心**：仍未实现。这意味着 **L1 目前事实上退化成 L0**——自动 apply 只写 `logger.info` 与 `emergence_status` 计数，用户不主动打开 EvolutionPanel 就完全不知情。这是 L1 成立的前提，应排在下一位。
- T2（能力激活分级）、T3（技能自动采纳）、T6（`tier_mode` 档位总开关）、S5 前端双区展示。
- **撤回窗口的物理上限**：`MAX_BACKUPS_PER_FILE = 5`，同一文件连续自动 apply 超过 5 次后早期备份被清理，此时撤回返回明确错误而非错误还原。若 24h 内提案频率可能 >5，需要加大该常量或按提案独立留存备份。

---

## 7. 待拍板

1. ~~**T1 是否认同**~~ → **已拍板（2026-08-02）**：认同拦截，但要求「非必要不弹」。已按 T1b 实现弹窗预算：源码改写仍必须人工确认一次，但同类动作 30 分钟 / 一个会话 / 永久内不再重复问。默认 TTL 30 分钟是否合适可再调（`approvals.privileged_grant_ttl_minutes`）。
2. **默认档位**：`balanced` 作为默认是否合适？还是先发 `conservative` 观察一段时间。
3. **24h 撤回窗口**是否合理，还是按动作类型区分（config 24h / 能力激活 7d）。
4. **S1 是否单独先发**：它是安全修复（当前源码改写零拦截），可以不等 S2–S5 一起打包。
