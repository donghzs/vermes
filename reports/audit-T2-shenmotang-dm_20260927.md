# 审计备忘录 · T2 神魔堂 1:1 默认面（`feat/shenmotang-ux-dm`）

> 审计人：WorkBuddy · 日期：2026-09-27 · 方法：`audit-completion-report` 四步（commit 验真 / 文件落地 / 测试实跑 / 契约核对）
> 被审对象：mimo 提交 `0a953e3697 feat(shenmotang): T2 1:1 默认面 — 选联系人 → peer_dm 会话`
> 关联：spec `vermes-im-desktop-entry-spec_20260926.md` §12（T1~T7）、审计 `audit-T1-shenmotang-leftbar_20260926.md`
> 取证纪律：不采信口播；所有结论回到 commit / 文件路径 / 行号 / 实跑输出。本轮 **1 次自查纠错**（秘书模式 grep 曾加 `head -20` 截断，已不加 head 复验）。

---

## 一、结论速览

**T2 骨架与后端契约全部真实、测试真绿、零污染；但交付物核心闭环有 1 个 P0 缺陷 + 1 个 P1 语义错配 —— 建议修复后再 merge，不建议直接合。**

| 严重度 | 问题 | 一句话 |
|---|---|---|
| **P0** | 1:1 面**收不到 agent 回复** | 后端回复走 WS 后台推送，而神魔堂侧三个组件**零 WS 订阅**（全前端仅 `BotRooms.vue:937` 监听） |
| **P1** | 单成员 DM 房**必然触发秘书模式**，与 1:1 心智错配 | 会造神 + `add_bot_room_member` 拉人入群，破坏 1:1 单成员前提，且第二次发消息行为"变脸" |
| 🟡 P2 | `peerFrom` 无生产入口 | 父组件从不传 → A2A `sendPeerDm` 分支是死代码（仅测试手动传才走） |
| 🟡 P3 | 状态点"在线"是**配置态**非真实探测 | `has_api_key / transport==='acp'` ≠ 在线，绿点+「在线」标签可能误导 |

---

## 二、回盘**通过**项（逐条实测，非采信）

