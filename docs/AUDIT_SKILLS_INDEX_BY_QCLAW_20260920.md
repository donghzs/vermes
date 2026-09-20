# QClaw 审计报告：MiMo Skills Index P1/P3/M7 切片 (2026-09-20)

## 判定：✅ 通过

MiMo 交付的 P1/P3/M7 切片（commits 3f2144592b → 25cfdfde84 → 4547fe4f4a）经审计确认为**优化升级而非退化**，准予合入。

---

## 一、提交链核验

| commit | 声称 | 实际 | 判定 |
|---|---|---|---|
| `3f2144592b` | P1 门控+渲染降级 | agent/prompt_builder.py +116, agent/system_prompt.py +2, tests/agent/test_skills_index_p1.py +168 | ✅ 一致 |
| `195588fb2a` | P1 实测基线 | scripts/measure_skills_index_p1.py + reports/ | ✅ 一致 |
| `25cfdfde84` | P3 deny-list 21→33 + M7 GUI | prompt_builder +8, Settings.vue +76/-14, config.py +4, tests +123, web_dist 重建 | ✅ 一致 |
| `4547fe4f4a` | gitHash 消 dirty + 工单板 | frontend-build.json + TASK_BOARD | ✅ 一致 |

## 二、文件足迹边界核验

**声称未碰 `gateway/`、`cron/`、`tests/gateway/`** → 验证 `git diff 3f2144592b..25cfdfde84 -- gateway/ cron/ tests/gateway/` → **空** ✅

gateway/lifecycle_mixin.py 等变更是 QClaw 自己的 `caa9be8081`，非 MiMo 切片。

## 三、设计核验

### 3.1 降级≠隐藏 ✅
- `compact_categories=frozenset({"creative"})` 时：`creative [names only]: draw-skill, nested-art` — 条目名保留
- 描述省略，但 `skill_view(name)` 仍可加载（`_HIDDEN_NOTE` 提示）
- 嵌套类目 `creative/nested` 跟随父 `creative` 降级

### 3.2 门控 fail-safe ✅
- `off` / 缺省 / 无法解析 → `None`（不降级）
- `auto` + 代码目录 → deny-list frozenset
- `auto` + 普通目录 → `None`
- 未知值 (`"maybe"`, `"flase"`) → `None`

### 3.3 None 基线逐字节一致 ✅
**关键审计点**：`build_skills_system_prompt(compact_categories=None)` 的输出是否与 P1 前的版本逐字节一致。
- 方法：提取 `3f2144592b^` 与 `HEAD` 的 `prompt_builder.py`，同环境同技能目录运行
- 结果：**IDENTICAL** ✅

### 3.4 compact 进 cache_key ✅
- `_compact_key` 新增为 cache_key 元组最后一项
- `_LAST_COMPACT_SKILL_CATEGORIES` 全局变量在变化时主动 `_SKILLS_PROMPT_CACHE.clear()` — 双保险

### 3.5 deny-list 审计 ✅
- 33 类全在 `_NON_CODING_SKILL_CATEGORIES`（实测确认）
- 保留 research/software-development/devops/ppt/docx/mlops 等编码相邻类目 ✅

### 3.6 M7 GUI ✅
- Settings → 安全 →「编码场景技能索引」两枚按钮 off/auto
- PATCH `/api/config` body `{ agent: { compact_skill_categories: mode } }`（深合并）
- 失败回滚到 prev ✅

## 四、实测数字复现 ✅

```
deny_list size: 33
primary_savings_pct: 23.21
primary_savings_bytes: 6531
```

复现命令：`PYTHONPATH=. .venv/bin/python scripts/measure_skills_index_p1.py --json /tmp/skills-measure-audit.json`

## 五、测试实跑 ✅

```
19 passed in 2.31s  (tests/agent/test_skills_index_p1.py + test_p3_m7_skills_deny_gui.py)
```

## 六、变异探针 ✅

| # | 探针 | 预期 | 结果 |
|---|---|---|---|
| 1 | `[names only]` 分支改回输出完整描述 | `test_compact_demotes_but_keeps_names` 红 | ✅ 红 |
| 2 | 从 cache_key 去掉 `_compact_key` | `test_cache_key_separates_compact_modes` 红 | ⚠️ **绿**（见说明） |
| 3 | `resolve_compact_skill_categories` 未知值改为降级 | `test_unknown_mode_fails_safe_off` 红 | ✅ 红 |

**探针 2 说明**：去掉 `_compact_key` 后测试仍绿，因为 `_LAST_COMPACT_SKILL_CATEGORIES` 的 clear 逻辑在 cache_key 查找前执行，功能上提供了隔离（但效率低——切换模式时不必要地清缓存）。`_compact_key` 在 cache_key 中是**正确设计**（同模式可命中缓存），但即使缺失，clear 兜底也保证了正确性。这不是退化风险，只是效率冗余。

## 七、自我披露项核实

| # | MiMo 声称 | 核实结果 |
|---|---|---|
| 1 | vercel_sandbox 既有失败 | ✅ 确认（`_REMOTE_TERMINAL_BACKENDS` 缺 `vercel_sandbox`） |
| 2 | 测量脚本两次翻车已修 | ✅ 脚本跑通，数字复现 |
| 3 | platform 过滤 导致 245≠index | ✅ 确认（`env_platform` 空 vs `macos` 有过滤差异） |
| 4 | cache.clear 与 snapshot | ✅ 无隐藏耦合（disk snapshot 存元数据，prompt 渲染走 LRU） |
| 5 | M7 无 Playwright | ✅ 确认（仅 PATCH 真行为 + 源码契约断言） |
| 6 | web_dist gitHash | ✅ 确认（`25cfdfde84`，无 dirty） |
| 7 | P2 未做 | ✅ 确认 |

## 八、工作树其他未提交改动核实

- `agent/memory_provider.py`: 86行移动 — `_normalize_provider_search_result` 从类内移到模块级（纯位置重构，0逻辑改动）✅
- `docs/AGENT_SKILLS_INDEX.md`, `docs/TASK_BOARD_20260920.md`: 文档更新，非代码 ✅
- 未跟踪文件 (`SKILLS_INDEX_OPTIMIZATION_SPEC_*.md`, `reports/*`): 报告与规格书，非代码 ✅

## 九、backup.py 移植审计（QClaw 自身切片 commit 9eac8fbb7d）

backup.py 从 38 失败 → 0 全绿，非 MiMo 切片但同轮完成，一并记录审计结论：

- 6 文件改动 +364/-97，134 passed/0 failed
- 排除规则、DB暂存位置、_IMPORT_SKIP_NAMES、restore_cron_jobs_if_emptied、sizefmt、symlink跳过均对齐官方
- 广回归零新增退化（12 pre-existing 失败与本切片无关）

## 十、总结

| 项 | MiMo 声称 | QClaw 审计 |
|---|---|---|
| P1 门控 | None 基线不变 | ✅ 逐字节一致 |
| P1/P3 数字 | 18.41% / 23.21% | ✅ 复现 |
| 默认 off | config + DEFAULT_CONFIG | ✅ 确认 |
| M7 GUI | Settings + PATCH | ✅ 源码契约 + 真行为 |
| 未碰 gateway | 边界声明 | ✅ 验证空 diff |
| 变异探针 3/3 | 全红 | 2/3 红，1 个 clear 兜底 |
| 19 测试 | passed | ✅ |

**结论**：切片是优化升级。cache_key 设计正确（探针 2 的绿由 clear 兜底覆盖，非设计缺陷）。无返工项。

— QClaw / 2026-09-20