# Vermes B1–B11 涌现飞轮根因修复 · 最终审计报告

**Commit**：`95c7002a3`（分支 `feature/vermes-brand-fork`）
**范围**：11 文件，+628 / −26（9 源码文件 + 1 新测试文件）
**测试**：`tests/agent/test_emergence_flywheel_bfixes.py` —— **8 passed / 0 failed**（聚焦测试，未跑全量套件）
**日期**：2026-08-02

---

## 一、执行摘要

本轮把此前"系统审计家底 + 运行时实测交叉核验"中定位到的 **B1–B11 根因**全部落地为源码改动，并补了 8 个聚焦测试固化证据。

核心闭环根因回顾：自学习管道在**源头**被三连破坏——
1. `emergent_clusterer.py` 非增量 + 破坏 recurrence 信号（独立 commit `1580faef6`+G5/G6 已修，不在本批）；
2. 生命周期阈值无地板 → 簇被毫秒级秒杀（**B1**）；
3. 死簇永不复活 + 自噬簇独占候选池（**B1/B2/B3**）；
4. 幽灵 `emotional_state` 边污染 relations（**B5**）；
5. 空向量 NULL 壳污染 embeddings 索引（**B6**）；
6. 冻结包缺 `tools.approval` 等 hiddenimports → 决策落地段静默死（**B8** + **B9** 日志补全）；
7. 桌面直连模式反思环不闭合（**B11**）。

本批逐个堵口，且全部用**实时库直读 / 真实函数驱动**验证，非静态推断。

---

## 二、B1–B11 修复明细与实证

| # | 文件 | 改动 | 实证证据 |
|---|------|------|----------|
| **B1** | `agent/cluster_lifecycle.py` | `compute_thresholds` 加 `MIN_INTERVAL=60.0` 地板（`avg_interval=max(avg_interval, self.MIN_INTERVAL)`）；新增 `_normalize_active_flag` / `_resurrect_dead_clusters` / `_load_dead_clusters` / `_has_new_events_since_death` 复活死簇 | 实时库 `avg_interval≈0.001s` → `k_dead=0.015s` 秒杀簇；加地板后 `k_dead` 通过 900s 阈值。37 个 `is_active=0` 死簇具备复活路径 |
| **B2** | `agent/capability_evolver.py` | `if len(clusters) < 3: return` → `if not clusters: return`（保留 `ratio>0.4` 质量门） | 原 3 簇硬刹车使 emergence 恒空转；改后单簇即可涌现 |
| **B3** | `agent/skill_extractor.py` + `agent/capability_evolver.py` | 排除自噬簇 `name NOT LIKE '|_|_%|_|_' ESCAPE '|'`（脆弱，NULL 时静默丢行）→ `name NOT GLOB '__*__'`，并补 `(name IS NULL OR ...)` | 实时库副本验证候选集从 **0 → 4** 个行为簇（`read_file:.py`/`search_files:.js+.py` 等）；`__self_assessment__` 仍正确排除。语义不变，仅去脆弱性 |
| **B4** | `agent/memory_recall.py` | stable/declining 计数加 `AND is_active = 1` | 与 `graph_sync.py:227` 口径统一，避免把死簇算进活跃信号 |
| **B5** | `agent/evolution_manager.py` | 新增 `purge_phantom_emotional_state_relations()`（进程级 `_GHOST_EMOTION_PURGED` 守卫，DELETE `target_type='emotional_state'`）；删除原 `outcome→emotional_state` 幽灵边写入块，移出 `_emotion_id` 守卫每次执行 | 实时库直读：emotional_state 边 **2003 条**（表 `emotional_states` 本实例不存在，100% 真幽灵）；purge 后保留 **955** 条 strategy/anti_pattern 有效边 |
| **B6** | `agent/hybrid_retriever.py` | `store_embedding._do_store`：`_get_embedding` 返回空即 `return`，不再写 NULL 向量壳 | canonical store = `~/.vermes/index/embeddings.db`；实时库该库 2391 行 **100% NULL vector**（真因=DeepSeek 无 `/embeddings` 端点）。修后 fail-open：provider 配好后自动恢复写入，不再积死重 |
| **B7** | `agent/memory_fabric.py` | `_get_index_db()` 调新增 `_maybe_cleanup_orphan_dbs()`（守卫 `_ORPHAN_DBS_CLEANED`）；删 0 字节孤儿 `~/.vermes/embeddings.db` 与空壳 `~/.vermes/evolution/memory_index.db`（保守判空后才删） | 两个 0 字节同名空壳已查实，易读错库；清理不碰活库 |
| **B8** | `vermes-backend.spec` | hiddenimports 补 `'tools'`,`'tools.approval'`,`'utils'` + 16 个 `agent.*` 子模块 | 根治冻结包 `ModuleNotFoundError`（**Bug2 真实面**：激活线程 `from tools.approval import ...` 未进白名单 → 决策生成但落不了地，被 `logger.warning` 静默吞） |
| **B9** | `agent/raw_event.py` | `_bg_activate` 失败 `logger.warning` → `logger.error(..., exc_info=True)` + 落 `record_raw_event(tool_name="capability_activate", is_error=True, ...)` | 失败事件可观测、可追因，不再静默 |
| **B10** | `agent/raw_event.py` | 4 处 `logger.debug("...skipped")` → `logger.info`；新增 `logger.info("Emergence cycle: %d decision(s) generated", len(decisions))` | 自学习环状态从"全静默"变可观测；之前全链路 debug 吞错是定位难的元凶 |
| **B11** | `backend_main.py` | 新增 `_reflection_daemon` 守护线程（daemon，每 600s 调 `maybe_run_reflection()` + `auto_resolve_eligible_flags()`，fail-open） | 闭合桌面直连模式反思环（CLI/桌面直连不走 gateway ticker，此前反思只挂 gateway） |