| mimo 声称 | 回盘结果 | 硬证据 |
|---|---|---|
| 提交 `0a953e3697` 在 `feat/shenmotang-ux-dm` | ✅ 真 | `git log --oneline main..feat/shenmotang-ux-dm`：`0a953e3697` / `f9e41506ab` / `8753987453` / `7c4ba88062` / `a84896bf29`（叠在 T1 之上） |
| 净变更 7 文件（T1+T2） | ✅ 真 | `git diff --name-only main..feat/shenmotang-ux-dm` = README + Shenmotang.vue + ImRail + **PeerDm** + api.js + 2 测试 |
| **web_dist = 0** | ✅ 真 | `git diff --name-only ... -- vermes_cli/web_dist` 空；`git status --short -- vermes_cli/web_dist` 空（未重蹈 T1 的 P1 污染） |
| `ShenmotangPeerDm.vue` 落地 | ✅ 真 | `frontend/src/components/ShenmotangPeerDm.vue`（235 行，头/消息流/输入三段齐全） |
| `api.sendPeerDm` 新增、后端 0 新增 | ✅ 真 | `api.js:723`；分支 diff **无 `.py` 文件** |
| `peer_dm` 端点与字段名对齐 | ✅ 真 | `chat.py:4282 bot_room_peer_dm`，docstring 明写 `body: {"from","to","text"}`；后端还兼容 `from_id/to_id/message` 别名；路由注册 `chat.py:7287` |
| **稳定 DM 房 `dm-<contactId>`** | ✅ 真 | `createBotRoom(name, members, extra)` → `{name, members, ...extra}`（`api.js:659-661`）→ 后端 `chat.py:3900 room_id = (body.get("id") or "").strip()`，**非空即用**（`3921 create_bot_room`），仅缺省才生成 `<title>_<rand6>` |
| 联系人字段无幽灵键 | ✅ 真 | `api_agent_contacts`（`chat.py:6389`）返回含 `id/name/provider/model/avatar_seed/**hue**/**transport**/**has_api_key**/capability_tags/... — 组件用到的 `hue/transport/provider/has_api_key` 全部真实 |
| 契约测 7 passed | ✅ **实跑真绿** | `npx vitest run tests/shenmotang-peer-dm.test.js tests/shenmotang-im-rail.test.js` → **Test Files 2 passed / Tests 7 passed**（4 T2 + 3 T1 回归），662ms |
| 非镜像实现 | ✅ 真 | 第 4 例直接 `fs.readFileSync('src/components/Shenmotang.vue')` 断言 `onSelectContact … activeContact.value = c`（行为契约，不是复述实现） |
| 守纪未 merge/push | ✅ 真 | 分支仍在，`git status` 无 merge/push 痕迹 |
| 工作树干净 | ✅ 真 | `git status --short` 仅 6 个 `??`（审计/落页报告，非交付物、不进分支） |

**额外坐实（mimo 未明说但成立）**：`send()` 的分支逻辑正确——`peerFrom && peerFrom !== contact.id` 走 `sendPeerDm`，否则走 `sendBotRoomMessage`（`ShenmotangPeerDm.vue:206-210`），与后端 `from_id == to_id → self-target` 拒绝（chat.py:4303）相互兜底。

---

## 三、问题清单（含定级依据）

### P0 · 1:1 面收不到 agent 回复（核心闭环断）

**证据链：**
1. 后端秘书模式是**后台化**的：`chat.py:5131-5145` 注释明写"请求秒回，流水线在后台跑，进度经 **WS room_update** 实时推送"，实际调用 `_run_org_in_background(fn=_secretary_orchestrate, ...)`，且返回体只带 `timeline`（用户消息即时写库，回复尚未产生）。
2. 前端 WS 派发方：`stores/chat.js:927`、`stores/botRoom.js:172` → `window.dispatchEvent(new CustomEvent('vermes:room_update', ...))`。
3. **监听方全仓仅 1 处**：`BotRooms.vue:937 window.addEventListener('vermes:room_update', _onRoomUpdate)`。
4. 神魔堂侧 `Shenmotang.vue` / `ShenmotangImRail.vue` / `ShenmotangPeerDm.vue` grep `vermes:room_update|WebSocket|socket|ws://` → **零命中**。

**后果**：用户在 1:1 面发消息 → UI 立即显示自己那条（`r.timeline` 覆盖），但 **agent 回复永远不出现**，除非切走联系人再切回（触发 `watch(contact.id)` → `load()`）。用户体验就是"石沉大海"。

**修复方向**：在 `ShenmotangPeerDm.vue` 加 `window.addEventListener('vermes:room_update', ...)`，`onUnmounted` 移除；按 `detail.room_id === roomId.value` 过滤后 `load()`（或增量 append）。直接复用 `BotRooms.vue:937` 既有范式，约 10 行。

> 诚实标注：本条重点核实了**首条消息**路径（必定秘书模式→WS）。dm 房被落岗位表后第二次发消息走 2c 组织分流的返回方式未逐条追踪，但**组件零 WS 订阅**意味着任何异步推送都收不到，结论不受影响。

### P1 · 单成员 DM 房必然触发秘书模式，与「1:1 私聊」心智错配

**证据链：**
1. 触发条件 `chat.py:5116-5129`：
   ```python
   room_roles = db.get_org_roles(room_id) ...
   if not room_roles:
       if len(profiles) == 1:          # ← T2 的 dm 房恰是 members=[cid]
           sec = profiles[0]
       elif secretary_profiles:
           sec = secretary_profiles[0]
   if sec is not None: → _secretary_orchestrate(...)
   ```
2. T2 建的 `dm-<contactId>` 房：**单成员**（`createBotRoom(name, [cid], {id})`）+ **新建无 org 岗位表** → 条件同时满足 → **第一条用户消息 100% 触发秘书模式**。
3. 秘书模式做什么（`chat.py:4805+`，函数 docstring 与注释）：
   - 该 agent 当"**老板秘书**"，设计组织架构（产出岗位 JSON，按经济-质量-效率三平衡选人）
   - 候选池 = **全量联系人**（`chat.py:4824`"秘书可摇人入群"）
   - 缺人时 **自动造神**（`forge:` 前缀 → `sec:<name>` 新 profile，`chat.py:4907-4935`）
   - **落地时 `db.add_bot_room_member(room_id, "agent", pid)`（`chat.py:4963`）往房间里加人**
   - 自动建任务并驱动流水线

