# 交叉审计：MiMo「已落地」批次（A1 清税 / T2 两脆弱点 / S2.0 gold / S2.1 adapter）

- 审计方：QClaw（WorkBuddy）
- 审计时刻：`main` @ `d3d7822aac` == `origin/main`（**已 push**）；MiMo 本批改动**全在工作树未提交**，`git diff --cached` 为空（无半暂存风险）
- 被审计对象：MiMo 的落地说明（回应 Hermes 审计）+ Hermes 对 T2/S2 的复核意见
- 结论：**PASS，可放行**。声称逐条成立，数字精确吻合；我新提 3 条低档观察 + 1 条 P2（机器产物/基线入库），并对 A7 给出与 Hermes 部分分歧的合成方案。
- **最重要的一条在 §0：我要撤回上一轮传播的错误根因，它差点让我误改正常代码。**

---

## 0. 撤回：我上轮说的「BSD grep 不支持 `\|`」是错的

上一轮我把「两本账零命中」归因于 *macOS BSD grep 不支持 BRE 的 `\|` 转义分支*，Hermes 复核时称「实测复现了该错误模式」。**这条不成立，我撤回，并向两方更正。**

| 项 | 实测（本次） |
|---|---|
| grep 身份 | `/usr/bin/grep` = `grep (BSD grep, GNU compatible) 2.6.0-FreeBSD` |
| `printf 'alpha\nbeta\ngamma\n' \| grep -c 'alpha\|beta'` | **2** —— `\|` 正常工作，不是字面量 |
| `grep -E 'alpha\|beta'` / `-e alpha -e beta` | 同为 2（三者一致） |

**真实根因（订正）**：不是 grep 语法，而是**搜索作用域/过滤链**——例如用 `--include="*.py"` 去找只存在于 `.md` 的标记，或管道里叠了 `grep -v … | head -N` 造成截断；叠加通病：把「0 命中」直接判成「对象不存在」。

**这条差点造成实质损害**：我据此去做 Hermes 点名的仓库级体检，`scripts/dev-check.sh:121/135` 两处 `grep 'A\|B'` 被我列为待修。实跑验证后确认**它们一直是正确生效的**：

- `:121` `grep -v 'test\|example\|placeholder\|…'` → 22 行原始命中正确过滤到 1
- `:135` `grep -rn 'DOMPurify\|sanitize' frontend/src/` → **DOMPURIFY=25**（前端确有 12 处 `v-html`），7b 判定「✅ 有防护」正确

→ **`dev-check.sh` 不需要改**，我已放弃修改。已同步订正 `~/.workbuddy/MEMORY.md`（跨项目纪律条目），避免继续传染。

---

## 1. 声称核实表（逐条实测）

| # | MiMo 声称 | 实测证据 | 判定 |
|---|---|---|---|
| 1 | A1 补登记 D-007 / D-008 | `docs/DISTRIBUTION_MANIFEST.md:163-164` 命中，含 commit hash 与「有意偏离」说明 | ✅ |
| 2 | canary 两文件归 own 区 | `upstream_watch.py` `ZONES.own` 新增 4 条：`scripts/upstream_canary.py`、`scripts/s2_snapshot.py`、`.github/workflows/upstream-canary.yml`（+原文 watch） | ✅ 且**多归了 `s2_snapshot.py`**（声称未提，合理） |
| 3 | 分区表措辞（A2） | 改为「跟随区改动（未登记明细见 §2）」，并留更正注释 | ✅ |
| 4 | boundary 零未登记税 | 实跑：`[boundary] commits=60 未登记税=0 已登记偏离=45` | ✅ |
| 5 | T2 脆弱点1：绑 venv | 新增 `default_python()`：`.venv/bin/python` 存在即优先，docstring 记录了「系统 Python 3.14 vs venv 3.11」的踩坑 | ✅ |
| 6 | T2 脆弱点2：句柄分级 | `handle_severity()`：`w`/`u` → FAIL，其余（含 `r`/`?`）→ WARN；`open_paths` 改 `-Ffn` 取 FD 模式位 | ✅ |
| 7 | S2.0 gold 16 场景 × 3 段 | `reports/s2/gold/` = 48 个 `.txt` + `manifest.json`（`scenario_count: 16`）；矩阵 model 4 × platform 4 × toolset 3 的 pairwise 16 条 | ✅ |
| 8 | volatile 日期白名单归一 | `volatile_normalizers: ["^Conversation started: .+$", "^Session ID: .+$"]`，S01 volatile 内确实含 `{{DATE}}` | ✅ |
| 9 | S2.1 adapter：Callable 强制 volatile | `content` 为 Callable 且 `layer="stable"` 且无 `stable_reason` → 直接 `ValueError` | ✅ |
| 10 | L-014 三护栏 | id 正则 `^[a-z0-9][a-z0-9._-]{0,127}$`；`max_chars` 默认 8000；同 id 重复注册报「已被 X 注册」 | ✅ |
| 11 | A4 优先级 plugin < builtin < user | `load_all_processors()` 顺序：plugin 先占位 → builtin 覆盖 → user 覆盖 | ✅（日志见 §2 P3-3） |
| 12 | 未改现有注入点 | `plugins.py` 纯新增 `PluginContext.register_system_prompt_section` + `PluginRegistration`；`prompt_processor_loader.py` 新增注册表与合并逻辑，未动既有注入路径 | ✅ |
| 13 | 契约测 56 passed | 实跑 6 个文件：`56 passed in 39.09s` | ✅ 精确吻合 |
| 14 | 未夹带 `feedback_tool.py` / `web_dist` | 二者在工作树为他人改动，`git diff --cached` 为空 | ✅ |

---

## 2. 我发现的新问题

