# 审计备忘录 · mimo T1 神魔堂 IM 左栏（feat/shenmotang-ux-leftbar）

> 审计者：WorkBuddy（技术审计型搭子）｜ 被审计：mimo 的「T1 完成 + C1–C8 核验」报告
> 方法：`audit-completion-report` 四步（验 commit 真身 → 验文件落地 → 实跑测试 → 查新引入风险面），不采信口播。
> 纪律：否定性结论不建在截断输出上；grep 用 Grep 工具（本机 Bash grep 是 shim，会假阴性）。

---

## 一、报告声称 vs 实测（对照表）

| # | mimo 声称 | 实测 | 结论 |
|---|---|---|---|
| C1 | 44 recipes，44/44 `transport: acp` | `find … -name '*.yaml' \| wc -l`=44；`grep -h "^transport:"` 计数 44/44 | ✅ 真 |
| C2 | Kimi/Qwen-Code/GLM ACP-native | `registry/*.yaml` 实测 `transport: acp` | ✅ 真 |
| C3 | kanban 真枚举 `triage…archived`，无 `dispatched/planning` | `kanban_db.py:97-98` | ✅ 真 |
| C4 | 3-tab 已收编 | `router/index.js` `/kanban` `/bot-rooms` → `/shenmotang` | ✅ 真 |
| C5 | 双入口独立 | `/`(ChatView) + `/shenmotang` + Sidebar ⛩️ | ✅ 真 |
| C6 | `peer_dm` + @B + 跨群联邦已通 | `chat.py:4282`(peer_dm) `/4286`(@B) `/4317-4318`(跨群联邦) | 🟡 真，但锚点应写 **4317** 非 4316 |
| C7 | 双模式决议 | spec §0.2（董董拍板） | ✅ 真 |
| C8 | BSL 红线在纪律里 | `AGENTS.md:60` | ✅ 真 |
| T1 | 新增 `ShenmotangImRail.vue` | `frontend/src/components/ShenmotangImRail.vue` 真实存在（129 行） | ✅ 真 |
| T1 | 后端 0 新增，复用 `/agents/contacts` + `/bot/rooms` | 两端点本就存在（`chat.py:6389/7142`、`chat.py:3939/7249`）；`api.js:658/719` 的 `listBotRooms/listAgentContacts` 真实 | ✅ 真（无幽灵方法） |
| T1 | 「反转进堂强收侧栏」 | `Shenmotang.vue` 已删除 `onMounted`/`afterEach` 的 `chat.sidebarOpen=false` 逻辑，改由 `ShenmotangImRail` 常驻 | ✅ 真，且对齐双模式定调 |
| T1 | 契约测 3 passed · build 通过 | `npx vitest run tests/shenmotang-im-rail.test.js` → **3 passed（实跑）**；测试含 fetch mock + 源码断言「不劫持 sidebarOpen」，非镜像实现反模式 | ✅ 真绿 |

---

## 二、问题清单（按严重度）

### ❌/🟡 P1 — `web_dist` 污染（merge 前必须拦下）
- **证据**：分支 3 commits 含 `7c4ba88062 build(frontend): web_dist 随 T1 IM 左栏重建`；`git diff --stat main..HEAD -- vermes_cli/web_dist` = **16 个已跟踪产物文件**被改写（含其他组件 chunk 重哈希 + `index.html` 脚本引用 + 1 行 css）。
- **为什么是坑**：`vermes_cli/web_dist/` 是**已跟踪**的 PyInstaller/Electron 嵌入产物，`frontend/npm run build` 跑一次即删除旧 chunk + 改 index.html + 新增 chunk（项目记忆工程坑 #1，明确警告「直接踩发行版化 agent 的工作面」）。
- **后果**：① merge 回 main 必与任何并行前端改动冲突；② 该 commit 顺带把**其他组件**的 chunk 重哈希一并提交，**夹带了非本功能的产物变更**。
- **修复方向**：分支只保留源文件 + 测试 + README；`web_dist` 留给 DMG/Electron 构建时再生。具体：`git checkout main -- vermes_cli/web_dist`（在该分支），或 `git revert --no-commit 7c4ba88062` 后只保留其非 web_dist 部分。

