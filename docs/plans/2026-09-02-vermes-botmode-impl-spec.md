# Vermes Bot Mode 实现方案（③ 群聊多智能体社会）

> 依据：用户审阅《Vermes-upstream-catchup-roadmap.md》后意见 + 真源实测锚点
> 真源：`/Users/dongzusheng/Projects/vermes-electron`（HEAD `d634478778` / v2.4.7），**只读不写**
> 纪律：纯增量、不破现有链路；表/列走 `vermes_state.py` 的 `SCHEMA_SQL` + `_reconcile_columns()` 自动迁移；改动由用户 commit/push
> **⚠️ 权威性（2026-09-02 通篇审计后加）**：本文件是 **③ 的可 apply 实现规格**，`Vermes-catchup-roadmap-final.md` 是**唯一权威**。冲突时以路线图为准；数字口径不同须标注换算（路线图「文档契约」第 2 条）。
> **同步状态**：已同步至路线图 **vFINAL.2**。本轮修订 **P0**：**`agent_profiles` 补齐 `provider` / `model` / `skill_set` / `transport` 等异构列**——旧稿缺失，照旧稿开发会退化成"同一 `AIAgent` 换 persona"的**同源多实例**，与路线图要求的「异构 Agent 联邦 / 神魔堂」相悖。另修正 §5 落地顺序表述、A2A 底座描述、RoomIdNormalizer 引用。

---

## 0. 设计原则（先定调，避免返工）

1. **房间是「会话派生层」，不是新的缓存维度。**
   现有两套 agent 缓存都是按 `session_id` 1:1：
   - 网关级 `gateway/run.py:1441` → `self._agent_cache: "OrderedDict[str, tuple]"`，key=`session_key`
   - Web/CLI 级 `vermes_cli/blueprints/agent_cache.py:107` → `_AgentCache(maxsize=20)`，key=`f"{provider}:{model}:{_session_id}"`（见 `chat.py:1210`）
   
   **关键决策**：Bot Mode 不改造 `_agent_cache` 内部。一个房间 = N 个 `(room_id, agent_profile_id)` 组合，每个组合派生出一个独立 `session_id`，每个 `session_id` 仍 1:1 命中缓存。**房间只是把这些 session 在 UI 上归并展示**，既保留 prefix-cache/LRU/TTL，又把"1:N"风险降到零。

2. **每个 agent 在自己的 session 里有独立记忆与上下文。**
   房间内 `@A` 和 `@B` 是两条互不干扰的会话，各自带记忆、各自压缩、各自演进。这是和 Hermes「桌面内群聊」的差异化落脚点——Vermes 还能把同一房间映射到飞书/钉钉群。

3. **两期拆分（用户意见采纳）**
   - **Phase 1（v2.5.0）**：桌面内单房间多 agent 群聊（最小可用）
   - **Phase 2（v3.0.0）**：跨渠道群聊（飞书/钉钉/企微群里的多 agent 协同）

---

## 1. 数据模型（Phase 1 + Phase 2 共用，落在 `vermes_state.py`）

按项目规矩：新表直接写进 `SCHEMA_SQL`（约 `vermes_state.py:498` 附近的 `CREATE TABLE` 区块），用 `CREATE TABLE IF NOT EXISTS`；索引在 `_reconcile_columns()` 调用之后（`~988-991`）用 `CREATE INDEX IF NOT EXISTS` 建。引擎启动即自动建表/加列，无需手写迁移。

