# DISTRIBUTION_MANIFEST.md — Vermes 作为 Hermes 上游发行版的契约

> 建立日期：2026-09-21 · 基线：`v2.5.1`（`888bf8a344`）
> 定位：**Vermes = Hermes Agent 的中文发行版（distribution）**，不是平行分叉。
> 目标：**保持 Vermes 独特开发，同时让取长上游从「偶发人工考古」变成「常态化、可度量、低风险」。**

---

## 0. 一句话结论

发行版化的核心不是"改成能 merge 上游"（做不到，见 §1 硬约束），而是
**把「哪些是 Vermes 的、哪些是上游的」用机器可检查的方式固定下来**，
然后让每次取长都走同一条：雷达 → 闸门 → 登记 → 验收 → 回退。

---

## 1. 硬约束（实测，非推断）

| 事实 | 实测命令 | 结果 |
|---|---|---|
| 与上游**无共同祖先** | `git merge-base main upstream/main` | **空**（`--is-shallow-repository=false`，非 shallow 导致） |
| 上游领先 | `git rev-list --count main..upstream/main` | **39,490** commits |
| Vermes 自有 | `git rev-list --count upstream/main..main` | **1,758** commits（自 v1.0.3 起独立演化） |
| 上游速度 | `git describe --tags upstream/main` | `v2026.9.14-4787-g3b7eda0887`（一个 tag 周期后又走了 4,787 个 commit） |
| 同源相似度 | `scripts/diverge_metrics.py`（见 `reports/diverge-baseline-20260920.md`） | `toolsets.py` 0.09 / `model_tools.py` 0.12 / `utils.py` 0.15 —— 深度 diverge |

**推论（必须接受，不要反复尝试推翻）**：

1. ❌ `git merge upstream/main` —— 无共同祖先，不可用
2. ⚠️ `git cherry-pick <upstream-sha>` —— 可执行，但补丁上下文已 diverge，冲突率高；
   仅用于**单文件/单特性的能力级搬运**（`chore/upstream-sync` 分支已有成功先例：
   `a8cb8ebc7a` = per-platform prompt-hint overrides，取自上游 `3ead2bdd0`）
3. ✅ 正确形态：**能力级取长**——看懂上游实现 → 在 Vermes 侧重写/适配 → 契约测试兜底

---

## 2. 分区（机器可检查的归属）

脚本真源：`scripts/upstream_watch.py` 的 `ZONES`（本表与之一致，改一处需同步另一处）。

| 分区 | 路径 | 判定规则 |
|---|---|---|
| **own**（发行版自有·红线） | `vermes_cli/`、`agent/memory_fabric.py`、`agent/capability_evolver.py`、`agent/workflow_runtime.py`、`agent/compression_scheduler.py`、`gateway/platforms/`、`scholarforge/`、`acp_registry/`、`frontend/`、`electron/`、`installer/`、`locales/`、`scripts/build-*` | 上游同名改动**禁止直接搬运**，只参考思路；Vermes 侧在此区改动是**正常开发** |
| **follow**（上游跟随区） | `plugins/`、`tools/`、`harness/`、`cron/`、`.github/`、`docs/`、`scripts/` | 优先跟随上游；Vermes 侧在此区改动 = **契约税**，需登记或外置为插件 |
| **core**（同源核心·已 diverge） | `agent/`、`gateway/`、`acp_adapter/`、`memory/`、`runner/`、`cli/` | 个案评估，需对照 `diverge_metrics` 度量 |

### 红线清单（Vermes 领先项，**任何取长都不得覆盖**）

| 能力 | 实现 | 规模 |
|---|---|---|
| 自进化 | `agent/capability_evolver.py` | 485 行 |
| 记忆织物 | `agent/memory_fabric.py` | 1,377 行 |
| 工作流 DAG | `agent/workflow_runtime.py`（+ scheduler/pipeline） | 291 行 |
| 上下文压缩调度 | `agent/compression_scheduler.py` | 656 行 |
| 中文平台 | `gateway/platforms/` | 40 文件 / 17 平台 |
| 运行时技能库 | `~/.vermes/skills/` | 245 个 SKILL.md |
| 双平台打包链 | `scripts/build-macos*.sh` / `build-windows*` | PyInstaller + DMG / NSIS |
| ScholarForge | `scholarforge/` | 论文写作 27+ 工具 |
| CAD-IR | 用户技能目录 `cad_ir_contract.py` | 27 KB 契约编译器 |

