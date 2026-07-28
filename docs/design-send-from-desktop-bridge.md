# 步骤 3：`send-from-desktop` 桥 — 桌面端代发渠道消息（完整技术方案）

> 状态：设计草案（待用户审计拍板）
> 日期：2026-07-28
> 配套文档：`plan-desktop-channel-unification.md`（4 步路线）、`report-desktop-channel-unification-discussion.md`（讨论过程与认知修正）

---

## 1. 问题定义

桌面控制台（Electron Web UI，由 `hermes_cli` 后端服务）要能**代用户**向某个**已存在于 state.db 的渠道会话**（telegram / feishu / …）发一条**真实用户消息**，并由该渠道的 bot **真正回复**，且回复**同时**回到：

1. 原渠道用户（TG / 飞书里那个人能收到）；
2. 桌面控制台（轮询显示）。

硬约束：

- 回复**必须经过该渠道的 `adapter.send`**——否则原渠道用户收不到，等于没用。
- 桌面发起的消息**必须进入记忆摄入路径**（走 gateway 进程的 `run_conversation`），否则自进化框架看不到这条对话。
- **绝不能走 web 后端的 `chat.py`**——它在 web 进程跑 agent、不桥接渠道、`adapter.send` 不会被调用，回复回不到 TG。

---

## 2. 架构事实（代码证据）

| 事实 | 证据 |
|---|---|
| 桌面 web 进程 ≠ gateway 进程 | `hermes_cli/blueprints/chat.py:1287/1450/1665` 在 web 进程跑 `run_conversation`；gateway 各渠道在 gateway 进程跑 `_handle_message`（`gateway/message_handler_mixin.py:1200/1498`）。两进程**只通过共享 `state.db` 通信**，非内存。 |
| 渠道会话写入 state.db | `gateway/session.py:982/1207` `create_session`，`:1298` `append_message`；`gateway/slash_handlers/session_handlers.py:295/447` `create_session`，`:460` `append_message`。 |
| handoff 跨进程信号机制现成 | `hermes_state.py` 提供 `list_pending_handoffs` / `claim_handoff` / `complete_handoff`；`gateway/watcher_mixin._handoff_watcher` 轮询 pending。可白嫖为桌面→gateway 的信号通道。 |
| **web/桌面会话当前不在 state.db（关键）** | `hermes_cli` 全仓**无任何** `db.append_message` / `db.create_session` 调用；`blueprints/session.py` 仅对 state.db **只读**；`chat.py` 对 state.db **零引用**。web 消息落在 `~/.vermes/messages/*.json` + 浏览器 IndexedDB，**不是** state.db。→ 本桥**只覆盖已在 state.db 的渠道会话**（TG/飞书等）；要让桌面也能 relay web 会话，需先做步骤 2（web 落 state.db）。 |
| 记忆摄入与 state.db 解耦 | `_sync_external_memory_for_turn`（`run_agent.py:2643` ← `conversation_loop.py:875`）逐轮写 `memory_index.db`，与 state.db 无关。只要消息走 gateway `run_conversation`，记忆**自动摄入**。 |
| 桌面 session token 已存在 | `hermes_cli/web_server.py:122` `_load_or_create_session_token()`、`web_server.py:143` `_SESSION_TOKEN` —— 可复用为 relay 端点的防伪造凭证。 |

---

## 3. 桥设计

### 3.1 信号通道：复用 `handoff_state`，扩列携带真实文本

- 在 `handoff_state` 表新增列：`relay_text TEXT`、`relay_source TEXT`（值 `'desktop'`）、`desktop_token TEXT`、`claimed_at REAL`、`expire_at REAL`。
- 一条 `relay_source='desktop'` 的 pending 记录 = “桌面请求 gateway 代发一条真实消息到某渠道会话”。
- 备选：新建 `desktop_outbox` 表（见 §7 开放问题 A）。本设计默认**复用扩列**，改动最小。

### 3.2 新端点（web 后端进程）

`POST /api/sessions/{session_id}/send-from-desktop`