**后果（三重）**：
- **心智错配**：用户以为在私聊，实际是"向秘书下达任务、让它组队干活"。mimo 报告写的是"单成员 → 后端秘书模式**应答**"——秘书模式不是简单应答，是组织编排流水线，此处存在认知偏差。
- **1:1 前提被破坏**：秘书落岗位表时会往 dm 房加成员 → 房不再单成员 → **第二次发消息不再满足 `len(profiles)==1`，改走 2c 组织分流，行为"变脸"**。
- **副作用外溢**：可能自动造出 `sec:*` agent 写进 profile 库（用户没要求造人）。

**修复方向（需董董拍板，三选一）**：
| 方案 | 做法 | 代价 |
|---|---|---|
| **(a) 预置最小岗位表**（推荐） | DM 房创建后写一个最小 org 角色 → `room_roles` 非空 → 跳过秘书设计，走 2c 直答 | 前端/后端各一处小改，语义仍是 1:1 |
| **(b) 后端加"1:1 直答"路径** | 对 `dm-*` 房跳过秘书模式，直接单 agent 应答 | 打破"后端 0 新增"，但语义最干净 |
| (c) 接受语义、改 UI 措辞 | 把 1:1 面改叫"下达任务" | 违背微信式 1:1 心智，不推荐 |

### 🟡 P2 · `peerFrom` 无生产入口 → A2A 分支是死代码

- 父组件 `Shenmotang.vue:94-98` 只传 `:contact="activeContact"`，**全文件无 `peerFrom` / `peer-from`**。
- `peerFrom` 默认 `''` → `send()` 恒走 `sendBotRoomMessage`；`api.sendPeerDm` 在生产路径**永不触发**（仅测试手动传 `peerFrom: 'p2'` 才走）。
- 定性：作为 T3（拉群后 agent@agent）的**预留分支**可接受，但报告里"peerFrom 时走 peer_dm（A2A）"应标注为**预留、未接线**，别让后人误以为已通。

### 🟡 P3 · 状态点是"配置态"，不是真实在线

- 判定：`has_api_key || transport === 'acp'` → 绿点 + 标签「在线」（`ShenmotangPeerDm.vue:122`、`ShenmotangImRail.vue:65`）。
- 这是"已配置/已接入"，**不等于在线可达**（agent 可能未启动、key 过期、端点不可达）。
- 建议：标签改「已接入」/「可用」，或接真实探测（若后端有 health 端点）。属 UX 措辞问题，不阻塞。

---

## 四、mimo 做对的地方（诚实记录）

1. **后端零新增、零幽灵调用**——`sendPeerDm` 字段与后端 docstring 完全一致，contacts 字段全部真实。
2. **稳定 DM 房设计正确**——正确利用了 `POST /bot/rooms` 的 `id` 可选参数（`chat.py:3900`），不是每次新建随机房。
3. **测试真绿且含源码断言**——第 4 例锁死 `onSelectContact → activeContact`，防回归。
4. **吸取 T1 教训**——本轮 **web_dist 零污染**（T1 曾 commit 16 个产物文件）。
5. 守"不自行 merge/push"纪律。

---

## 五、验收口径（修复后复验清单）

| # | 复验项 | 判据 |
|---|---|---|
| R1 | P0 已修 | `ShenmotangPeerDm.vue` grep 到 `vermes:room_update` 且 `onUnmounted` 有移除 |
| R2 | P1 已处置 | 依董董拍板方案；若 (a)：dm 房创建后有岗位表写入；若 (b)：后端对 `dm-*` 跳过秘书模式 |
| R3 | 测试仍绿 | `npx vitest run tests/shenmotang-peer-dm.test.js tests/shenmotang-im-rail.test.js` ≥ 7 passed |
| R4 | 零污染 | `git diff --name-only main..<branch> -- vermes_cli/web_dist` 为空 |
| R5 | P2 标注 | 报告/注释标明 peer_dm 为预留未接线（或已接线） |

