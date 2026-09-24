# 取长第 2 轮 · intake 判定表（2026-09-24）

> 真源：`reports/upstream-intent-20260924-v2026.9.14-max8000.md`（pin `v2026.9.14` + `--max 8000`）
> 口径：上游 `v2026.9.14` → `5a0c2fb89e`，3154 commits / 220 候选 / **21 条有对应物**。
> 每条固定产出字段：**上游后续变更次数**（同文件 `h..HEAD` 提交数，当场 `git rev-list --count`）+ 判定 + 人时。
> 「重写 vs 照搬上游形状」：后续变更 0–1 → 可贴近上游形状；≥4 → **必须重写**（上游仍在 churn）。

---

## 0. 已入账（对照 §7c，本轮不重做）

| hash | 对应物 | 后续变更 | §7c | 说明 |
|---|---|---|---|---|
| `1c0d95badbac` | `agent/file_safety.py` | 4 | L-002 ✅ | write-deny secret stores；已重写 |
| `e7cd1848c9bb` | `agent/file_safety.py` | 5 | L-002/L-003 | 同族 write-deny；control-file 放松 = L-003 拒绝 |
| `05e7e891751c` | `tools/approval.py` | 9 | L-006 ✅ | unattended allowlist |
| `2a630671d7bc` | `tools/approval.py` | 13 | L-006 ✅ | cron 非交互 |
| `2afb405337c3` | `tools/approval.py` | 0 | L-004 ✅ | 队列 pop/提交竞态；形状可贴（后续 0） |
| `3ed40556cea1` | `tools/environments/local.py` | 0 | L-005 ✅ | profile 门控三 seam 取两；残留 = T10 |

## 1. 本轮新判定（15 条）

| hash | 对应物 | 后续变更 | 判定 | 类型 | 人时 | 落点/去向 |
|---|---|---|---|---|---|---|
| `7c478ac257a3` | `agent/file_safety.py` | **2** | **采纳** | 重写 | ~0.5h | **T8 本轮**（复查点）；表结构见 §2 |
| `c0362da9a6e9` | `cron/scheduler.py`* | **4** | **采纳** | 重写 | ~0.6h | 交付前强制脱敏；Vermes 落 `cron/` delivery 路径（*上游在 `scheduler_delivery.py`，映射到 `cron/scheduler.py`） |
| `2dfb795cb78f` | `tools/approval.py` | **6** | **采纳** | 重写 | ~0.5h | CLI 审批未送达/未回答 ≠ 用户拒绝 |
| `1e2cb5797362` | `tools/approval.py` | **7** | **采纳** | 重写 | ~0.4h | session teardown / interrupted leader → withdraw |
| `6332216384b7` | `tools/approval.py` | **8** | **采纳** | 重写 | ~0.5h | gateway 审批 withdraw ≠ user deny |
| `9e232a7ff5c1` | `tools/approval.py` | **3** | **采纳** | 移植 | ~0.1h | plugin 分类懒解析 + bool 强制（与下条同刀） |
| `3fc1a184f8c0` | `tools/approval.py` | **4** | **采纳** | 重写 | ~0.3h | plugin container guard 分类生效 |
| `3fe8e5e443d1` | `tools/environments/local.py` | **4** | **采纳** | 重写 | ~0.7h | routed 子进程不继承 launch 凭据 |
| `547fff75003a` | `tools/environments/local.py` | **1** | **采纳** | 移植 | ~0.1h | scope overlay 失败 raise，不静默丢 |
| `802a9975d283` | `tools/env_passthrough.py` | **0** | **采纳** | 移植 | ~0.5h | no_agent 脚本拿属主 profile 凭据（形状可贴） |
| `b6b7802447f4` | `cron/scheduler.py` | **27** | **采纳（并入 T12）** | 重写 | ~0.3h | unattended 清 presence；后续 27 → 只取语义，禁照搬 |
| `dd68d175674d` | `tools/approval.py` | **5** | **暂缓** | 拒绝/暂缓 | — | GUI terminal ask 预排；属 UX 特征，非缺陷；挂 T16 配额观察 |
| `04fcf9159c18` | `tools/approval.py` | **11** | **暂缓** | 拒绝/暂缓 | — | api_server bridge + cron 自调度；高 churn，等 approval 家族本轮收口后复查 |
| `840c00c124be` | `cron/scheduler.py` | **33** | **拒绝** | 拒绝 | — | 重构（复用 helper）；非缺陷、churn 极高，不跟 |
| `dcdbcb8a2b14` | `tools/environments/local.py` | **10** | **暂缓** | 拒绝/暂缓 | — | env-loader 拆函数；等 `3fe8e5e443d1`/`802a9975d283` 落地后一并看 |

### 统计

| 口径 | 数量 |
|---|---|
| 已入账（本轮不重做） | 6 |
| 本轮采纳 | **10** |
| 暂缓/拒绝 | 5 |
| 合计 | 21 |

## 2. T8 · `file_safety` 上游后续变更（`1c0d95badbac` 之后，本文件起复查点）

| 上游 commit | 后续序 | 要点 | 本轮处置 |
|---|---|---|---|
| `b98ff81978` / `df72fdaa2b` | 1–2 | vault/browser-profile 折叠进 protected-subpath 表 | 对照 Vermes `_WRITE_DENIED_SECRET_DIRS`，采纳语义 |
| `c9956192a3` | 3 | 抽坐标 helper | 重写时内联，不跟重构 |
| `e342248e1a` | 4 | 去 no-op suppress | 确认 Vermes 无同形 suppress |
| `7c478ac257a3` | 5 | **credential write guards 锚定到 write 涉及的每个 home** | **本轮主刀** |

## 3. T 表三类分派（交接 §3.1，本轮执行）

| 类 | 项 | 动作 |
|---|---|---|
| 产品正确性 | T5 `sync-version.sh` 静默失败 | **直接修** |
| 产品正确性 | T10 profile 门控三条 | **直接修**（①去 default 豁免 ②fail-closed ③形状白名单注记） |
| 产品正确性 | T12①③ 会话状态 | **直接修**（①启动层 presence 剥离 ③ TERMINAL_CWD 改 contextlocal） |
| UX（用户可见配额） | T16 三条 | **进配额**（①approval 按 session ③路径段匹配 ④测试文案） |
| 取长续期 | T8 `file_safety` | **归本轮**（§2） |

## 4. 处置纪律（沿用）

1. 采纳：改代码 + 契约测试 + TAKEALONG §7c（含来源/落点/验收/人时/**上游后续变更次数**）
2. 拒绝/暂缓：§7c 记一行
3. 后续变更 ≥4：**只重写语义**，禁照搬上游形状
4. 数字带当场命令 + HEAD hash + 报告日

— 完 —
