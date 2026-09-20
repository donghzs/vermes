# 致 mimo · Vermes 上游对齐并行分工（2026-09-20）

> 这份文档**自包含** —— 你不需要读我们前几轮的讨论。读完即可开工。
> 唯一需要你先知道：本轮目标是**让 Vermes 取长上游 `NousResearch/hermes-agent`**，
> 路线图正文在 `reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`（唯一真源，只读不改）。
> 你和我（WorkBuddy）**并行推进不同文件**，互不阻塞；产出后**交叉审计**。

---

## 一、背景（3 句）

1. Vermes 是 hermes-agent 的中文本地化 fork（现 v2.4.9，上游 v0.21.3）。
2. 四份调研报告已合并核实成终版路线图，结论：**差距主因不是"哪个功能没搬"，是制度层缺了一半**
   （上游 12 个按目录写的 `AGENTS.md` vs 我们 1 个；CI 35 条 vs 14 条）。
3. 契约层第一项（home channel 单一口径）WorkBuddy 已做完并 commit（`b7305707d4`）。
   **剩下的活按文件足迹切成两半，你一半我一半。**

---

## 二、你的四个工单（M1–M4）

### M1 · 分歧度量脚本落地（A4）· 0.25 天

- **做什么**：把 `~/.hermes/skills/hermes-vermes-architecture/scripts/diverge_metrics.py`
  **搬进仓库** `scripts/`，适配路径，跑出第一份基线。
- **⚠️ 别从零写** —— 脚本已存在且可用，这是"搬 + 适配"，不是"新建"。
- **验收**：能真跑出三组数字 —— ① 同源文件 Jaccard ② 工具 schema token 规模 ③ 静默失败提交数。
  基线写进 `reports/diverge-baseline-20260920.md`。

### M2 · 重写 `UPSTREAM_SYNC.md`（A5）· 0.5 天

- **做什么**：这份文档**烂掉了** —— 版本基线还停在「上游 0.18.2 / Vermes v2.3.1」，
  实际是 **上游 v0.21.3 / Vermes v2.4.9**；品牌、remote 描述都不符。
- **⚠️ 所有数字必须实测，禁止沿用旧数字**。实测命令：
  ```bash
  cat version.txt                      # 本地版本
  git remote -v                        # upstream/upstream2 → NousResearch/hermes-agent
  curl -s "https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=5"
  ```
- **关键判断（别写反）**：`git remote` **没坏**，坏的只是文档描述。别去"修管道"。
- **验收**：版本基线 = 实测值；remote 描述与 `git remote -v` 一致；不同步类别清单更新到 v0.21.3。

### M3 · 补 3 条 CI lane（A6）· 1 天

- **做什么**：本地 14 条 → 补上游独有但我们有价值的三类：
  `js-tests.yml`、`tests-os.yml`(mac+linux)、`install-e2e.yml`(mac)。
- 上游 35 条里可参照的同名文件：`install-e2e.yml` / `install-e2e-macos-run.yml` /
  `install-e2e-windows-run.yml` / `tests-os.yml` / `js-tests.yml` / `lockfile-diff.yml`
  （`uv-lockfile-check.yml` 我们**已有**，别重复）。
- **验收**：lane 在 PR 上真实生效；**不得误伤现有 14 条**。
- **⚠️ 这个会触发真实流水线** —— 拿不准的 lane 先写成 `if: false` 占位，别直接开跑。

### M4 · GUI 设置入口 + 首条 DM 自动设定（A7）· 2–3 天

- **为什么需要**：实测 `grep -rn "home_channel|homeChannel|默认通知" frontend/src` **零命中** ——
  前端**根本没有** home channel 的入口。已做完的 A1/A2 只解决「**设了能被认**」，
  **不解决「没地方设」** —— 不做 M4，本轮收益只对命令行用户生效。
- **后端落点已备**：`vermes_cli/gateway_channels.py`（33KB）。
- **⚠️ 别引入新口径** —— 判定与投递必须走共享解析器
  `gateway/gateway_utils.py:resolve_home_channel_chat_id()`（env → legacy env → config）。
  自己在前端另起一套判定 = 又造一个双口径，正是本轮要消灭的东西。
- **验收**：GUI 能设 home channel；设完 **当次生效**（不必重启）。

---

## 三、文件足迹边界（**不冲突的保证**）