### 🟢 gold 门闩变异测试（先报好消息）
S2.2 全靠 gold 拦截回归，所以我做了一次变异：在 `S01.stable.txt` 末尾追加一行 → `test_rebuild_matches_gold_byte_for_byte` 与 `test_cli_check_entrypoint` **2 failed**；还原后 5 passed，`md5` 与备份一致。**门闩有牙，不是走过场。**

### 🟡 P3-1：16 场景只有 13 个唯一 stable 指纹
`S09≡S13`（e83e02da）、`S02≡S14`（41cd67a6）、`S04≡S16`（0153367f）——差异对全是**同 platform + 同 toolset、仅 model 不同**。说明当前 **model 维度对 stable 段贡献 0 差异**（没有 `model_affinity` 触发）。
→ 今天合法，但「16 场景」名义覆盖 > 实际 13 路。建议 manifest 里显式标注唯一指纹数，避免以后被读成 16 条独立护栏（不虚标覆盖）。

### 🟡 P3-2：context 段 16/16 全 0 字节 → 该层零覆盖
快照用 `skip_memory=True` / `skip_context_files=True` 保确定性，**恒空是有意的**；且实测 36 个 YAML 中**仅 1 个声明 `layer:`（且为 stable）**，0 个声明 context/volatile。所以当前 context 层的 processor 贡献本就是 0。
→ 今天无害；**但 S2.2 一旦把块迁进 context 层，gold 对该层的回归将无护栏**（stable 不缩、context 恒空 → 静默通过）。建议 S2.2 立项时先扩一个「带 context 输入」的场景。

### 🟡 P3-3：A4 覆盖日志只打了一半（Hermes 那条没完全落实）
Hermes 要求「同名胜出者要显式打印」。实测：
- plugin → builtin：`logger.info("Builtin processor '%s' overrides plugin-registered section (owner=%s)")` ✅ INFO，看得见
- user → builtin/plugin：`prompt_processor_loader.py:551` 仍是 `logger.debug("Loaded user processor: … overrides=%s")` ❌ 生产默认 INFO 下不可见

→ 建议把这条提到 INFO（或新增一条 INFO 摘要），否则「用户级覆盖静默生效」仍难查。

### 🟠 P2：机器产物入库污染 + **gold 基线整个未跟踪**
- 未跟踪：`reports/canary/20260923-003755Z.md` 等 3 份、`reports/dist-boundary-2026092{2,3}.md`、`reports/.canary-state.json`、`reports/upstream-intent-20260923-*.md`
- 历史已误入库：`reports/canary/20260923-000746Z.md`、`reports/dist-boundary-20260921.md`（我上一轮提交的，认领）
- **风险最大的一条：`reports/s2/`（含 gold 基线 48 段 + manifest）整个 untracked。** 基线丢一次，S2.2 的「逐字等价」就没了判据。

处置（本次已做）：`.gitignore` 新增「巡检脚本机器产物」段（canary/dist-boundary/upstream-intent/upstream-watch/.canary-state.json），并**显式标注两个必须入库的例外**：`reports/s2/gold/**` 与 `reports/.upstream-canary-pin.json`。

---

## 3. A7 回退开关：我的建议（与 Hermes 部分分歧）

Hermes 主张做（桌面小白无法 git 回滚、cache 前缀回归需重发版），我原方案是不做（避免永久双轨）。**合成方案**：

| 方案 | 评价 |
|---|---|
| ❌ 新旧两套装配路径 + env 切换 | **不做**。等于永久双轨 + 两份真相，正是发行版化要消灭的东西；且双轨下 gold 要维护两份基线 |
| ✅ **插件段禁用名单**（推荐） | `VERMES_DISABLE_PROMPT_SECTIONS=id1,id2`：在既有 `register_plugin_processor` 前置过滤，不注册即不注入。**零双轨代码路径**、env 生效、无需发版、可观测（禁用时打 INFO） |
| 时机 | **不在 S2.1 做**——S2.1 未动任何注入点，现在做是无用功。**S2.2 第一次真正动注入点时同步落地**，并设退役日期（建议 `v3.0.0-distribution` 冻结前移除，过期由契约测报警） |

理由：Hermes 担心的「cache 前缀回归无法自救」是真的，但解法不必是双轨——禁用名单同样能一键回到无插件注入的状态，成本只有十几行。

---

## 4. 待董董拍板 / 处置

| # | 事项 | 建议 |
|---|---|---|
| 1 | 是否由我提交 MiMo 这 8 个文件（含 **gold 基线**） | 我**未提交**（避免抓到并发半态）。建议提交，尤其 `reports/s2/gold/**`——未跟踪的基线是最脆的一环 |
| 2 | push | `main == origin/main` 已同步；本批未提交故无可推内容 |
| 3 | S2.2 首个迁哪个块 | 按工单：先 `identity`（always 注入、无条件依赖）做 walking skeleton；第二个迁 `editing_guardrails`（`_PROCESSOR_FALLBACK` 15 键中**唯一**仍硬编码的，迁完可整条退役该常量） |
| 4 | A7 形态 | 采纳「插件段禁用名单」，S2.2 落地 + 退役日期 |
| 5 | 挂起项（T5） | L-009 清单、T1 历史税、AGENTS.md 12 目录——Hermes 并行中，未冲突 |

---

## 5. 本次我改的文件（仅自己范围内，按路径提交）

- `.gitignore`：新增巡检产物忽略段 + 两个「必须入库」例外标注
- `~/.workbuddy/MEMORY.md`：订正错误根因（跨项目纪律）
- 本报告
- **未改**：`scripts/dev-check.sh`（验证后确认无 bug，放弃修改）、MiMo 的任何文件
