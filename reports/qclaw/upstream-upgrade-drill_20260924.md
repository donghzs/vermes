# 上游 tag 升级演练记录 · 2026-09-24

> 模板：`docs/plans/upstream-upgrade-drill-template.md`（先定模板再跑）。
> **目标点说明**：上游最新 tag 仍是 pin `v2026.9.14`=`345cd2b057`，**无更新 tag**。
> 按「禁止移动 ref」纪律，演练目标冻结为 commit **`5a0c2fb89e`**
> （`v2026.9.14-3154-g5a0c2fb89e`，2026-09-24 当日上游 HEAD 快照）——
> 这正是「若今天升钉」的真实代价面。
> 性质：**只读演练**——判定 / 测闸门 / 记代价，不改产品代码、不动 pin。

---

## 演练记录（6 字段）

| 字段 | 值 | 当场命令 / 证据 |
|---|---|---|
| **1. 上游新增提交** | **3154** | `git -C ~/.hermes/hermes-agent rev-list --count v2026.9.14..5a0c2fb89e` |
| **2. 新增候选** | **220**（GHSA 1 / fixsec 6 / 安全语义 220） | `reports/upstream-intent-20260924-v2026.9.14-max8000.md` 校准摘要 |
| **3. 有对应物（引擎面入队）** | **21** | 同上；产品面 7 条自 `6fb755b7e2` 起标「不判」（本报告用旧口径「红线」计，语义等价） |
| **4. 真断点（接口变更）** | **6 族 / 13 commits 命中** | 见下方断点清单（新模块 / 缺抽象 / 包布局分裂） |
| **5. 总人时** | **≈ 10h** | 引擎面 21 条：判定 ~2.5h + 已实施 7 条实测 **3.3h**（§7c 人时合计）+ 拒缓 6 条 ~0.5h；「无对应物」192 条抽样速判 ~4h（≈1.2 min/条） |
| **6. 结论** | **成立** | 六字段齐全；断点全部可用现有「重写/暂缓 + §7c 登记」表达，**无需补机制**；产品面分流已把 7 条移出人工队列 |

### 附加留痕

| 项 | 值 |
|---|---|
| 演练日 / 操作者 | 2026-09-24 / MiMo（并行 Hermes 构 Win，本演练只读未碰产品面） |
| pin（旧）→ 目标 | `v2026.9.14`=`345cd2b057` → 冻结 `5a0c2fb89e` |
| Vermes 基线 | main `6fb755b7e2`（发版 2.5.3 + 取长分流之后，静止干净） |
| boundary 未登记税 | **0**（commits=91 · 偏离 75 · core 100%） |
| gold `--check` | **17 场景 × 3 段逐字相同** |
| canary | **FAIL=0** · WARN=2（intake 待裁 1 / coexistence 2）· ab-sentinels 7 全绿 · contract 86 passed |
| 产品面命中（不判） | **7**（webhook GHSA / api_server / weixin×3 / telegram base×2） |
| 回退开关 | 不需要（演练零代码改动） |

---

## 断点清单（字段 4 明细）

> 判定口径：**真断点** = 上游改了 Vermes 调用面会破的接口/模块布局——照搬形状必炸，
> 只能重写或暂缓。sig=def/class 增删数；imp+=新增 import 数。

| 上游 hash | 接口变更摘要 | Vermes 调用面 | 处置 | 人时 |
|---|---|---|---|---|
| `dd68d175674d` | **新模块** `agent/terminal_approval_batch.py` + 批量 ask API（sig 33 / imp 18） | `tools/approval.py` 无此面 | 暂缓（L-021，UX 特征） | — |
| `3ed40556cea1` | 新面 `local_env_policy` + `web_server_gateway` + `update_restart_recovery` | 只有 kanban/gateway 两 seam | 部分重写（L-005/L-016） | 0.9h |
| `3fe8e5e443d1` | `served_profile_child_env` / `inherit_credentials` | 无同构 API | 暂缓（L-025，缺 scope 基建） | — |
| `547fff75003a` + `802a9975d283` | `scoped_passthrough_additions`（scope overlay） | 无 | 暂缓（L-025 同族） | — |
| `9e232a7ff5c1` + `3fc1a184f8c0` | `terminal_env_registry.provider_flag`（plugin container 分类） | approval 硬编码 env_type 集合 | 暂缓（L-026，无 plugin ABI） | — |
| `2afb405337c3` + `2dfb795cb78f` + `1e2cb5797362` + `6332216384b7` | 上游拆出 `approval_gateway_wait.py` + `Unanswered`/`shared_metrics` | Vermes 单文件 `tools/approval.py` | 重写语义（L-004 部分 / L-020 家族） | 1.6h |
| `dcdbcb8a2b14` + `c0362da9a6e9` | 包布局分裂（`hermes_cli.env_loader` / `scheduler_delivery`） | `vermes_cli` / `cron/scheduler.py` | 映射重写（L-019 交付脱敏已落） | 0.4h |

**断点族统计**：6 族 · 13/21 commits 命中断点面 · 全部可用「重写 / 暂缓 + §7c」表达。

---

## 成本剖面（字段 5 拆开）

| 项 | 人时 | 说明 |
|---|---|---|
| 引擎面 21 条判定（读 diff + 分流） | ~2.5h | 含产品面/红线 7 条零成本（今后由脚本自动「不判」） |
| 采纳 7 条实施（代码+测试+登记） | **3.3h** | §7c 实测：L-014…L-020 人时合计 |
| 拒缓 6 条登记 | ~0.5h | L-021~L-026 |
| 无对应物 192 条速判 | ~4h | 抽样 1.2 min/条；多数一句「无对应物」 |
| 闸门四件（gold/boundary/canary/intake） | ~0.5h | 全自动 |
| **合计** | **≈ 10h** | 一次 pin 升 3154 commits 的全量代价 |

---

## 验收（停止条件 1）

| 口径 | 结果 |
|---|---|
| 六字段齐全 | ✅ |
| 真断点逐条有处置 | ✅（6 族全落到 L 行 / 暂缓理由） |
| 总人时可量化 | ✅ ≈ 10h |
| 演练后 boundary/gold/canary 不红 | ✅ 0 / 逐字绿 / FAIL=0 |
| **结论** | **成立**——发行版化「血统层」机制可用，代价可预算。无需补机制件。 |

### 备注（诚实边界）

1. 无「比 pin 更新的 tag」——目标用冻结 commit 替代；下一次真 tag 出现后应按同模板复跑一次作对照。
2. 192 条「无对应物」是速判不是精读；若某条其实同域不同名，会在后续 intake 轮次浮出。
3. 演练未改 pin、未合任何上游代码；真升钉走 `bump_policy`（单独 PR + 对比区间 + takeover 清单）。

— 完 —