### 🟡 P2 — 根目录散落文件复发 + 看板口径不准
- **证据**：git status 显示根目录又出现 `?? index.html` / `?? app.js`（看板页），与已归档的 `reports/index.html` / `reports/app.js` **重复**。此前已 `mv` 进 `reports/` 并警告过，现复发。
- **看板口径不准**：看板页（及 mimo 报告）把分支写成「T1 + T6 两个 commit」，但分支实为 **3 commits**——漏算的正是 `7c4ba88062`（web_dist build）。等于**未披露 P1 污染**。
- **C6 锚点漂移**：看板写 `4282/4286/4316`，跨群联邦实为 `chat.py:4317-4318`，应改 4317。

### 🟡 P3 — 双左栏 UX 取舍（非缺陷，待视觉确认）
- 反转侧栏劫持后，神魔堂内**同时显示全局侧栏 + IM 左栏**两个左侧面板。在双模式定调下可接受（全局侧栏放 ⛩️ 入口与单聊列表，IM 左栏放神魔堂联系人），但需确认横向空间与视觉层级不打架。

---

## 三、mimo 做对了什么（诚实，不只挑刺）
1. **C 清单逐条回盘，不沿旧口播**——尤其推翻了自己早先的「6+ recipes」「dispatched 状态」旧结论，符合 §11 设闸意图。
2. **端点/方法零新增、零幽灵调用**——`ShenmotangImRail.vue` 的 `load()` 走真实 `api.listAgentContacts/listBotRooms`，且有 `.catch(()=>null)` 兜底与空态处理，鲁棒。
3. **测试真绿且非镜像实现**——`npx vitest run` 实测 3 passed；第三个用例直接读 `Shenmotang.vue` 源码断言「不含 `chat.sidebarOpen = false` / `wasSidebarOpen`」，把行为契约锁死。
4. **反转侧栏劫持对齐双模式**——把神魔堂从「收起全局侧栏的实验场」改成「自带常驻 IM 左栏的重模式」，正是 spec §0 的方向。
5. **交付节奏守纪**——本地提交、不自行 merge/push，等董董审。

---

## 四、merge 前行动建议（交董董 / 转 mimo）
1. **【必做】剔除 web_dist**：在 `feat/shenmotang-ux-leftbar` 上 `git checkout main -- vermes_cli/web_dist`，使分支只含 `ShenmotangImRail.vue` + `Shenmotang.vue` + 测试 + README。
2. **【必做】清理根目录看板副本**：把根 `index.html/app.js` 移入 `reports/`（或删根目录副本），避免 `git add .` 误提交进真源。
3. **【顺手】看板 C6 锚点 4316→4317**，并把「2 commits」更正为「3 commits（含 web_dist build，待剔除）」以诚实披露。
4. **T2（1:1 默认面）待董董口令再开**——勿提前开工。

> 审计结论：T1 功能实质达标、可合并；唯一硬阻塞是 P1 的 `web_dist` 污染，剔除后即可审合并。

---

## 五、复审（2026-09-26 15:40 · mimo 修复后回盘）

mimo 提交修复报告后，WorkBuddy 不采信口播，按原四步逐项复验。**结论：P1/P2 已真修复，T1 分支验收通过、可 merge。**

### 复验证据（全部实跑/实读）

| 项 | 复验命令 / 动作 | 结果 |
|---|---|---|
| P1 · web_dist 零差异 | `git --no-pager diff --name-only main..feat/shenmotang-ux-leftbar` | ✅ 仅 4 文件：**README.zh-CN.md / Shenmotang.vue / ShenmotangImRail.vue / shenmotang-im-rail.test.js**，零 `web_dist` |
| P1 · 无未跟踪残留 | `git --no-pager status --short -- vermes_cli/web_dist` | ✅ 空（13 个新 hash 残留确已清，checkout 不自动清的部分已手删） |
| P1 · 剔除 commit 行为正确 | `f9e41506ab fix(distribution): 剔除 T1 夹带 web_dist 产物污染` | ✅ 仅恢复 web_dist 到 main 状态，未误删其它源文件 |
| P2 · 根目录干净 | `git --no-pager status --short -- index.html app.js style.css` | ✅ 空（根目录无散落） |
| P2 · 看板已归档 | `ls reports/shenmotang-ux-kanban_20260926.html` | ✅ 存在；与 reports 内旧产品方案页共用 style.css/app.js |
| P2 · C6 锚点更正 | `reports/shenmotang-ux-kanban_20260926.html:43` | ✅ `4282/4286/4317`（注释在 4316，逻辑在 4317-4318） |
| P2 · commits 口径 | `git log --oneline main..feat/shenmotang-ux-leftbar` | ✅ 4 commits：`a84896bf29`(T1) / `7c4ba88062`(污染build) / `8753987453`(T6 README) / `f9e41506ab`(剔除)，并披露 P1 已处理 |
| 功能 · ImRail 真挂上 | `Shenmotang.vue:7` import + `:44` 渲染 `<ShenmotangImRail` | ✅ 非写而未用 |
| 功能 · 测试真绿 | `npx vitest run tests/shenmotang-im-rail.test.js` | ✅ **3 passed**（vitest v4.1.10；两处 Vue Router warn 为 mock 路由噪音，不影响通过） |

