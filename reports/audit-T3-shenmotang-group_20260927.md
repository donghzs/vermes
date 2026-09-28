# T3 ⊕ 拉群 审计报告（feat/shenmotang-ux-group · 96ae330248）

> 审计时间：2026-09-27 · 审计人：WorkBuddy
> 纪律：不采信完成情况口播，逐项回盘到磁盘/源码/实跑。分支铁律——本地提交由 WorkBuddy/mimo，审计 + push 由董董。
> 结论：**功能与接线全部真落地、测试 13 passed 真绿、零污染；但 T3 的 headline 纪律宣称「绝不走秘书隐式造神/拉人」被同源后端测试证伪——存在 1 处 P1 复发窗口，建议修完再 merge。**

---

## 一、回盘通过项（实核，非采信）

| mimo 声称 | 实测结果 | 证据 |
|---|---|---|
| 提交 `96ae330248` 在 `feat/shenmotang-ux-group` | ✅ 真 | `git log main..feat/shenmotang-ux-group` 单条 feat；`branch --show-current` = 该分支 |
| 净变更 3 文件 | ✅ 真 | `frontend/src/components/Shenmotang.vue`（接线）+ `ShenmotangCreateGroup.vue`（组件）+ `frontend/tests/shenmotang-create-group.test.js`（测）|
| **后端 0 新增** | ✅ 真 | diff 无任何 `.py` |
| **web_dist = 0** | ✅ 真 | `diff --name-only main..HEAD -- vermes_cli/web_dist` 空；`status --short -- vermes_cli/web_dist` 空（连续两轮吸取 T1 教训） |
| `ShenmotangCreateGroup.vue` 落地 | ✅ 真（231 行） | 群名输入 + 联系人多选 + 「搭组织」勾选 + 模板选择 + 建群按钮，UI 完整 |
| **两个入口统一到同一弹窗** | ✅ 真 | ImRail `@create-group`(Shenmotang.vue:68) 与 PeerDm `@create-group`(:113) 双双绑 `onCreateGroup` → `createGroupOpen=true` → 同一 `<ShenmotangCreateGroup>` |
| **显式拉人 + useOrg 门控** | ✅ 真 | `submit()`：`createBotRoom(gname, [...selected])` 传显式成员；`if (useOrg && orgKey)` 才 `applyBotOrg`（CreateGroup.vue:194/201-208） |
| **从 1:1 发起预选对方** | ✅ 真，且**非硬编码** | `name.value = \`${props.preselectName} 群\``（:161-163）——对方是 Codex 才是「Codex 群」，是 Kimi 就是「Kimi 群」。mimo 报告里的「Codex 群」是示例，非 bug |
| toast + 选中新群 + 刷新左栏 | ✅ 真 | `toast.success` → `emit('created')` → 父 `onGroupCreated` 设 `selectedKey='room:'+rid` + `tab='hall'` + `imRail.reload()` |
| 契约测 13 passed | ✅ **实跑真绿** | 5(T3) + 5(T2) + 3(T1) = 13 passed，707ms |
| verdicts 漂移已 stash 隔离 | ✅ 真，且标注清晰 | `stash@{0}: On feat/shenmotang-ux-group: canary-verdict drift（非T3，勿混入）`；`git status` tracked 干净 |

**额外自查（我一度怀疑但被证伪，如实记录）**：曾担心「从群会话态点 ⊕ 会沿用陈旧 `activeContact` 预选错人」→ 回盘 `Shenmotang.vue:33` `onSelectRoom` 确有 `activeContact.value = null`，**该怀疑不成立**，mimo 做对了。

---

## 二、P1 · 「1 成员 + 未搭组织」的新群仍走秘书模式，自动造神/拉人（headline 纪律被证伪）

### 现象