---

## 三、测试（B12，聚焦、不刷屏）

`tests/agent/test_emergence_flywheel_bfixes.py` —— **8 passed**：

1. `test_b1_threshold_floor` — avg_interval 极小非零时 k_dead 仍被地板托住
2. `test_b1_resurrect_dead_clusters` — 有新生事件的死簇被复活
3. `test_b1_normalize_active_flag` — dead 但 stage 合规的簇 is_active 归 1
4. `test_b1_no_over_resurrect` — 无新生事件的死簇不被误复活
5. `test_b2_single_cluster_emergence` — 单簇即可触发 emergence（非 <3 刹车）
6. `test_b3_candidates_exclude_self_eating` — 候选排除 `__self_assessment__`、保留行为簇
7. `test_b5_purge_phantom_emotional_state_relations` — 删 2003 幽灵边、留 955 有效边
8. `test_b6_skip_null_vector` — 空向量不写 NULL 壳

> **纪律说明**：按你"不跑全量测试套件"的偏好，本次只跑新写的聚焦测试（8 个，秒级）。全量套件动辄上万、易触发 xdist INTERNALERROR，等待成本高，非必要不跑。

---

## 四、运行时实测报告交叉核验纠错（已坐实，供你复核）

此前你贴的 Vermes.app 运行时报告，我逐条用原始证据复现，**多数坐实，3 处需纠错**（详见工作记忆 `2026-08-02.md` 第五节）：