```sql
-- ── Bot Mode：agent 名册（可 @mention 的 agent 画像）──
-- 🔴 异构列（2026-09-02 P0 修复）：路线图 §5 ③ 要求"每个 bot 可挂不同 LLM / 不同技能包 / 或干脆是外部 agent"。
-- 旧稿缺这些列 → 照旧稿实现会退化成"同一 AIAgent 换 persona"的同源多实例，与「异构 Agent 联邦 / 神魔堂」定位相悖。
CREATE TABLE IF NOT EXISTS agent_profiles (
    id              TEXT PRIMARY KEY,          -- 唯一 slug，如 "researcher" / "coder"
    name            TEXT NOT NULL,             -- 显示名，如 "研究助手"
    description     TEXT,                      -- 一句话能力描述（名册卡片用）
    system_prompt   TEXT,                      -- 该 agent 的 system prompt 覆写（可为空=用默认）
    toolsets        TEXT,                      -- 启用的 toolset 列表，JSON 数组字符串
    avatar_seed     TEXT,                      -- 头像生成种子（见 §3.3）
    hue             INTEGER DEFAULT 0,         -- 头像色相 0-360
    is_default      INTEGER DEFAULT 0,         -- 无 @mention 时的默认应答 agent
    -- ── 异构支持（🆕 本轮补齐，路线图 §5 ③ / §10）──
    provider        TEXT,                      -- LLM provider 覆写（anthropic / openai / deepseek …）；空=用全局默认
    model           TEXT,                      -- 模型覆写（claude-opus-4 / gpt-5 / deepseek-chat …）；空=用全局默认
    skill_set       TEXT,                      -- 挂载的技能包列表，JSON 数组字符串（复用 Skills Hub）
    transport       TEXT DEFAULT 'native',     -- 'native'=Vermes 本地 AIAgent；'adapter'=外部 agent，走 ① 的 transport adapter registry
    transport_ref   TEXT,                      -- transport='adapter' 时的适配器标识（codex / claude_code / coze / doubao …）
    capability_tags TEXT,                      -- 能力标签 JSON 数组（code/search/writing/vision…），供按标签派活（路线图 §5 ①「能力发现」）
    created_at      REAL
);

-- ── Bot Mode：房间 ──
CREATE TABLE IF NOT EXISTS bot_rooms (
    id              TEXT PRIMARY KEY,          -- room_id（桌面房间 / 渠道 group id）
    title           TEXT,
    channel         TEXT DEFAULT 'desktop',    -- desktop | feishu | dingtalk | weixin | ...
    source_group_id TEXT,                      -- 跨渠道时：渠道群原始 id
    created_at      REAL,
    updated_at      REAL
);

-- ── Bot Mode：房间成员（人类 + agent 混合）──
CREATE TABLE IF NOT EXISTS bot_room_members (
    room_id         TEXT NOT NULL,
    member_type     TEXT NOT NULL,             -- 'human' | 'agent'
    ref_id          TEXT NOT NULL,             -- human=user_id；agent=agent_profiles.id
    joined_at       REAL,
    PRIMARY KEY (room_id, member_type, ref_id)
);

-- ── Bot Mode：房间消息（统一时间线，按 author 渲染）──
CREATE TABLE IF NOT EXISTS bot_room_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         TEXT NOT NULL,
    author_type     TEXT NOT NULL,             -- 'human' | 'agent' | 'system'
    author_ref      TEXT,                      -- human=user_id；agent=agent_profiles.id
    content         TEXT,
    created_at      REAL,
    turn_session_id TEXT                      -- 该条消息由哪个派生 session 产生（追链路用）
);
CREATE INDEX IF NOT EXISTS idx_bot_room_msg_room ON bot_room_messages(room_id, created_at);
CREATE INDEX IF NOT EXISTS idx_bot_rooms_channel ON bot_rooms(channel, source_group_id);
```