---

## 六、待董董拍板

1. **P1 采用哪个方案？**（a 预置岗位表 / b 后端 1:1 直答路径 / c 改措辞）—— 我推荐 **(b) 语义最干净**，若坚持"后端 0 新增"则选 (a)。
2. **P0 是否要求 mimo 修完再 merge**，还是先合 T1、T2 单独修？—— T1 已终验可合，T2 建议修完 P0 再合。
3. **T3（⊕ 拉群）是否按原计划开工** —— 建议等 P1 拍板后再开，否则 T3 的"拉群"会和秘书模式的"自动拉人"语义打架。

---

## 七、main 合入复核（2026-09-27 · 董董已 merge，未 push）

> 纪律：不采信口播，逐项回盘。结论：**merge 链真实、零污染、测试真绿；但 P0/P1 两个硬伤原样带入 main（未修），属 carry-forward，需在 T3 前处置。**

### 复核证据表

| 复验项 | 实测结果 |
|---|---|
| 当前分支 = main | ✅ `git branch --show-current` = main |
| 3 条 merge/feature 提交真实 | ✅ `347c0b03c5`(merge T1) / `e8617c976b`(merge T2) / `9930435330`(chore canary) 均在 `origin/main..main` 列表 |
| 领先 origin/main 8 commits | ✅ `rev-list --count` = 8（含 2 merge + T1 末态 3 commits + T2 1 commit + canary 1） |
| 净变更 7 文件（源+测） | ✅ `diff --name-only origin/main..main` = README + Shenmotang.vue + ImRail + PeerDm + api.js + 2 测 = **7 文件**；**外加** `reports/canary-schedule/verdicts.jsonl`（canary chore，第 8 个 diff 文件，非 T1/T2 交付） |
| **web_dist = 0** | ✅ `diff origin/main..main -- vermes_cli/web_dist` 空 + `status --short -- web_dist` 空（**merge 未引入污染**） |
| 后端零改动 | ✅ diff 中无任何非测试 `.py`（"后端 0 新增"成立，秘书模式逻辑 `chat.py:4282/5126` 原样） |
| 契约测 7 passed（main 上） | ✅ `npx vitest run` 实跑 **7 passed**（4 T2 + 3 T1 回归，555ms） |
| gateway WIP stash 仍在 | ✅ 现 `stash@{0}` = `WIP on main: 7bb6803d9f fix(gateway)...`（注：canary 原 stash@{0} 已 pop 提交，gateway 原 stash@{1} 自然重编号为 stash@{0}，内容未动、未误 pop） |
| reports/ 未跟踪落页未带入 | 🟡 现 **7 个** `??`（用户说 6 个，少算今晨新增的本报告）；全为 WorkBuddy 生成的落页/审计，未进 merge，不污染真源 |

### carry-forward 风险（merge 未修，必须正视）

- **P0 · 1:1 面收不到 agent 回复**：`ShenmotangPeerDm.vue` grep `WebSocket|room_update|socket|onUnmounted` → **零命中**（merge 未改该组件）。秘书模式回复走 WS `room_update` 后台推送，神魔堂三组件仍无监听 → 发消息后只看到自己那条，回复不出现。
- **P1 · 单成员 DM 房必触发秘书模式**：后端 `chat.py:5126 len(profiles)==1` 分支 merge 未动 → 语义错配（"1:1 私聊"首条消息即触发组织编排 + `sec:*` 造神 + 自动拉人）原样保留。

### 复核结论

- **可 push 前提**：push 由董董做（`origin/main` 领先 8）；push 前**无需再剔除 web_dist**（已干净）。
- **阻塞提示**：P0/P1 未修即合入 → 1:1 私聊在真实后端下**不可用 / 行为错配**，属已知 open 缺陷。建议 **T3 开工前先处置 P0（照 BotRooms.vue:937 加 WS 监听 ~10 行）+ P1 拍板**，否则 T3 拉群会与秘书模式自动拉人打架。
- **stash@{0} 注意**：那是另一 agent 的 gateway WIP，**勿误 pop**，等它 owner 处理。