---

## 3. 闸门（每次取长/每次发版前跑）

| # | 闸门 | 命令 | 通过条件 |
|---|---|---|---|
| G1 | **跟随区契约税** | `.venv/bin/python scripts/upstream_watch.py boundary --since <tag>` | 0 处=PASS；≤10 处=WARN（逐条登记）；>10 处=FAIL（先外置） |
| G2 | 上游红线扫描 | `.venv/bin/python scripts/upstream_watch.py watch` | 红线命中项逐条人工判定，不得直接搬运 |
| G3 | 并存不变量 | `python3 scripts/check_coexistence.py --deep` | FAIL 0 |
| G4 | 目标测试 | `pytest <相关文件>` | 0 failed |
| G5 | 版本号一致 | `cat version.txt` + 四处 package.json/pyproject | 全部等于 `vermes_cli/__init__.py:__version__` |

> G1 首次实测（2026-09-21，`v2.4.9..main`）：**契约税 48 处 / 100 commits** → 当前 **FAIL** 档。
> 这是 S1 的主要工作：逐条判定「登记（有意偏离）」还是「外置为插件」。

---

## 4. 取长登记 ledger（每个取长一个条目，写在本文件 §7）

```
### <日期> · <上游 hash 或 PR> · <一句话价值>
- 上游做什么：<...>
- Vermes 差异：<为什么不能直接搬 / 改了什么>
- 冲突面：<命中分区 / 涉及文件>
- 验收：<pytest 命令 + 数字>
- 回退：<开关名 或 revert commit>
```

**判定优先级**（避免无效搬运）：
1. 安全修复（`fix(security)`）→ 最高优先，即使要重写也做
2. 关键 bug（崩溃 / 数据丢失 / 凭据错误）→ 高
3. 上游新增能力但 Vermes 已有等价实现 → **不搬**（红线：不追 provider 适配、不追 UI 重构/TUI）
4. 纯上游品牌/英文文案 → 不搬

---

## 5. 工具

| 工具 | 作用 |
|---|---|
| `scripts/upstream_watch.py watch` | 上游雷达：基线以来上游改动 → 分区分类 → 取长候选 + 红线告警；报告 `reports/upstream-watch-<date>.md` |
| `scripts/upstream_watch.py boundary` | 边界闸门：Vermes 跟随区契约税；报告 `reports/dist-boundary-<date>.md` |
| `scripts/diverge_metrics.py` | 同源相似度度量（Jaccard），判断某个 core 文件还能不能搬 |
| `scripts/check_coexistence.py --deep` | 5 个安装并存不变量（12 项） |
| `reports/.upstream-baseline.json` | 雷达基线，下次只看增量 |

---

## 6. 路线（对齐 `reports/vermes-distribution-ROLLOUT-PLAN_20260920.md` 的 P0–P5，不另起炉灶）

| Sprint | 内容 | 对应 ROLLOUT | 验收（硬） | 工期 |
|---|---|---|---|---|
| **P0** | 冻结一版（**v2.5.1，本次完成**：版本真源对齐 + CHANGELOG + tag） | P0 | tag 存在 + CHANGELOG 完整 + release notes 含已知问题 | ✅ 完成 |
| **S1** | 制度层：雷达 + 闸门 + 本契约（本次落地）+ **上游 canary CI lane** | 贯穿规则 3 | G1 降到 WARN 档（契约税 ≤10 且逐条登记）；canary 周跑一次 | 3–5 天 |
| **S2** | P1 自进化插件化：静态块→`register_system_prompt_section`；动态块→`pre_llm_call`；工具后→`post_tool_call` | P1 | 注入文本与 v2.5.1 **逐字相同**；diff 仅落 `plugins/`；记录一次取长的人时 | 1–2 周 |
| **S3** | P3 记忆织物 → `MemoryProvider`（**先做召回 A/B**） | P3 | 同一批问题，v2.5.1 vs 新版召回并排；变差即停 | 2–3 周 |
| **S4** | P4 中文平台 → `plugins/platforms`（每平台必测断线重连不丢消息） | P4 | 一平台一验收 | 2–3 周 |
| **S5（可选）** | 形态 B：引擎作依赖、Vermes 只留 plugins+品牌+打包 | P5 | 追上游 = 版本号 +1 | 视情况 |