| 执行者 | 你独占 | 你**绝不碰** |
|---|---|---|
| **mimo（你）** | `scripts/`、`UPSTREAM_SYNC.md`、`.github/workflows/`、`frontend/src`、`vermes_cli/gateway_channels.py`、`reports/diverge-baseline-*.md` | `gateway/`、`cron/`、`tests/gateway/`、`vermes_state.py` |
| **WorkBuddy（我）** | `gateway/`、`cron/`、`tests/gateway/` | 你上面那一栏 |

> 两侧**零交集**。真需要跨界 → 在 `docs/TASK_BOARD_20260920.md` §4 记一笔，**别直接动**。

---

## 四、红线（违反即停）

1. **不碰 `vermes_state.py`** —— 2.5 的 B4/B8 正在改它（route_ledger 新表）。
   state.db 六件套必须**等 2.5 A2A/kanban 收口**并过 route_ledger 回归闸门，**现在谁都别动**。
2. **不 push** —— 本地提交你做，**审计 + push 由董董负责**。
3. **不覆盖 Vermes 领先项**：workflow DAG / memory fabric / 自进化 / 中文平台 / 245 技能 /
   打包链 / ScholarForge / cadir_build。
4. **动手前先 `git status --short`** —— 看在途改动是谁的，别覆盖别人的活。
5. **「已落盘」必须回读验证**：`ls -la` + `wc -c`。**Edit 回执成功 ≠ 落盘** ——
   本轮 WorkBuddy 就翻过一次车（声称落盘的文件实际不存在）。
6. **否定性结论禁用 shell `grep`** —— 本会话有实证假阴性（`ls | grep` 在含 5 个匹配项的目录返回空）。
   用 Grep 工具或 `ls -1` 全量列举。

---

## 五、我这半边（你只需知道，不用做）

| ID | 内容 | 落点 |
|---|---|---|
| W1 | 品牌大小写不一致**定点**判定（irc 两处） | `tests/gateway/test_irc_adapter.py:223/379` |
| W2 | E 类 4 条 reconnect 失败根因判定 | `tests/gateway/test_platform_reconnect.py` |
| W3 | email self-message 过滤（**疑真 bug**，若是则修，禁止隔离了事） | gateway email 侧 |
| W4 | A3 提示去重（独立 `notices` 域，不污染 `gateway/status.py`） | gateway 侧 |

> **W1 口径提醒**（避免你也去做无用功）：全仓 `VERMES_` 大写残留 **768 行**，
> 但绝大多数是**环境变量前缀**（本该大写，如 `VERMES_HOME`），**不是 bug**。
> 真问题只有 irc 那两处混用。**禁止全仓 sweep** —— 会造 768 行无意义 diff。

---

## 六、交叉审计（产出后必做）

| 审计方 | 对象 | 要点 |
|---|---|---|
| **你审我** | W1–W4 | ① 判定是否带源码行号证据 ② W3 若真 bug 是否真修了 ③ W4 去重是否真持久化 ④ 我有没有越界 |
| **我审你** | M1–M4 | ① M2 数字是否实测 ② M1 能否真跑出数字 ③ M3 会不会误伤现有流水线 ④ M4 有没有引入新口径 |

审计纪律：commit 用 `git cat-file` 验真、文件用 `find`+`git diff` 验落地、测试实跑、
**专门找"新引入了什么"**。详见 `~/.workbuddy/skills/audit-completion-report/`。

---

## 七、环境速查（实测有效，直接用）

```bash
cd ~/projects/vermes-electron            # 注：~/projects 是 ~/Projects 的软链，同一目录
.venv/bin/python                          # Python 3.11，构建与跑测都用它

# pytest（沙箱下默认 tmpdir 会被拦，必须指定 basetemp；xdist 会崩，必须关）
BT=$(mktemp -d /tmp/vtest.XXXXXX)
.venv/bin/python -m pytest <路径> -p no:xdist -o addopts="" --basetemp="$BT" -q

git --no-pager <cmd>                      # 不加 --no-pager 会进 pager 卡死（exit 137）
```

gateway 全量套件很慢（27 分钟）→ **优先跑定向子集**。

---

## 八、完成后怎么交接

1. commit（**路径限定 `git add`，不 `git add -A`**，不 push）。
2. 更新 `docs/TASK_BOARD_20260920.md` 里你那几行的状态 `⏳ → ✅`。
3. 回一段话给我：做了什么 / 实测数字 / **你自己拿不准或改了主意的地方** / 需要我审什么。

**最后一条最值钱** —— 本轮最有价值的一条记录是「主动披露自己上一轮声称落盘的文件实际不存在」。
自我披露比任何"已审计通过"都可信。照这个标准来。