> 注：`agent_profiles` 建表后需 seed 至少 2 个默认 agent（如 `researcher` / `coder`），否则房间只有人类、无意义。可在 `_reconcile_columns()` 之后补一段「若 `agent_profiles` 为空则插入默认 2 条」的 idempotent 初始化。
>
> **🔴 异构列如何生效（2026-09-02 补，接线说明）**：新列按项目规矩写进 `SCHEMA_SQL` 即可——`_reconcile_columns()` 会用 `PRAGMA table_info` 比对并**自动 ALTER 进历史库，无需手写迁移**（注意：引用新列的**索引**必须放在 `_reconcile_columns()` 调用**之后**再建，否则历史库首轮初始化会中断）。路由时（§2.1 第 3 步建/取 agent 之前）先读本行 profile：
> - `provider` / `model` 非空 → 写入既有 `model_overrides`（定义 `gateway/run.py:1446` `_session_model_overrides`、读取 `run.py:3272` `self._session_model_overrides.get(session_key)`），使该派生 session 用指定 LLM；
> - `skill_set` 非空 → 从 Skills Hub 挂载对应技能包；
> - **`transport='adapter'` → 不建本地 `AIAgent`**，改走 ① 的 transport adapter registry，用 `transport_ref` 定位适配器，Vermes 只持轻量 `AgentHandle`。**副作用（重要）**：外部 agent **不占 `_agent_cache` 的 `maxsize=20`**（LLM 在外部跑），故"拉 50 个外部 bot 进群"资源可行（路线图 §12 规模上限）；
> - `capability_tags` → @mention 无明确目标时按能力标签派活（路线图 §5 ①「能力发现」）。
>
> **验收补充（异构）**：[ ] 两个 profile 分别配 `provider/model=A` 与 `B` → 同一房间内 `@A` 与 `@B` 实际调用的是不同 LLM（日志可见不同 provider）；[ ] 一个 profile 配 `transport='adapter'` + `transport_ref='codex'` → 该 bot 走外部适配器、且 `_agent_cache` 计数不增。

---

## 2. Phase 1（v2.5.0）：桌面内单房间多 agent 群聊

### 2.1 后端：session 派生 + @mention 路由

**核心函数（**`⚠️ 新建 helper，真源当前不存在`**——用户审阅已实跑确认：真源只有 `_session_key_for_source(event.source)`，见 `gateway/session_mixin.py:92` + `run.py:2590/2663`。本函数建于其之上，加 room 维度派生）**：

```python
def _session_key_for_room(room_id: str, agent_profile_id: str) -> str:
    """把一个 (房间, agent) 组合映射成现有缓存/会话体系能识别的 session_key。
    复用既有的 session_key 命名空间，房间只是前缀，不引入新缓存维度。"""
    return f"room:{room_id}:agent:{agent_profile_id}"
```

> **跨渠道 room_id 非同构提醒**：飞书 `chat_id` ≠ 企微 `chatid` ≠ 微信 `room_id` ≠ 钉钉 `conversationId`。调用本函数前应先过 `RoomIdNormalizer`（`channel::room_id` → 统一 `room_uid`，~100 行，**即路线图 ⑫**；论述见**路线图 §5 ③「显式子项」**，施工位置见 **§3 落地顺序第 6 步**；旧稿写"§3 ③"不够精确，已修正），避免同一物理群被识别为多个房间。

**@mention 解析（新增 helper）**：

```python
import re
_MENTION_RE = re.compile(r"@([A-Za-z0-9_一-鿿\-]+)")

def parse_room_mentions(text: str, profiles: list) -> list[str]:
    """从消息里抽出被 @ 的 agent_profile.id 列表（大小写不敏感匹配 name/slug）。"""
    hits = {m.group(1).lower() for m in _MENTION_RE.finditer(text)}
    matched = []
    for p in profiles:
        if p["id"].lower() in hits or p["name"].lower() in hits:
            matched.append(p["id"])
    return matched
```

**路由逻辑（房间消息入口）**：
1. 收到房间消息 → `parse_room_mentions` 得到目标 agent 列表。
2. 若为空 → 取房间 `is_default` 的 agent（无则 fan-out 给所有 `member_type='agent'` 成员）。
3. 对每个目标 agent：`session_key = _session_key_for_room(room_id, agent_id)` → `_agent_cache.get(session_key)`（Web 路径 `chat.py:1211`）或网关 `_agent_cache.get(session_key)`（网关路径 `run.py:1441` 体系）→ 命中则复用，未命中则按该 `agent_profile` 的 `system_prompt`/`toolsets` 建新 agent。
4. agent 跑完 → 把回复写进 `bot_room_messages`（author_type='agent', author_ref=agent_id, turn_session_id=session_key）。
5. 房间时间线 = `SELECT * FROM bot_room_messages WHERE room_id=? ORDER BY created_at`。