T3 组件注释（CreateGroup.vue:184-187）与交付报告都宣称：**「显式拉人；绝不走秘书隐式造神」**。但**未勾「搭组织」且恰好选中 1 人**时，建出的群在首条消息必定触发秘书模式 → 该 agent 当老板秘书 → 自动 `sec:*` 造神 → `add_bot_room_member` 拉人 → 跑组织流水线。这正是 P1 想消灭的行为。

### 为什么命中（代码链）

```python
# vermes_cli/blueprints/chat.py:5120-5126
_is_dm_room = room_id.startswith("dm-") or (room.get("title") or "").startswith("1:1 ·")
sec = None
if not _is_dm_room and not room_roles:      # ← T3 群：两个条件都为 True
    if len(profiles) == 1:                  # ← 只预选 1 人 → 命中
        sec = profiles[0]                   # → 秘书模式
```

- T3 调 `api.createBotRoom(gname, [...selected])`（**不传第三参 `id`**）→ 后端随机 `room_id`，且 title 为「X 群」→ **两处短路条件都不命中** `_is_dm_room`。
- 未勾「搭组织」→ 无 `applyBotOrg` → `room_roles` 为空。
- 从 1:1 发起时 `selected` 默认只有 `preselectId` 一人（:158-160），且 `useOrg` 默认 `false`（:135）→ **`len(profiles)==1` 命中**。

### 双探针实证（最强证据：mimo 自己参与的那份测试文件里的对照）

同一份 `tests/botmode/test_org_integration.py` 中，**两个测的房间成员结构完全相同**（都是 1 个 `researcher`、无岗位表），唯一差别是 id/name：

| 测试 | 建房方式 | 断言 |
|---|---|---|
| `test_message_send_routes_secretary_mode_to_background` (:261-282) | `POST /rooms {"name":"秘书路由房"}`（**随机 id**） | **触发** `_secretary_orchestrate` |
| `test_dm_room_skips_secretary_mode` (:317-353) | `POST /rooms {"name":"1:1 · researcher","id":"dm-researcher"}` | **不触发**秘书，且 `members==1` 未被加人 |

→ **T3 建的「X 群」= 随机 id，与「秘书路由房」同构、与 `dm-*` 不同构**，因此走的是**前者（触发秘书）**。
这满足双探针：应命中一次（秘书房触发）+ 应不命中一次（dm 房不触发），探针本身有效，结论可靠。

### 触发窗口与后果

- **窗口**：`members 恰为 1` **且** `未勾搭组织`。而这恰好是**从 1:1 点「＋ 拉群」的默认状态**（只预选对方、`useOrg=false`），用户不额外加人即命中。
- **不受影响的情形**（公允列出）：勾了「搭组织」（写了 room_roles → 走 2c 组织流水线，属预期组队语义）；或选了 ≥2 个非 secretary 成员（`len!=1` 且无 secretary profile → 正常 path-3 群聊）。
- **后果**：用户想拉个群聊几句，结果群里凭空冒出 `sec:*` 神 + 被加人 + 跑组织流水线 → 违背 T3「显式拉人」纪律，也违背 P1 审计意图。

### 修法（三选一，需董董/mimo 拍板）

| 方案 | 做法 | 评价 |
|---|---|---|
| **A1（推荐·纯前端·保「后端 0 新增」）** | `selected.length===1 && !useOrg` 时**禁用「建群」并提示**：「单人群请用 1:1 私聊；拉群至少选 2 人，或勾选『搭组织』」 | 语义最干净、无隐藏副作用；改动 <10 行 |
| A2 | 单成员时自动按 DM 语义建（传 `{id:'dm-'+cid}` + `1:1 · name`） | 行为接近用户直觉，但可能与该联系人**既有的 `dm-<cid>` 房 id 撞车**（`ensureDmRoom` 已在用同名稳定房），有重复/冲突风险 |
| B（后端） | 收紧秘书触发条件（如要求显式组织意图） | **不推荐**：会打破 `test_..._secretary_mode_to_background` 所编码的既有产品语义（"群=组队场景"），属产品决策而非 bug 修复 |

