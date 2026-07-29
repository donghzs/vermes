# 桌面端全渠道统一 — 综合路线方案

> 日期：2026-07-28
> 状态：路线草案（待用户拍板执行顺序与步骤 4 归属）
> 配套：`design-send-from-desktop-bridge.md`（步骤 3 方案）、`report-desktop-channel-unification-discussion.md`（讨论与认知修正）

---

## 0. 核心结论速览（**修正版**，供审计）

| 事实 | 判定 | 证据 |
|---|---|---|
| `state.db` 全渠道唯一真相源（渠道侧） | ✅ 证实 | `gateway/session.py:982/1207/1298`、`session_handlers.py:295/447/460` 写库 |
| Web/桌面对话早已进记忆库（记忆与 `state.db` 正交） | ✅ 证实 | `chat.py:1287/1450/1665` 跑 `run_conversation` → `_sync_external_memory_for_turn`（`run_agent.py:2643`）写 `memory_index.db` |
| 续聊写闭环必须走 gateway 进程（`adapter.send` 回渠道） | ✅ 证实，不能用 `chat.py` | `chat.py` 对 `state.db`/渠道零引用；`_handle_message` 在 gateway 进程（`message_handler_mixin.py:1200/1498`） |
| 桌面 web 进程 ≠ gateway 进程（跨进程，共享 `state.db` 中转） | ✅ 必须 | 两进程仅经共享 `state.db` 通信 |
| **web 会话已在 `state.db`（无需补落库“承重项”）** | ❌ **误判，已推翻** | `vermes_cli` 全仓无 `db.append_message/create_session`；`session.py` 仅读；web 消息落 `~/.vermes/messages/*.json` + IndexedDB |
| scope 污染是既有债，治理动机“防污染保涌现” | ✅ 修正 | per-turn sync 走全局 `''`（`memory_fabric.py:145`），与 `state.db` 无关 |

> ⚠️ 你速览中第 5 行“web 会话已在 state.db”**不成立**——这是把“web 已进 `memory_index.db`（记忆层）”与“web 已进 `state.db`（会话层）”混淆。**顺序坑（步骤 2）仍然成立，是承重项。**

---

## 1. 四步路线

### 步骤 1 — 统一视图（读闭环，低风险）
- 桌面控制台会话列表 / 消息读取数据源从 `/api/gui/sessions`（扫 `~/.vermes/messages/*.json`）切到 `/api/sessions` + `/api/sessions/{id}/messages`（读 `state.db`）。
- 覆盖：TG / 飞书 / discord / slack 等已在 `state.db` 的渠道会话。
- **不覆盖**：web 会话（不在 `state.db`，需步骤 2）。
- 风险：低（纯读路径，失败可回退原数据源）。

### 步骤 2 — web 会话落 `state.db`（**承重项**，补顺序坑）
- 在 web 进程 `chat.py` 起会话 / 每轮消息时调用 `SessionDB().create_session(id, source='web')` + `append_message(id, role, content)`。
- **顺序铁律**：先让 web 落 `state.db`，**再**把步骤 1 的读数据源切到 `/api/sessions`，否则老 web 会话在统一视图里消失。
- 工作量：中（需处理幂等、消息去重、与现有 `~/.vermes/messages/*.json` 双写兼容）。

### 步骤 3 — `send-from-desktop` 桥（写闭环）
- 完整方案见 `design-send-from-desktop-bridge.md`。
- 复用 `handoff_state` 信号机制（扩列 `relay_text`/`relay_source`/`desktop_token`/`expire_at`），新增端点 `POST /api/sessions/{id}/send-from-desktop`，gateway 加 `_process_desktop_relay` 分支走 `_handle_message(internal=True)`，前端轮询显示。
- 对**渠道会话**（TG/飞书）**不依赖步骤 2**即可验证；web 会话 relay 需步骤 2 落地。