---

## 八、四项审计修复复核（2026-09-27 · f90b89429e 合回 main）

> 纪律：不采信口播，逐项回盘。结论：**四项修复全部真落地、测试真绿、零污染；carry-forward 风险已闭环。**

### 修复复核证据表

| 级别 | 修法 | 回盘结果 |
|---|---|---|
| **P0 收不到回复** | PeerDm 订阅 `vermes:room_update` + onUnmounted 移除 | ✅ `ShenmotangPeerDm.vue:248` `addEventListener('vermes:room_update', onRoomUpdate)`、`251-253` `removeEventListener`；`onRoomUpdate` 监听 `room_message`/`room_message_delta` 后 `load()`；事件名与派发方 `stores/chat.js:927` / `botRoom.js:172` **完全一致** |
| **P1 秘书模式错配** | 方案 b：dm-* / `1:1 ·` 房跳过秘书与组织流水线，fall-through 到 path-3 单 agent 直答 | ✅ `chat.py:5120-5121` `_is_dm_room`；`5126` 秘书触发改 `if not _is_dm_room and not room_roles`；`5156` 组织分流改 `if room_roles and not _is_dm_room`；dm 房落到 `5159+` path-3（`_run_one` acp/cli/native 三通路直答），回复经 WS 广播被 P0 接收，**逻辑自洽** |
| **P2 peerFrom 死代码** | 标注"预留未接线，A2A 留 T5" | ✅ `ShenmotangPeerDm.vue:107-112` prop docstring 明示"父组件只传 :contact，不传 peerFrom；A2A 接线在 T5" |
| **P3「在线」误导** | 改「已接入」（配置态≠在线） | ✅ `ShenmotangPeerDm.vue:126-130` statusLabel = `已接入`/`本地已接入`/`就绪`；断言同步 `shenmotang-peer-dm.test.js:74` `toContain('已接入')` |

### 测试实跑（真绿，非口播）

- **前端 8 passed**（实跑）：`shenmotang-peer-dm.test.js` 5（含 P0 源码断言 `addEventListener/removeEventListener('vermes:room_update')` @134-142）+ `shenmotang-im-rail.test.js` 3。
- **后端 10 passed**（实跑）：`tests/botmode/test_org_integration.py` 全量。**含新测 `test_dm_room_skips_secretary_mode`**；旧秘书路由 2 测 `test_message_send_routes_secretary_mode_to_background` / `test_message_send_secretary_member_triggers_without_mention` **不回归**。
- ⚠️ 后端首跑报 `PermissionError: EEXIST: /tmp/pytest-of-unknown` —— **非代码错，是 WorkBuddy 沙箱 shim 对固定临时目录名的碰撞**（AGENTS.md 已记 `TMPDIR` 假红）。用 `T=$(mktemp -d) && TMPDIR=$T ...` 绕过即 10 passed。后续核后端测一律用此法。

### 工程卫生

- commit `f90b89429e`（merge）+ `0b3569fbd6`（fix）真实在 `origin/main..main`；main 领先 **10 commits**。
- 净变更 10 文件（README + 3 Shenmotang 组件 + api.js + 2 前端测 + verdicts.jsonl + 后端测 + chat.py）；**web_dist 零污染**（diff 空 + untracked 空）。
- **后端改动范围克制**：仅 `chat.py` 两处条件 + 单测文件，未碰其它秘书路由。

### 复核结论

- **四项 carry-forward 风险已闭环**：P0/P1 修复后，1:1 私聊在真实后端下可用且语义正确；P2/P3 为口径标注，无行为风险。
- **可 push**：push 由董董做（领先 10）；web_dist 干净，无需再剔。
- **T3 绿灯**：此前"P1 拍板前勿开 T3"的阻塞已解除——dm 房短路后，"自动拉人"只在非 dm 群触发，T3 拉群不会与之撞车。T3（⊕ 拉群）可开做，但仍建议复用 `applyBotOrg` 显式建群，勿依赖秘书模式隐式拉人。
- **stash@{0}（gateway WIP）仍勿误 pop**。