我推荐 **A1**。注意：**不要**因为"后端才干净"就改后端——秘书模式对「真正的单人组队房」是既定特性，前端守住输入约束才是正确边界。

---

## 三、工程卫生

- 分支 1 commit，未 merge / 未 push，纪律守好 ✅
- `web_dist` 零污染（连续第二轮）✅
- canary `verdicts.jsonl` 二次漂移已 stash 并**标注"非T3，勿混入"**——比上一次更规范 ✅
- `stash@{1}` = 另一 agent 的 gateway WIP（`7bb6803d9f`），**勿误 pop**

> 备注：此前核后端测曾遇 `PermissionError: EEXIST /tmp/pytest-of-unknown`（WorkBuddy 沙箱 shim 对固定临时目录名碰撞，**非代码错**）。解法：`T=$(mktemp -d) && TMPDIR=$T .venv/bin/python -m pytest ...`。本次未跑后端（T3 后端 0 改动）。

---

## 四、复验清单（给 mimo 整改后自查）

| # | 复验项 | 通过标准 |
|---|---|---|
| R1 | 单成员 + 未搭组织时不建普通群 | A1：按钮 disabled + 文案提示；或 A2：确认不与既有 `dm-<cid>` 撞车 |
| R2 | 契约测仍绿 | `npx vitest run` 三个文件 ≥ 13 passed |
| R3 | 零污染 | `git diff --name-only main..HEAD -- vermes_cli/web_dist` 空 |
| R4 | 后端仍 0 改动 | diff 无 `.py` |
| R5 | 多成员路径不受影响 | 选 ≥2 人 / 勾搭组织 时行为与现在一致 |

---

## 五、结论

- **可合并性**：功能、接线、测试、卫生全达标；**唯一阻塞是 P1 的「单成员未搭组织」窗口**——它直接违背 T3 自己的 headline 纪律，建议按 **A1 修完再 merge**（改动 <10 行）。
- **T4（任务卡）口令**：建议等 P1 处置完再开，避免在一个语义未收敛的群模型上继续叠任务卡绑定。
- mimo 做对的地方如实记录：显式成员 + useOrg 门控、两入口统一、`preselectName` 派生非硬编码、连续两轮 web_dist 零污染、漂移 stash 标注清晰、测试真绿。

---

## 六、A1 修复复核（2026-09-27 · 348a01c628）

> 结论：**A1 主修复真落地、默认路径已封死、测试双探针真绿 → P1 解除阻塞，可 merge。**
> 但守卫判的是「useOrg 意图」而非「组织真会被应用」，**残留 1 条 P2 缝**（低概率、未覆盖），一并列出供后续收口。

### A1 实现回盘（实核）

| 项 | 证据 |
|---|---|
| 分支 2 commits | ✅ `96ae330248`(功能) + `348a01c628`(A1 fix) |
| 净变更仍 3 文件 / 后端 0 改动 | ✅ diff 无 `.py` |
| **web_dist 零污染** | ✅ diff + status 均空（连续第二轮） |
| `blockedSolo` 守卫 | ✅ `ShenmotangCreateGroup.vue:153` `computed(() => selected.value.length === 1 && !useOrg.value)` |
| 按钮禁用 | ✅ `:disabled="submitting \|\| !name.trim() \|\| !selected.length \|\| blockedSolo"` |
| 提示文案 | ✅ `v-if="blockedSolo"` 琥珀提示「单人群请用 1:1 私聊；拉群至少选 2 人，或勾选「搭组织」。」 |
| **`submit()` 同步守卫** | ✅ `:201` `if (!gname \|\| !selected.value.length \|\| submitting.value \|\| blockedSolo.value) return` —— 覆盖回车 `@keydown.enter.prevent="submit"` 路径，非仅视觉禁用 |
| 不误伤正常路径 | ✅ `blockedSolo` 在 `≥2 人` 或 `useOrg=true` 时为 false |