1. ~~校验请求头 `X-Desktop-Token` 与启动时 `_SESSION_TOKEN` 一致 → 否则 `403`~~ **【实现勘误】**：最终实现复用全站 `X-Hermes-Session-Token` auth_middleware（`/api/sessions/*` 非公开路径强制校验），缺失/伪造返回 **401**（中间件层），未新增独立 `X-Desktop-Token` 头。`source='web'` 拒绝 = **400**（端点层）；relay 防重放 = **409**。
2. 解析 `session_id`，确认它在 state.db 中存在且 `source ∈ {telegram, feishu, discord, slack, …}`（**拒绝 `source='web'`**，避免绕回 web 进程形成环路）。
3. 把真实用户消息写入共享 state.db：
   ```python
   db = SessionDB()
   db.create_session(session_id, source, ... )   # 幂等，已存在则跳过
   db.append_message(session_id, "user", text)    # 真实消息落库（这就是"桌面写真实消息"的实现路径）
   ```
4. 写入一条 pending relay 信号：
   ```python
   write_handoff(session_id, status="pending", relay_text=text,
                 relay_source="desktop", desktop_token=token,
                 expire_at=now()+300)
   ```
5. 返回 `{ok: true, relay_id}`，**不阻塞等回复**。

### 3.3 gateway 消费分支（gateway 进程）

在 `gateway/watcher_mixin._handoff_watcher` 轮询循环内新增 `_process_desktop_relay` 分支：

1. 取 `status='pending'`、`relay_source='desktop'`、未过期的记录。
2. `claim_handoff(relay_id)` 加锁（幂等，防并发重复消费）。
3. **还原该 session 的渠道 source（核心，见 §3.3.1）**：
   ```python
   chan_source = self._find_source_by_session_id(session_id)
   if chan_source is None:
       complete_handoff(relay_id, status="failed", error="session source not resolvable")
       return
   ```
4. 构造内部消息事件并注入 gateway 进程执行：
   ```python
   event = MessageEvent(text=relay_text, source=chan_source, internal=True)
   await self._handle_message(event)   # 已支持 internal=True + event.source
   ```
   - `internal=True` 跳过授权；`event.source` 携带**原渠道的 chat_id**，使 `_handle_message` 内部走到该渠道的 `adapter.send` 把**助手回复发回原渠道用户**，并把 assistant 消息 `append_message` 进 state.db。
   - **不需要修改 `_handle_message` 签名**（实测 :94 已接受 `MessageEvent`，从 `event.source` 取 source，`:111` 已读 `event.internal`）。
5. `complete_handoff(relay_id)`。
6. **绝不经 `chat.py`**（web 进程）。

#### 3.3.1 `_find_source_by_session_id` —— 还原渠道 source 的科学路径（家底审计结论）

**问题**：`_process_desktop_relay` 知道 `session_id`，但 gateway 消费时需要原渠道的 `chat_id`/`platform` 才能构造 `SessionSource` 并 `adapter.send` 回原渠道。需要可靠的还原路径。

**家底审计结果**（2026-07-28 查证）：

| 候选路径 | 结论 |
|---|---|
| A. 读 `state.db.sessions.chat_id` 字段 | ❌ **当前不可靠**。gateway **主创建路径 `gateway/session.py:967-971`（get_or_create_session）及 rotate 路径 `:1207` 均只传 session_id/source/user_id**（session_handlers.py:295/:447 只是次要路径）；chat_id/chat_type/thread_id/session_key/origin_json 列是 schema-diff 自动迁移（hermes_state.py:624-669）加的，基础 DDL（:308-340）无。本机实测（2026-07-28）：cli 197/197、web 2/2 全 NULL；feishu 22/23、telegram 10/11 NULL——**各有 1 条非 NULL 且带完整 origin_json**，系跑过含上游 #58899 代码的构建时写入（见 §3.5） |
| B. 从 gateway `session_store._entries` 反查 | ✅ **最可靠、零改动**。SessionStore 从 `sessions.json` 全量加载（`gateway/session.py:736-744`），`SessionEntry.origin` 为完整 `SessionSource`（`session.py:956`，字段名是 **`origin` 不是 `source`**）。⚠️ 边界：`prune_old_entries`（`session.py:1068`）按 `session_store_max_age_days`（默认 **90 天**，config.py:517）清理——超龄会话不在 `_entries`，必须走兜底 |
| C. 像 `_process_handoff` 用 `handoff_platform`+config home channel | ❌ 语义不对（home channel ≠ 该会话原本 chat_id），排除 |