**插入点（真源实测）**：
- Web/CLI 入口：`vermes_cli/blueprints/chat.py:1210`（`_cache_key = f"{provider}:{model}:{_session_id}"` 之前插入房间路由分支；若为房间消息，`_session_id` 改为 `_session_key_for_room(...)`）。
- 网关入口：`gateway/run.py:2590`（`session_key = self._session_key_for_source(event.source)`）之后，若 `event.source` 带 `room_id`，改用 `_session_key_for_room`。
- 缓存淘汰兼容：`vermes_cli/blueprints/agent_cache.py:76` 的 `pop_for_session` 已用 `k.endswith(f":{session_id}")` 匹配，派生 session_key 仍以 `:{session_id}` 结尾，房间清理天然兼容，无需改。

### 2.2 前端：房间 UI 原型

**组件（桌面 Web / TUI 两套，结构一致）**：
- **房间列表栏**：左侧 `bot_rooms` 列表，点击进入；「+ 新建房间」按钮（最小可用：建一个空房间 + 拉满默认 agents 为成员）。
- **聊天区**：单一时间线，按 `author_type`/`author_ref` 渲染气泡。
  - 人类消息：右对齐、用户色。
  - agent 消息：左对齐、**带 agent 头像 + 名字标签**（从 `agent_profiles` 取 `name`/`avatar_seed`/`hue`）。
- **@mention 输入框**：输入 `@` 触发补全下拉（数据源 `SELECT id,name FROM agent_profiles`）；发送时把 `@xxx` 留在文本里，后端解析路由。

**SSE 复用**：现有 `chat.py` 的 SSE 重连快照机制（`_session_plan_store`，`chat.py:1203`）可直接复用——每个派生 session 一条 SSE 流，房间前端按 `room_id` 把多条流聚合渲染。

### 2.3 Agent 头像生成方案（零外部依赖）

不引入图片服务。每个 `agent_profile` 有 `avatar_seed` + `hue`：
- **色相**：`hue = int(hashlib.md5(profile_id.encode()).hexdigest(), 16) % 360`，保证同一 agent 颜色稳定。
- **图形**：前端用 SVG 画一个圆形底色（hsl(hue,65%,55%)），中间放名字首字（中文取首字、英文取首字母）。可选叠加一个由 `avatar_seed` 决定的简单几何花纹（确定性 pseudo-random）做区分。
- **TUI 端**：用 `[A:研究]` / `[B:编码]` 之类的前缀标签代替图形头像，保持一致语义。

### 2.4 验收（Phase 1）
- [ ] 桌面建房间 → 至少 2 个默认 agent 自动入房。
- [ ] 在房间发 `@研究助手 帮我查 X` → 仅 researcher agent 应答，其回复带头像。
- [ ] 同一房间连续对 `@研究助手` 发消息 → 命中同一缓存 agent（prefix-cache 生效，日志可见 `_agent_cache hits` 自增）。
- [ ] 对 `@编码助手` 发的消息 → 走另一条 session，双方记忆互不污染（可各说各的上下文）。
- [ ] 房间时间线持久化：`/new` 或重启后重进房间，历史消息从 `bot_room_messages` 还原。

---

## 3. Phase 2（v3.0.0）：跨渠道群聊（飞书/钉钉/企微）

### 3.1 复用现有 26 渠道基座

真源已在渠道层区分群/单聊并提取 `room_id`：
- `gateway/platforms/weixin.py:362`：`room_id = str(message.get("room_id") or message.get("chat_room_id") or "")` + `is_group` 判定。
- 同理各渠道 adapter 大多已有 `room_id`/`chatroom` 概念（mattermost/discord/whatsapp 等均在 persona 命中列表里）。

**核心映射**：渠道群消息 → 房间。`bot_rooms` 用 `(channel, source_group_id)` 唯一定位一个房间（`idx_bot_rooms_channel` 索引）。首次见到某群消息 → 自动建 `bot_rooms(channel='feishu', source_group_id='xxxx')` 并拉默认 agents 入房。

### 3.2 渠道群内 @mention 解析