### 唯一遗留：工作树漂移 `verdicts.jsonl`（🟡 非 mimo 责任，与 T1 无关）

- 现象：`git status` 显示 ` M reports/canary-schedule/verdicts.jsonl`（+1 行）。
- 定性：它是 **canary 定时监控今晨 `20260926T011705Z` 的运行产物**（tracked 文件，记录 boundary/gold/canary 返回码），属另一 agent 的自动化输出，**不在 T1 4 文件交付集**。
- 影响：merge T1 分支只带入那 4 个已提交文件，不会触碰 verdicts.jsonl；但工作树不干净时 `git merge` 可能要求先 stash/commit。
- 建议：merge 前把该 1 行**单独提交**（或 stash），勿混进 T1 merge；或明确丢弃（运行产物，可重跑）。**与 mimo 无关，不计入本次验收。**
- 附带：reports/ 下另有 6 个未跟踪产物（WorkBuddy 先前生成的报告/落页），非 mimo 责任，建议后续统一 `git add` 进 reports/ 或加 `.gitignore`。

### 复审定稿

> **T1 分支 `feat/shenmotang-ux-leftbar` 验收通过，可 merge。** 已提交差异干净（4 文件、零污染、测试绿）；P1/P2 整改均经回盘核实为真。merge 前仅需处理 unrelated 的工作树漂移 `verdicts.jsonl`（canary 产物，非本交付）。T2（1:1 默认面）仍待董董口令，勿提前开工。

### 终验收口（2026-09-27 06:31 · mimo 隔离遗留项后）

mimo 将 `verdicts.jsonl` 那行 canary 全绿记录单独 `git stash`（stash@{0}），使工作树 tracked 部分干净、merge 不再触碰它。WorkBuddy 复验：

| 项 | 复验动作 | 结果 |
|---|---|---|
| 分支净变更 | `git diff --name-only main..feat/shenmotang-ux-leftbar` | ✅ 仍只 4 文件（README / Shenmotang.vue / ShenmotangImRail.vue / 测试），零 web_dist |
| web_dist 零残留 | `git status --short -- vermes_cli/web_dist` | ✅ 空（tracked 差异 + untracked 残留均为零） |
| stash 真存在且内容正确 | `git stash list` + `git stash show -p stash@{0}` | ✅ stash@{0} 标注「canary-verdict-20260926T011705Z 全绿」，patch 仅为 verdicts.jsonl **+1 行**那条全绿记录（`boundary_rc:0/gold_rc:0/canary_rc:0/unregistered_tax:0`），非 T1 交付 |
| 工作树 tracked 干净 | `git status --short`（tracked 部分） | ✅ 无 tracked 修改；仅余 6 个 `??` 未跟踪报告产物（WorkBuddy 先前生成的 reports/ 落页/备忘录，非 mimo 交付、不进分支） |

**终验结论：T1 分支 `feat/shenmotang-ux-leftbar` 已可随时 merge。** 工作树干净、merge 安全；源文件较昨日无变化，契约测沿用 2026-09-26 实跑结果 **3 passed**（免重复跑）。

**后续动作（交董董）**：
1. merge 进 main：`git checkout main && git merge feat/shenmotang-ux-leftbar`（不 force、不 push，按协作约定审计+push 由董董负责）。
2. merge 后：`git stash pop`（stash@{0}）→ 单独 `git add reports/canary-schedule/verdicts.jsonl && git commit -m "chore(canary): 记入 9/26 全绿 verdict"` 保留历史，不混进 T1 merge commit。
3. ⚠️ 勿误 pop `stash@{1}`：那是**另一 agent 在 main 上的 gateway 修复 WIP**（`WIP on main: 7bb6803d9f fix(gateway): 治本③ channel-dir 关停覆盖 + 不堆叠`），与 T1 无关，属并行 agent 遗留，待其 owner 处理。
4. reports/ 下 6 个未跟踪产物建议后续统一 `git add` 进 reports/ 或加 `.gitignore`，避免 `git add .` 误带（不阻塞本次）。

> T2（1:1 默认面）仍待董董口令，mimo 守纪未开工。