> 既有先例：`gateway/session_mixin.py:451-480`（关机通知）就是同款还原级联 `entry.origin → 缓存 → _parse_session_key(session_key)`，可直接参考其防御式写法。

**采用路径 B（主） + 路径 A 回填（补强）**：

```python
# gateway/message_handler_mixin.py 或新 helper

def _find_source_by_session_id(self, session_id: str):
    """按 session_id 还原原渠道 SessionSource（用于桌面 relay）。

    主路径：遍历 gateway SessionStore（sessions.json 权威索引，覆盖全部历史
    会话，不限于内存活跃）。兜底：state.db.get_session 读 chat_id 重建最小
    SessionSource。再无则返回 None（拒绝 relay）。
    """
    # 主路径：SessionStore 内存索引（从 sessions.json 恢复，含完整 origin）
    store = getattr(self, "session_store", None)
    if store is not None:
        store._ensure_loaded()
        for entry in store._entries.values():      # O(n) 遍历，n=会话数，可接受
            if entry.session_id == session_id:
                return entry.origin                  # ⚠️ 字段名是 origin（session.py:956），不是 source
    # 兜底 1：state.db origin_json（上游 #58899 起写入，完整 SessionSource.to_dict）
    # 兜底 2：state.db chat_id 列重建最小 SessionSource
    try:
        row = SessionDB().get_session(session_id)
        if row:
            if row.get("origin_json"):
                return SessionSource.from_dict(json.loads(row["origin_json"]))
            if row.get("chat_id"):
                return SessionSource(
                    platform=Platform(row["source"]),
                    chat_id=row["chat_id"],
                    chat_type=row.get("chat_type") or "dm",
                    thread_id=row.get("thread_id"),
                    user_id=row.get("user_id"),
                )
    except Exception:
        logger.debug("desktop relay: state.db fallback failed for %s", session_id)
    return None
```

**边界**：
- `store._entries` 未加载时调 `_ensure_loaded()`（已有）。
- 若 gateway 从未运行过该渠道（极端），`_entries` 无此 session_id 且 state.db 也无 chat_id → 返回 None → relay 标记 failed 并提示“该渠道未连接/需先在该渠道收发一次消息”。
- 遍历 O(n) 可接受（会话数通常 < 几千）。

### 3.5 家底审计修复 —— 让 state.db 成为权威真相源（修正：本仓与上游已大幅分叉，不可直接 merge）

**背景（2026-07-28 查证）**：上游 `upstream/main`（`NousResearch/hermes-agent`）的 **#58899 `747386ecf`** 已完整实现“gateway 创建 session 时写 `chat_id`/`chat_type`/`thread_id`/`display_name`/`origin_json` 进 state.db”。本机 DB 里 telegram/feishu 各 1 条带完整 origin_json 的非 NULL 行，即是曾跑过含该代码构建的证据。

**但“merge/cherry-pick 上游”在本仓不可行**，量化证据：
- 本仓 main `hermes_state.SCHEMA_VERSION = 14`；`upstream/main = 23`（**差 9 个版本**）。
- 本仓 main 落后 `upstream/main` **17,726 个 commit**，分叉点已不可查（早已大幅分叉）。
- 该 commit 改动面 9 文件 +672/-82（含 `gateway/run.py`/`mcp_serve.py`/`mirror.py`/`status.py`），是为**上游自己的架构**写的会话发现重构。本仓这些文件已被 v2.3.x 大量定制，**整体 cherry-pick 会把这些上游重构一并拉入，破坏本仓现有会话发现逻辑**。
- 故：**禁止 `git merge upstream/main`**（灾难）；整段 cherry-pick 风险极高，不推荐。

**三个可行选项**：