### 测试实跑：**15 passed**（7 T3 + 5 T2 + 3 T1），970ms

新增两例是**真行为断言、非镜像**，且构成双向双探针：

- **A1 禁建侧**：挂载 `preselectId:'p1'`（默认选 1 人、useOrg=false）→ 断言按钮 `disabled` 存在 + 提示文案出现；**再强行 `trigger('click')`，断言 `createdBodies` 中无 `createRoom`**（证明 submit 守卫真生效，不只是置灰）；随后再勾 Kimi（≥2 人）→ 断言 `disabled` 解除。
- **A1 可建侧**：勾选「搭组织」→ 按钮启用 → 断言 `createRoom` **与** `applyOrg` 均被真实调用（坐岗是显式 applyBotOrg）。

→ 一「应不命中」一「应命中」，探针自证有效，与后端审计同一取证标准。

### 🟡 P2 残留：守卫判「意图」而非「组织真会应用」

`blockedSolo` 只判 `useOrg`，但 `submit()` 应用组织的条件是 `useOrg && orgKey`（:212）。二者不一致 → 当 `orgKey` 为空时，勾了「搭组织」也不落岗位表：

```
loadTemplates() 模板加载失败 / 返回空 → templates=[] → orgKey 保持 ''   (:141/:174-176)
用户勾「搭组织」→ useOrg=true → blockedSolo=false → 按钮可用
submit() → :212 (true && '') = false → 不调 applyBotOrg
→ 建出「1 成员 + 随机 id + 无岗位表」房 → chat.py:5126 秘书模式复发
```

同类旁路还有第二个子情形：模板有但 `roles` 映射为空（:218 `if (roles.length)` 跳过）→ 同样不落岗位表。

- **触发条件**：`1 人 + 勾了「搭组织」+ 模板不可用/无岗位`。属低概率（模板端点 `chat.py:4370` 正常时可用），但**沉默地**产生与 P1 完全相同的坏结果，且**测试未覆盖**（A1 两测都建立在模板可加载的假设上）。
- **不影响 merge 判断**：P1 的**默认路径**（从 1:1 点 ⊕、useOrg 未勾）已被 A1 正确封死，这是主要风险面。
- **收口修法（1 行，纯前端）**：把守卫改为判「组织真会被应用」而非意图：
  ```js
  const orgWillApply = computed(() => {
    if (!useOrg.value || !orgKey.value) return false
    const tpl = templates.value.find(t => t.key === orgKey.value)
    return ((tpl && tpl.roles) || []).length > 0
  })
  const blockedSolo = computed(() => selected.value.length === 1 && !orgWillApply.value)
  ```
  并建议提示文案区分：勾了组织但模板不可用时显示「组织模板未加载，无法搭组织」。

### 🟡 P3 UX 小瑕

模板不可用（`templates=[]` 且 `loadingOrg=false`）时，勾选「搭组织」后下方模板区渲染为空白，用户看不到任何反馈，不知道组织其实不会被应用。建议加一条空态提示。

### 复核结论

- **P1 解除阻塞，T3 分支可 merge**（2 commits、3 文件、web_dist 零、15 passed 真绿）。
- **P2/P3 属后续收口项**，不阻塞本次 merge；建议记入 T4 前置或在 T3 合入后单开一个 3 行修。
- T4（任务卡）仍建议等群模型语义收敛后再开——尤其上述「组织是否真落岗位表」的不一致，会影响任务卡派活依赖的岗位语义。

---

## 七、P2/P3 收口复核（commit `c9a9cfe07b`，2026-09-27）

> mimo 把「合入后单开 3 行修」直接并进本分支（避免坏态先上 main），此节为回盘复核。

### R1 · 提交通真