渠道群的 @mention 形态各异（飞书 `@名字`、钉钉 `@工号`、企微 `@userid`）。解析策略：
1. 先按渠道原生的「@提醒」字段拿到被 @ 的 `user_id/name` 列表（从消息 envelope 取，不靠正则）。
2. 映射到 `agent_profiles`：渠道侧建一张轻映射 `bot_room_member_alias(channel, source_group_id, channel_mention, agent_profile_id)`，把「飞书里的 @研究助手」绑定到某个 `agent_profile.id`。
3. 若群消息未 @ 任何 agent（纯人类聊天）→ agent 不响应（fail-open，不打扰人类对话）；若 @ 了某 agent → 走 §2.1 的路由，把回复发回该群（as the agent, 带 `[研究助手]` 前缀）。

### 3.3 插入点（Phase 2）
- 群消息入口：各渠道 adapter 的 message 分发处（如 `gateway/platforms/weixin.py:362` 之后）插入「群消息 → 房间路由」分支。
- 回复回写：agent 应答后，经原渠道 adapter 的 `send()` 把消息发回 `source_group_id`，前缀加 agent 名字标签。
- 不需要改 `_agent_cache`：跨渠道只是 `room_id` 换成渠道群 id，`_session_key_for_room` 完全复用。

### 3.4 验收（Phase 2）
- [ ] 飞书群里 `@研究助手 总结本周` → 仅 researcher agent 在群里应答，带 `[研究助手]` 前缀。
- [ ] 同群内对 `@编码助手` 的提问 → 走独立 session，与飞书单聊里的同一 agent 记忆是否共享由 `session_key` 决定（跨渠道默认隔离，符合预期）。
- [ ] 人类之间的纯聊天不触发任何 agent（fail-open）。
- [ ] 群解散/退群 → `bot_rooms` 标记 inactive，缓存经既有 TTL 自然回收。

---

## 4. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 房间消息误触发人类私聊 agent | 房间消息走独立 `session_key` 前缀 `room:`，与单聊 session 命名空间隔离；既有单聊逻辑零改动 |
| 大量房间 × N agent 撑爆 `_agent_cache` | 复用既有 `_enforce_agent_cache_cap`（`run.py:3385`）与 idle TTL；房间派生 session 同样受淘汰约束 |
| `agent_profiles` 为空致房间无 agent | idempotent seed（见 §1 注）；空房间前端提示「先添加 agent」 |
| 跨渠道 @mention 方言不一致 | 解析优先用 envelope 的 @字段，正则仅作桌面端兜底 |

**回滚**：全部为新增表 + 新增路由分支，不删不改既有 session/cache 代码；出问题只需关闭房间功能开关（`bot_rooms` 表置空 + 入口 `if not BOT_MODE_ENABLED: return` 短路），不影响现有单聊链路。

---

## 5. 与取长路线图的对应

- 本方案实现用户审阅里 **Tier1-Bot Mode** 的 Phase 1（v2.5.0）+ Phase 2（v3.0.0），即路线图编号 **③**。
- **建议依赖顺序**（= **技术依赖视角**）：**先 ① A2A v1.0 协议底座 → 再 ③ Bot Mode Phase 1**（房间路由依赖"能寻址到另一个 agent"的寻址原语，A2A 提供它）→ 然后 ② Grounded Citations。
  - ⚠️ **勿与路线图 §3「开工节奏」混淆**：路线图 §3 是"先易后难的施工顺序"（④ Cron → ② Citations → ① A2A → ⑪ channel_push → ③ Bot Mode），本节是"技术依赖图"。**二者不等价**——Citations 与 A2A 无依赖关系，施工上可插队到 A2A 之前；但 Bot Mode **必须**在 A2A 之后（见路线图术语表「开工节奏 vs 依赖图」）。
  - ⚠️ **A2A 底座表述修正（2026-09-02）**：旧稿写"A2A 底座（`hermes_tools_mcp_server.py` 桥接）"不精确——该文件（`agent/transports/hermes_tools_mcp_server.py`）是**外部 agent 适配器**的现成实例，**不是**通用 A2A 协议桥；A2A 协议本体底座仍是 `mcp_serve.py` + `tools/delegate_tool.py` / `tools/mcp_tool.py`（路线图 §9 #1 已再纠偏）。
- 跨渠道群聊（Phase 2）依赖 Phase 1 的 `_session_key_for_room` + 现有 26 渠道 `room_id` 提取，二者就绪后增量极小。