**拍板建议**：做 S1 + S2，**暂不做 S5（形态 B）**。
理由：形态 B 要重做整条打包链（PyInstaller 内嵌 → 依赖安装），且上游是否能作为 pip 依赖安装
**尚未核实**（不得凭假设推进）；而 S1+S2 已能把"取长成本"从考古降到可控，投入产出比更高。
S5 的前置核实项已登记为待办（§8）。

---

## 7. Ledger（取长登记）

### 待判定（来自 `reports/upstream-watch-20260921.md`，2026-09-21 首次扫描）

| 上游 hash | 主题 | 与 Vermes 的关联 | 建议 |
|---|---|---|---|
| `b534f4b8c8cd` | fix(security): match credential env names case-insensitively | **不是** v2.5.1 `key_env` 收口的同域问题（首轮推断有误，2026-09-21 读上游 diff 后更正）。真实作用域：凭据 env **屏蔽名单**在 Windows 上被大小写变体绕过（`openai_api_key` 注册被接受，随后 `os.getenv` 在 Windows 解析到真实 `OPENAI_API_KEY`），经 `env_passthrough` / `docker_forward_env` / `docker_extra_args` / `_filter_secret_env` / `_scrub_credentials` 注入 SSH、Docker exec 与兄弟 profile。Vermes 侧有对应机制：`tools/env_passthrough.py`、`tools/environments/docker.py`、`tools/session_env_registry.py` | 优先核实：Vermes 的屏蔽名单是否同样大小写敏感；若是 → 同类漏洞，取长 |
| `940c6109943a` | fix(security): keep `_HERMES_PROVIDER_ENV_BLOCKLIST` importable | 同一屏蔽名单的可导入性（防止重构后守卫失效） | 随 `b534f4b8c8cd` 一并评估 |

### 已合入

| 日期 | 来源 | 内容 |
|---|---|---|
| 2026-09（在途分支） | 上游 `3ead2bdd0` | per-platform prompt-hint overrides（`chore/upstream-sync` → `a8cb8ebc7a`） |

---

## 8. 待办 / 未决

| # | 项 | 状态 |
|---|---|---|
| T1 | G1 契约税 48 处逐条判定（登记 vs 外置） | 待做（S1） |
| T2 | 上游 canary CI lane（周跑：fetch → 雷达 → 契约测试） | 待做（S1） |
| T3 | 核实 Vermes 的凭据 env 屏蔽名单（`tools/env_passthrough.py` / `tools/environments/docker.py`）是否大小写敏感 —— 若是即与上游 `b534f4b8c8cd` 同类漏洞 | 待做（高优先） |
| T4 | 形态 B（引擎作依赖）前置核实 | **已核实 2026-09-21**：`hermes-agent` **确实在 PyPI**（`https://pypi.org/pypi/hermes-agent`，作者 Nous Research，MIT，requires Python ≥3.11 <3.14，extras 覆盖 wecom/feishu/dingtalk/acp/mcp 等 40 项）。**但 PyPI 最新版 0.19.0 落后于 GitHub v0.21.3**（tag `v2026.9.14`）→ 形态 B 有路径，代价是**跟随版本落后上游 2 个小版本**，且需重做打包链 |
| T5 | `scripts/sync-version.sh` 在本机 shell shim 下静默失败（EXIT=1 无输出），v2.5.1 改用 jq/sed 同步 | 待修 |

---

## 9. 证据索引

| 结论 | 证据 |
|---|---|
| 无共同祖先 / divergence 数字 | `git merge-base --all main upstream/main`（空）· `git rev-list --count` · `git rev-parse --is-shallow-repository=false` |
| 上游 HEAD 与速度 | `git describe --tags upstream/main` = `v2026.9.14-4787-g3b7eda0887` |
| 契约税 48 处 | `reports/dist-boundary-20260921.md`（`v2.4.9..main`，100 commits） |
| 上游候选 300 commits / 810 文件 / 36 红线 | `reports/upstream-watch-20260921.md` |
| 分区定义真源 | `scripts/upstream_watch.py` 的 `ZONES` |
| 冻结基线 | tag `v2.5.1` @ `888bf8a344` · `CHANGELOG.md [2.5.1]` · `version.json` |
| 前序路线 | `reports/vermes-distribution-ROLLOUT-PLAN_20260920.md`（P0–P5） |
| 同步策略（旧） | `UPSTREAM_SYNC.md`（本文档为其工程化落地） |