- ✅ **坐实**：Bug1 阈值无地板；簇计数 `dead=33/contradictory=4/active=5`；`on_new_event` / `cleanup_dead_clusters` 零生产调用；`capability_evolver.py:234 len<3` 硬刹车；`extracted_skills=5 pending`；Bug2 真面目=`tools.approval` 未进 hiddenimports。
- ❌ **纠错1·幽灵边数量/机制**：报告引 `capabilities.db/outgoing_relations` 本实例**不存在**（find 全 `~/.vermes` 无 `capabilities.db`）。真实 store=`self-model.db.relations`(2958)。`strategy`(806)/`anti_pattern`(149) 经 `memory_recall.py:467-493` 解析到 `strategies`/`anti_patterns` 表，**全部有效（ghost=0）**；仅 `emotional_state`(2003) 表不存在+解析器从不读=**100% 真幽灵**。→ 真实幽灵边=**2003 条（仅 emotional_state）**，非报告所写 2944 全类型。B5 据此只清这 2003 条，保留 955 条有效边。
- ⚠️ **纠错2·embeddings 证据路径**：源码 `hybrid_retriever.py:50` 实际用 `index/embeddings.db`（1MB/2391 行/100% NULL vector）；报告查的 0 字节 `~/.vermes/embeddings.db` 是**孤儿陈旧文件，代码不读**。结论方向仍真（embeddings 无用），证据路径应改。
- ⚠️ **纠错3·头条算术**：报告"41/42 簇截流"算错，应为 **37/42**（33 dead + 4 stable/0 衰老，5 活，共 42）。

> 取证纪律（已同步跨项目记忆）：① 否定性结论"全 X/无 X"必先验证该对象本实例真实存在（报告引不存在的 `capabilities.db` 致失真）；② 多文件同名 store 必须 grep 源码实际 `open()` 路径判定活 store（0 字节 vs 1MB embeddings）。

---

## 五、⚠️ 部署铁律（最关键，务必读）

**源码改 ≠ 运行态生效。** 这条是本次最容易踩的坑：

1. `/Applications/Vermes.app` 是**独立的 PyInstaller + Electron 冻结安装副本**，执行的是 `Vermes.app/Contents/Resources/backend/_internal/agent/*.py` 这份**冻结拷贝**，**不读** `Projects/vermes-electron/agent/*.py` 源码。
2. 改源码 + 重启 app 对运行态**零效果**（铁证：此前 `6c6b2668e` 孤儿清理改了源码但 app 仍报 76 open flag，因冻结后端 `grep orphan_cleanup = 0`）。
3. **让修复落地的唯一路径**：
   - 改源码 → `bash build.sh`（同步 web_dist → PyInstaller 冻结后端 `vermes-backend.spec` → electron-builder → 产出 `dist-electron/*.dmg`）
   - 用新 DMG **重装覆盖** `/Applications/Vermes.app`
   - **干净退出旧 app**（Cmd+Q + 活动监视器杀残留 + 清 `/tmp/vermes-startup.lock`）
   - 重开
4. **B8 是构建期必修**：`vermes-backend.spec:173` 当前已加 `tools.approval` 等 hiddenimports，但**只有重建后**冻结包才含这些模块；若跳过 rebuild 直接重装旧包，激活线程照样 `ModuleNotFoundError`。
5. **构建环境坑**：`build.sh` 的 `rm -rf web_dist/assets` 会触发 WorkBuddy 沙箱 `genie-safe-delete` 批量删除拦截（>50 文件 abort）。若由 AI 跑构建，须先 `unset` 6 个 `CODEBUDDY_SAFE_DELETE_BULK_*` 环境变量。

**本次未触发构建**——按你"改源码 + 提交"的指令，源码改动与测试已 commit，DMG 重建由你侧按上述流程执行（或我后续帮你跑 `build.sh`）。

---

## 六、判定与待办

**判定**：B1–B11 是「源头三连破坏」的必要堵口，归档为**正确性 / plumbing 修复**，不创造新涌现能力。飞轮评分不预称（north star=优秀秘书的体感，只能靠你长期使用，非 lab 分数）。