### 步骤 4 — 记忆 scope 治理
- 在 `memory_fabric` 摄入点按 channel/上下文推导 `scope`（非既有 session_id scope）；recall 默认聚合所有 scope 保涌现、对当前渠道加权。
- 动机：“防污染保涌现”，非“补盲区”。
- 与前端统一**正交**，可独立 PR。

---

## 2. 执行顺序建议

**推荐：1 → 3（TG 验证）→ 2 → 4**

- 步骤 1（读）与步骤 3（TG 写闭环）可先组成“全渠道统一”的最小可用闭环——TG 会话已在 `state.db`，无需等步骤 2 即可端到端验证“桌面看 + 桌面发 + 渠道回复”。
- 步骤 2 专门把 web 会话纳入统一视图与 relay 范围（承重但独立）。
- 步骤 4 独立收尾（或拆 PR）。

**备选：1 → 2 → 3 → 4**（严格先低风险读、再补 web 落库、再写闭环）——更稳但 TG 验证被推迟到步骤 2 之后。

> 回答你的问题：建议**先 1→3 用 TG 验证写闭环**（渠道会话已在 db，零阻塞），同时并行推进步骤 2；不要因为“等步骤 2”而拖住步骤 3 的验证。

---

## 3. 工作量估算（~4.5–6.5d）

| 步骤 | 内容 | 估时 |
|---|---|---|
| 1 | 前端读数据源切换 + 回归 | 0.5–1d |
| 2 | web 落 `state.db`（幂等/去重/双写兼容） | 1–1.5d |
| 3 | send-from-desktop 桥（端点 + gateway 分支 + 前端轮询 + 护栏） | 2–3d |
| 4 | scope 治理（摄入推导 + recall 加权） | 1–1.5d |
| — | 缓冲（联调/边界） | 0.5–1d |
| **合计** | | **4.5–6.5d** |

---

## 4. 回归测试清单

- [ ] 普通 TG / 飞书消息流行为不变（端到端）。
- [ ] web `chat.py` 路径不变（记忆仍摄入，渠道回复不串）。
- [ ] `handoff` 既有 CLI→渠道迁移语义不被 relay 记录干扰（`relay_source` 过滤）。
- [ ] 桌面统一视图：列表/消息读自 `state.db`，频道标记正确。
- [ ] 步骤 3 集成：桌面对 TG 会话发消息 → 原渠道用户收到回复 + 桌面轮询看到回复 + 记忆库出现该轮。
- [ ] 步骤 2：web 会话出现于统一视图，老 web 会话不丢失。
- [ ] 记忆 scope：跨渠道涌现仍在，闲聊不污染工作上下文。

---

## 5. 风险登记

| 风险 | 影响 | 概率 | 缓解 |
|---|---|---|---|
| 步骤 2 顺序错（先切读源后落库） | 老 web 会话在视图消失 | 中 | 严格先落库再切读源；切源加灰度开关 |
| `handoff_state` 复用污染既有 handoff 消费者 | CLI→渠道迁移异常 | 低 | `relay_source` 过滤；单测覆盖 |
| relay 端点被伪造调用 | 代发垃圾消息 | 中 | `X-Desktop-Token` 校验；拒绝 `source='web'` |
| gateway 未运行 → relay 死信 | 桌面发消息无回复 | 中 | `expire_at` 超时回退 + 前端失败态 |
| web 双写（`state.db` + `~/.vermes/messages/*.json`）不一致 | 消息重复/丢失 | 中 | 幂等 `create_session` + `append_message` 去重 |
| 桌面轮询风暴 | UI 卡顿/DB 压力 | 低 | 轮询间隔 1s + 快照比对 + 停止条件 |

---

## 6. 待拍板

1. 步骤 3 方案采纳？（含开放问题 A：复用 `handoff` 扩列 vs 新建 `desktop_outbox`）
2. 执行顺序：1→3(TG)→2→4 还是 1→2→3→4？
3. 步骤 4（scope 治理）纳入本路线还是独立 PR？
4. **确认第 0 节修正**：web 会话不在 `state.db`，步骤 2 为承重项——是否认可？