| 选项 | 做法 | 风险 | 推荐 |
|---|---|---|---|
| **A. 自研最小移植（对齐上游格式）** | 仅在本仓 `create_session`/`record_gateway_session_peer`（或本仓等价函数）补传 `chat_id`/`chat_type`/`thread_id`/`display_name`/`origin_json`，且 **`origin_json` 严格写 `SessionSource.to_dict()` 的 JSON**（与上游 #58899 格式一致）。存量回填脚本遍历 `_entries` 写回。 | 低。只动写入路径，不触架构 | ✅ **推荐** |
| B. 完整 cherry-pick 747386ecf+75099ca0e | 解决冲突，接受引入上游会话发现重构 | 高。可能 destabilize 本仓现有 gateway | ❌ 不推荐 |
| C. 暂不做 | 步骤 3 主路径 B（entry.origin 遍历 _entries）已 90 天内可用；兜底读本仓自己补传的 origin_json；超龄会话用“提示用户该渠道需先收发一次”缓解 | 零。但 90 天边界无根治 | 可过渡 |

**采用选项 A**（推翻原版“勿自研”结论——因整段 merge 不可行，自研最小移植且对齐上游 `origin_json` 格式才是正确解，未来若真合上游也不分叉）。

**关键约束（2026-07-28 二次查证修正）**：
1. **`origin_json` 写法**：与上游逐字相同——`json.dumps(source.to_dict())`（上游 `gateway/session.py:1844`）。直接调本仓 `SessionSource.to_dict()`，**别手拼 JSON**。注意上游 `to_dict()` 已演进出 `scope_id`/`guild_id` 双写（D-Q2.5 迁移），本仓旧版只发 `guild_id`；上游读取端保留 `guild_id` 别名兼容，所以本仓格式天然向前兼容，无需仿造 `scope_id`。
2. **必须同时补写 `session_key` 列**：上游 `find_session_by_origin`（upstream hermes_state.py:3699）是**纯列匹配**（`LOWER(source)=? AND chat_id=? AND session_key IS NOT NULL` + thread_id/user_id 过滤），**不解析 origin_json**。只补 chat_id 不补 session_key 的行，该函数永远匹配不到。真正解析 origin_json 的消费者是上游 `channel_directory.py:400`（渠道目录还原）和 `gateway/session.py:1637`（Slack workspace 越界防护）。
3. **存量回填同理**：`session_key`（即 `_entries` 的 key 本身）+ `chat_id`/`chat_type`/`thread_id`/`display_name`/`origin_json` 一起回填，缺 session_key 的回填是无效回填。

- **存量回填**：一次性脚本，遍历 `session_store._entries`，把 `entry.origin.to_dict()` 写回 `origin_json` + chat_id 等列（幂等 UPDATE，COALESCE 只补 NULL）。
- **效益**：relay 兜底直接读 origin_json 反序列化完整 SessionSource（§3.3.1 兜底 1）；且解决路径 B 的 90 天 prune 边界（超龄会话仍可从 state.db 还原）。
- **排期**：独立 PR，不阻塞步骤 3（主路径 B 已不依赖）。

---
### 3.4 前端轮询（桌面控制台）

- 发完 relay 后，对 `{session_id}` 轮询 `GET /api/sessions/{id}/messages`。
- 检测由 gateway 写入 state.db 的**新增 assistant 消息**（按 `message_id` / 时间戳比对发前快照）并显示。
- 轮询间隔 ~1s，直到看到对应回复或触发超时护栏（§5）。

---

## 4. 改动清单

| 模块 | 改动 |
|---|---|
| `hermes_state.py` | `handoff_state` 扩列（`relay_text`/`relay_source`/`desktop_token`/`claimed_at`/`expire_at`）；或新建 `desktop_outbox` 表 + 对应 `list/claim/complete` 函数。 |
| `hermes_cli/blueprints/session.py`（或新 blueprint） | 新增 `POST /api/sessions/{id}/send-from-desktop`。 |
| `hermes_cli/web_server.py` | 复用 `_SESSION_TOKEN` 做 `X-Desktop-Token` 校验（端点装饰器）。 |
| `gateway/watcher_mixin.py` | `_handoff_watcher` 增加 `_process_desktop_relay` 分支。 |
| `gateway/message_handler_mixin.py` | 新增 `_find_source_by_session_id()` 辅助函数（§3.3.1，遍历 `session_store._entries` 按 session_id 取 source）。`_handle_message` **无需改签名**（已支持 `internal=True` + `event.source`）。 |
| `gateway/slash_handlers/session_handlers.py` | （家底审计修复，可选）`create_session` 调用补传 `chat_id`/`chat_type`/`thread_id`（§3.5）。 |
| 前端（桌面控制台） | 会话内“发消息”入口 + 轮询显示逻辑 + 超时/失败态 UI。 |
| 依赖：步骤 2 | web 会话落 state.db，否则 web 会话在统一视图不出现、且不可被 relay。 |

