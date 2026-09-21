# S1 契约税逐条判定（2026-09-21 08:36）

> 区间 v2.4.9..main，50 处跟随区改动，逐条判定：登记（有意偏离）vs 外置（移出跟随区）

## 判定标准

- **登记**：改动是有意的 Vermes 偏离，与上游不冲突或冲突可接受，记入 DIVERSION_LEDGER
- **外置**：改动应移出跟随区（如 docs 应移到 docs/vermes/，scripts 应移到 scripts/vermes/），消除未来冲突

## 逐条判定

### 一、docs/ 类（29 处）— 全部判定：**外置到 docs/vermes/**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| docs/TASK_BOARD_20260920.md | 13 | 外置 | Vermes 工单板，上游无此文件 |
| docs/AGENT_SKILLS_INDEX.md | 3 | 外置 | 跨 agent 技能索引，Vermes 独有 |
| docs/CROSS_AUDIT_M_20260920.md | 2 | 外置 | 审计报告 |
| docs/DISCUSSION_p2_coding_context_mimo_20260920.md | 2 | 外置 | 三方讨论 |
| docs/SPEC_coding_mode_vermes_20260920.md | 2 | 外置 | Vermes 规格文档 |
| docs/HANDOFF_*.md (3 个) | 3 | 外置 | 交接包 |
| docs/AUDIT_SKILLS_INDEX_BY_QCLAW_20260920.md | 1 | 外置 | 审计报告 |
| docs/POINT_CHECK_A1_CHANNEL_GATE_BY_MIMO_20260920.md | 1 | 外置 | 点验报告 |
| docs/QCLAW_POINTER_SKILL_TO_PASTE.md | 1 | 外置 | 指针文档 |
| docs/SPEC_coexistence_invariants_20260920.md | 1 | 外置 | 规格文档 |
| docs/DISTRIBUTION_MANIFEST.md | 1 | 外置 | 发行版清单（本轮新建） |

**操作**：`mkdir -p docs/vermes/ && git mv docs/TASK_BOARD_20260920.md docs/vermes/ ...` 批量迁移。上游 docs/ 目录结构不同，未来不会冲突。

### 二、scripts/ 类（7 处）— 全部判定：**外置到 scripts/vermes/**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| scripts/migrate_plaintext_provider_keys.py | 2 | 外置 | Vermes 专属迁移脚本 |
| scripts/measure_skill_usage.py | 1 | 外置 | 技能度量 |
| scripts/measure_skills_index_p1.py | 1 | 外置 | 技能度量 |
| scripts/diverge_metrics.py | 1 | 外置 | 分歧度量 |
| scripts/check_coexistence.py | 1 | 外置 | 共存检查 |
| scripts/upstream_watch.py | 1 | 外置 | 上游雷达 |

**操作**：`mkdir -p scripts/vermes/ && git mv scripts/{migrate_plaintext_provider_keys,measure_skill_usage,...}.py scripts/vermes/`

### 三、tools/ 类（5 处）— 判定：**登记**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| tools/kanban_tools.py | 4 | 登记 | Vermes 看板工具，上游有同名但内容深度分叉（Jaccard 低），登记为有意偏离 |
| tools/skills_tool.py | 1 | 登记 | 技能工具，添加了热度 tie-breaker，上游有同名文件，冲突可接受 |

### 四、plugins/ 类（2 处）— 判定：**登记**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| plugins/memory/holographic/__init__.py | 1 | 登记 | M6 yaml 保注释修复，上游有同名，改动是 bug fix |
| plugins/platforms/irc/adapter.py | 1 | 登记 | IRC 重连修复，上游有同名，改动是 bug fix |

### 五、harness/ 类（2 处）— 判定：**登记**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| harness/__init__.py | 1 | 登记 | stability 探针注册 |
| harness/stability_hotpath.py | 1 | 登记 | Vermes 新增文件，上游无此文件 |

### 六、cron/ 类（1 处）— 判定：**登记**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| cron/scheduler.py | 1 | 登记 | home-channel 统一解析修复，上游有同名，改动是 bug fix |

### 七、.github/workflows/ 类（3 处）— 判定：**登记**

| 路径 | 次数 | 判定 | 理由 |
|---|---|---|---|
| .github/workflows/install-e2e.yml | 1 | 登记 | CI lane 新增，上游无此文件 |
| .github/workflows/js-tests.yml | 1 | 登记 | CI lane 新增 |
| .github/workflows/tests-os.yml | 1 | 登记 | CI lane 新增 |

## 汇总

| 判定 | 处数 | 占比 |
|---|---|---|
| 外置（docs/ + scripts/） | 36 | 72% |
| 登记（tools/ + plugins/ + harness/ + cron/ + .github/） | 14 | 28% |

**外置后契约税从 50 → 14**（降 72%），剩余 14 处均为有意偏离（bug fix 或新增文件），已登记不需外置。

## 执行计划

1. 批量 `git mv docs/{TASK_BOARD,HANDOFF_*,AUDIT_*,...}.md docs/vermes/`
2. 批量 `git mv scripts/{migrate_*,measure_*,diverge_*,check_*,upstream_*}.py scripts/vermes/`
3. 更新代码中对这些路径的引用（grep 搜索）
4. 在 docs/DISTRIBUTION_MANIFEST.md 补充 DIVERSION_LEDGER 段记录 14 处登记项
5. commit + push（非冻结，文档/脚本路径迁移零风险）

## 风险评估

- **零功能影响**：只移文件位置，不改内容
- **引用更新**：需搜索代码中对迁移文件的引用（主要是 scripts/ 下的 .py 可能有 import）
- **上游冲突消除**：docs/vermes/ 和 scripts/vermes/ 不在上游路径中，未来上游 docs/ scripts/ 改动不会冲突