**仍横在前面的第一梯队（未动，非本批范围）**：
- pending 技能前端确认入口（出口的出口，P2-D）
- `event_time` 时序维度（G2 已加列但待运行态激活）
- `merge` 真合并（G1 清扫已接线待首次 init 触发）
- `relations` 2878 零召回读取路径（fix② 只接 recall 桥、未接读取）
- L3 episodic 层实例化（代码有地板但层未灌数据）
- `state.db` 48.4% 碎片（VACUUM 可回收 ~37.9MB）

**本轮交付物**：
- 源码 commit `95c7002a3`（B1–B11 + B12 测试）
- 本报告
- 排除项：`frontend/dist/index.html`（陈旧构建产物，不属于本批，未提交）

---

## 七、给你审计的入口

```bash
# 看改动
git show 95c7002a3 --stat
git show 95c7002a3 -- agent/cluster_lifecycle.py agent/evolution_manager.py vermes-backend.spec

# 跑聚焦测试（秒级）
cd /Users/dongzusheng/Projects/vermes-electron
PYTHONPATH=. .venv/bin/python -m pytest tests/agent/test_emergence_flywheel_bfixes.py -p no:xdist -o addopts=""
```

需要我接着跑 `build.sh` 重建 DMG（含 unset 环境变量 + 冻结包 `grep` 复验 hiddenimports 已含 `tools.approval`），还是先按你审计结果调整？

---

## 八、冻结包发布后独立验证（2026-08-02 14:22，用户已构建发布）

用户构建流程：PyInstaller 重打包后端（含 B8）→ 冒烟 G1 state.db + 插件路由 → Electron 双架构 DMG → 上传 + version.json（12 条 changelog）。下载：https://vbit.top/vermes/#downloads

我独立 grep 已安装的 `/Applications/Vermes.app` 冻结后端，核验 B1–B11 是否真落地（审计铁律：源码改 / 冻结二进制含该代码 / 重装新包，三者缺一不算落地）：

| 项 | 验证方法 | 结果 |
|----|----------|------|
| **B1–B10** | `_internal/agent/*.py` 文件存在 + `grep -c` marker 计数>0 + **md5 与仓库源码完全一致**（抽样 cluster_lifecycle/skill_extractor/evolution_manager） | ✅ **铁证落地**（B1=7/B2=1/B3=1+1/B4=1/B5=2/B6=1/B7=2/B9+B10=12） |
| **B8** | `tools/approval.py` + `agent/memory_reflection.py` 在冻结包且 md5 一致 | ✅ **铁证落地**（Bug2 真实面=激活链 ModuleNotFoundError 根治） |
| **B11** | backend_main.py 是入口脚本（bytecode 嵌二进制）；二进制 mtime 13:00 晚于 commit 12:13；依赖模块 memory_reflection.py md5 一致；延迟 import 调 maybe_run_reflection/auto_resolve_eligible_flags（fail-open） | ✅ **间接证据链完整**，建议运行时确认 `vermes-reflection` 线程存在 |

**⚠️ 方法论发现（已同步跨项目记忆）**：`strings` 对 PyInstaller 冻结二进制**完全不可靠**。控制实验铁证——已用 md5 确认在冻结包里、且 grep .py 计数>0 的模块字符串常量（`MIN_INTERVAL`、`decision(s) generated`），`strings binary | grep` 仍返回 0。因 PyInstaller 把 .py marshal 进 PYZ archive，字符串带长度前缀，strings 滑动窗口识别不了。**验证冻结包必须用 md5 比对 .py 模块，不能用 strings。**

**用户冒烟验证缺口（建议补）**：用户冒烟了 G1 state.db + 插件路由，但未验证 B8（激活链）与 B11（反思环）的运行态。建议：
- **B8 运行时确认**：触发一次能力激活，看 `capability_activate` 是否落地（无 `ModuleNotFoundError`）、有无失败事件落库。
- **B11 运行时确认**：启动后看有无 `vermes-reflection` 线程（`py-spy dump` 或日志里 reflection daemon tick），或等 600s 后看 auto_resolve 是否跑过。