---

## 5. 风险护栏

- **强制 gateway 进程执行**：relay 信号只由 gateway 消费；web 后端只写信号、不跑 agent。gateway 未运行时 relay 永不消费 → 触发超时回退。
- **桌面 session token 防伪造**：实现复用全站 `X-Hermes-Session-Token` auth_middleware，伪造/缺失请求返回 **401**（见 §3.2 勘误；设计初稿的 `X-Desktop-Token`/403 方案未采用）。
- **超时回退**：`expire_at` 过期未被 claim → 端点/前端标记“发送失败，渠道未响应”，可重试；gateway `claim` 后亦校验未过期。
- **只 relay 渠道会话**：端点拒绝 `source='web'` 的 `session_id`，并校验 `session_id` 存在于 state.db，杜绝双写/环路。
- **防重放**：`claim_handoff` 幂等，同一 `relay_id` 只处理一次。
- **失败开放（fail-open）**：写信号 / 消费任一环节异常均 `try/except` 吞掉、不阻断渠道正常消息流。

---

## 6. 验证计划

- **单测**：relay 信号写入 / `claim` / `complete` 幂等；`X-Desktop-Token` 拒绝伪造；过期回退正确。
- **集成（TG）**：桌面对一条 telegram 会话发 `ping` → gateway 经 `adapter.send` 把回复发回 TG 用户 → state.db 出现 `user(ping)` + `assistant(...)` 两行 → 桌面轮询看到回复。
- **记忆回归**：该轮对话出现在 `memory_index.db`，验证摄入路径生效。
- **回归红线**：普通 TG / 飞书消息流不受影响；web `chat.py` 路径不受影响；`handoff` 既有 CLI→渠道迁移语义不被 relay 记录干扰（`relay_source` 过滤）。

---

## 7. 开放问题

**A. 复用 `handoff_state` 扩列 vs 新建 `desktop_outbox` 表？**
- 复用扩列：改动小、直接复用 `_handoff_watcher` 轮询；需确保既有 handoff 消费者不被 `relay_source='desktop'` 记录干扰（加 `relay_source` 过滤即可）。
- 新建 `desktop_outbox`：语义清晰、零污染；但需新建表 + 独立轮询或并入 `_handoff_watcher`。
- **倾向：复用扩列**（小改动），除非审计发现既有 handoff 消费者难以加过滤。

**B. web 会话 relay 范围？**
- 本桥默认 scope = 已在 state.db 的渠道会话。要让桌面也能 relay **web** 会话，需步骤 2（web 落 state.db）先落地；文档注明：未落地前 web 会话不可 relay。

**C. `_process_desktop_relay` 如何还原渠道 source？（已查清，见 §3.3.1）**
- 主路径：遍历 gateway `session_store._entries`（从 `sessions.json` 加载，覆盖全部历史会话），按 `session_id` 取 `entry.source`（完整 chat_id）。零 schema 改动、立即可用。
- 兜底：state.db `get_session` 读 chat_id 重建最小 `SessionSource`。
- 已排除：读 state.db.chat_id 为主路径（gateway 创建时未写该列，存量 NULL）；用 config home channel（语义不对）。
- 家底审计修复：让 gateway 创建 session 时回填 chat_id 进 state.db（§3.5），长期让 state.db 成为权威真相源。

---

## 8. 关键决策记录（供拍板）

- 复用 handoff 的 state.db 信号机制（轮询 pending），新增 `relay_text` / `relay_source` 携带真实文本。
- gateway 侧加 `_process_desktop_relay` 分支，走 `_handle_message(internal=True, text=真实)`，**绝不走 `chat.py`**。
- 护栏三件套：强制 gateway 进程执行 + 桌面 session token 防伪造 + 超时回退。
- web 会话 relay 依赖步骤 2，非本方案内闭环。