`feat/shenmotang-ux-group` 现 3 commits（`96ae330248` T3 功能 → `348a01c628` A1 → `c9a9cfe07b` P2/P3），净变更仍 **3 文件**（`Shenmotang.vue` / `ShenmotangCreateGroup.vue` / `shenmotang-create-group.test.js`），**后端 0 新增**（diff 无 `.py`），**web_dist 零污染**（diff 空 + untracked 空），工作树干净（仅 7 个 `??` 未跟踪 reports/ 产物，非交付物）。

### R2 · P2 守卫语义改对 ✅

`ShenmotangCreateGroup.vue:159-165`：

```js
const orgWillApply = computed(() => {
  if (!useOrg.value || !orgKey.value) return false
  const tpl = templates.value.find(t => t.key === orgKey.value)
  return ((tpl && tpl.roles) || []).length > 0
})
const blockedSolo = computed(() => selected.value.length === 1 && !orgWillApply.value)
```

与审计给的修法**逐字一致**，且 **submit 守卫同源**：`:213` `blockedSolo.value` —— 不存在"视觉置灰但回车绕过"的旁路。

与 `submit()` 实际落岗条件**完全对齐**：`:224` `useOrg && orgKey` → `:226-230` `roles.length` 才 `applyBotOrg`。即"守卫判意图"的不一致已消除。

### R3 · P3 空态提示 ✅

`:82-84` 新增 `v-else-if="!templates.length"` 琥珀提示：「组织模板暂不可用，无法坐岗。单人请改用 1:1 私聊，或再勾一位联系人建群。」—— 不再是空白。

### R4 · 新增测试是真行为断言 + 双向双探针 ✅

新测 `P2：勾了搭组织但模板/roles 空时，单人仍禁建`（mock `/org/templates` → `templates: []`）：

- 断言 P3 提示文本出现；
- 断言建群按钮 `disabled`；
- **强行 click 后断言 `createRoom` 与 `applyOrg` 均未被调用**（证明 submit 守卫真生效，非摆设）。

"应不命中"侧成立；"应命中"侧由既有测（模板可用 + 勾组织 → `createRoom` + `applyOrg` 都真被调用）覆盖，未回归。

**实跑：16 passed 真绿**（`shenmotang-create-group` 8 + `peer-dm` 5 + `im-rail` 3，863ms）。

### R5 · 理论缝已证伪（降级 P4）

审计曾担心的第二条旁路——「模板**非空但选中模板 roles 为空** → 无解释性提示」（此时 `:82` 的 `!templates.length` 不成立，只剩 `:115-117` 的 A1 文案「或勾选「搭组织」」，而用户已勾 → 略误导）。

回盘实证：`ORG_TEMPLATES`（`org_engine.py:71`）**只有 2 个模板**——`company`:73、`court`:90，且 `"roles": [` 计数 = 2 = 模板数，**两者 roles 均非空**；空 roles 仅出现于 `:144/150` 的坏 JSON 解析容错路径（非模板定义）。

→ **该子情形在现状数据下不发生**，降级为 P4 理论缝，不阻塞 merge。

### R6 · 🟢 P4 文案小瑕（不阻塞）

模板不可用时，`:82-84` 与 `:115-117` 两条琥珀提示**同时出现**，后者仍写「…或勾选「搭组织」」，而用户此刻已勾选 → 轻微自相矛盾。建议后续把 `:115` 文案改为条件式（未勾 → 提示勾组织；已勾但不可用 → 提示再勾一位联系人）。

### 复核结论

- **P2/P3 修复真实、测试双探针真绿、工程卫生达标 → T3 三 commits 可 merge 进 main。**
- P1（默认路径）与 P2（残留缝）**双封死**：`1 人 + 未搭组织`、`1 人 + 搭组织但组织不会落岗` 两种态均禁建，秘书模式复发窗口全闭。
- 遗留仅 P4 文案小瑕（R6），可并入 T4 或单开 3 行修。
- **T4（任务卡）可开工**——群模型语义已收敛（建群要么 ≥2 人、要么显式落岗位表，无隐式态）。
