# 桌面端全渠道统一 — 讨论过程报告

> 日期：2026-07-28
> 目的：完整记录从“用户诉求”到“最终事实链”的讨论演进，并列出被推翻/修正的认知，供审计。
> 配套：`plan-desktop-channel-unification.md`（路线）、`design-send-from-desktop-bridge.md`（步骤 3 方案）

---

## 1. 你的诉求（起点）

1. **方向确认**：桌面端做“全渠道统一控制台”，但**不是合并成一条会话**，而是**统一视图 + 同步 + 续聊**。
2. **更深一层**：桌面前端统一 + 同一 `state.db`，对“记忆-自学习-自进化-涌现一体化框架”有何影响？直觉是“同一 state.db 应该更有益”。
3. **先后分层**：先做哪一层“等会再聊”——先把事实查清楚，再出可评审设计。

---

## 2. AI 初案（第一版分析，含错误前提）

初版给出两点核心判断：

- **记忆层**：Web/桌面会话在 IndexedDB，`state.db` 只存渠道会话；记忆系统只从 `state.db` 抽 handoff 摘要 → “Web 会话是记忆盲区” → 统一到 `state.db` 后记忆素材翻倍。
- **会话层（顺序坑）**：web 会话今天不在 `state.db`，切前端读源前**必须先让 web 落 `state.db`**，否则老 web 会话消失。

> 这两点中，**“记忆盲区”前提是错的**，“顺序坑”前提**是对的**（但初案未充分证明，后被误推翻）。

---

## 3. WorkBuddy 第一轮推翻（压力测试审计）

对“桌面续聊写闭环”做代码级压力测试，推翻两处方案核心闭环：

- **“带 telegram session_id 发 `/api/chat/completions` 即可续聊” ❌**：
  `chat.py` 对 `create_session`/`append_message`/`handoff_state`/`state.db` **零引用**；`req.session_id` 只当 agent 缓存键，agent 在 **web 进程**跑、回复经 SSE 回前端，**不桥接渠道**。所以 web 端发消息 TG 用户收不到，两渠道脱钩。
- **“复用 handoff 机制做桌面控渠道” ❌**：
  `handoff_state` + `_handoff_watcher` + `_process_handoff` 是 **CLI→渠道迁移**（注入“从 CLI 接管”合成消息、switch_session 重绑），语义是“迁移”不是“桌面遥控”，复用会注入假消息、用错 prompt。

→ 得出正确方向：需**新建 send-from-desktop 小桥**，复用 `_handle_message` + `adapter.send`，经共享 `state.db` 信号中转，绝不走 `chat.py`。

---

## 4. AI 独立查证 → 推翻“顺序坑承重项”（此处出现误判，见 §6 修正）

为给步骤 3 定稿，独立查证“web 会话是否已在 state.db”：

- 全仓 `create_session(` / `append_message(` 调用点：`cli.py`、`gateway/*`、`tui_gateway`、`acp_adapter`、`conversation_compression` —— **无一个在 web/桌面进程（vermes_cli）**。
- `vermes_cli/blueprints/session.py` 仅对 `state.db` **只读**（list/get_messages/search/delete），无 POST 创建。
- `chat.py` 对 `state.db` **零引用**。

> ⚠️ **本步得出“web 已在 state.db、顺序坑被推翻”是错误结论**（把“web 已进 `memory_index.db`（记忆层，真）”与“web 已进 `state.db`（会话层，假）”混淆）。真实结论见 §6。

---

## 5. WorkBuddy 第二轮自我纠错（记忆层）

重新查证记忆摄入链路，纠正初案的“记忆盲区”前提：

- 记忆摄入**与 `state.db` 解耦**：发生在 agent 主循环 `_sync_external_memory_for_turn`（`run_agent.py:2643` ← `conversation_loop.py:875`）→ `memory_manager.sync_all` 写 `memory_index.db`。
- web 后端 `chat.py:1287/1450/1665` 直接调 `agent.run_conversation`，且 `chat.py:955` 建 agent 未传 `skip_memory` → **web 对话早已逐轮进记忆库**。
- 因此“统一到 `state.db` 让记忆素材翻倍”**不成立**；记忆层本就跨渠道统一。`state.db` 统一对记忆是**中性偏正、间接增益**。
- scope 污染是**既有债且与 `state.db` 无关**（per-turn sync 走全局 `''`），治理动机应定为“防污染保涌现”，scope 轴从 session 改 channel。

---

## 6. 最终事实链（经 §4 误判修正后）

```
对话输入
 ├─ 所有渠道 → run_conversation 循环 → memory_index.db      ← 记忆摄入（早已跨渠道统一，与 state.db 无关）
 └─ 渠道会话(TG/飞书/...) → SessionDB(state.db)             ← 会话存储（web 桌面会话【不在】此，需步骤2补）
        ↕ 共享 state.db（跨进程信号）
   桌面控制台 ──send-from-desktop 桥──▶ gateway 进程 ──_handle_message+adapter.send──▶ 渠道回复
```

**关键修正（推翻 §4 的误判）**：web/桌面会话**当前不在 `state.db`**。“先让 web 落 `state.db` 再切读源”的**顺序坑仍然成立**，未被推翻。

---

## 7. 5 条被推翻 / 修正的认知清单

| # | 原认知 | 修正后（查证结论） | 性质 |
|---|---|---|---|
| 1 | Web 会话是记忆盲区，统一到 `state.db` 素材翻倍 | 记忆摄入在 agent 循环层，web 早已进 `memory_index.db`，与 `state.db` 无关；统一不增加记忆源 | **推翻** |
| 2 | web 会话已在 `state.db`，顺序坑被推翻 | web/桌面会话**不在** `state.db`（vermes_cli 零写路径）；顺序坑**仍然成立**，需步骤 2 补落库 | **推翻（反向修正）** |
| 3 | 带 telegram session_id 发 `/api/chat/completions` 即可续聊 | web 与 gateway 是不同进程，`chat.py` 不桥接渠道；必须走 gateway `_handle_message` | **推翻** |
| 4 | 复用 `handoff` 机制做桌面控渠道 | `handoff` 是 CLI→渠道迁移、注入合成消息；需新增 `_process_desktop_relay` 分支，携带真实文本 | **推翻** |
| 5 | 统一后 scope 污染放大，需按渠道隔离 | 污染是既有债、与 `state.db` 无关；治理动机改“防污染保涌现”，scope 轴从 session 改 channel | **修正** |

---

## 8. 待你最终审计拍板

- 步骤 3 技术方案是否采纳？（设计文档已出，含 2 个开放问题：复用 `handoff` 扩列 vs 新建 `desktop_outbox` 表）
- 执行顺序：先低风险读闭环（1→2→3→4）还是先 3 验证写闭环（TG 已在其 db，可独立验证）？
- 记忆 scope 治理（步骤 4）纳入本路线还是独立 PR？
- **第 2 条修正需你确认**：web 会话不在 `state.db`，顺序坑仍在——这直接影响步骤 2 是否为承重项。
