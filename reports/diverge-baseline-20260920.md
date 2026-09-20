# 分歧度量基线（A4 · M1）· 2026-09-20

- **脚本真源**：`~/.hermes/skills/hermes-vermes-architecture/scripts/diverge_metrics.py`（Hermes skill）
- **仓库副本**：`scripts/diverge_metrics.py`（适配默认路径 + 补静默失败口径）
- **上游检出**：`~/.hermes/hermes-agent` @ `5a0c2fb89e`（describe `v2026.9.14-3154-g5a0c2fb89e`，总提交 37,857）
- **Vermes**：本仓 worktree @ 量测时 HEAD（`feat/p0a-mimo-m1m4` 基于 `e65f217752`）
- **复现**：

```bash
.venv/bin/python scripts/diverge_metrics.py ~/.hermes/hermes-agent .
```

---

## ① 核心工具面（口径：`toolsets.py` 的 `*_CORE_TOOLS`）

| 侧 | 核心工具数 |
|---|---|
| 上游 Hermes | **60** |
| Vermes | **49** |

| 差集 | 项 |
|---|---|
| 仅上游（16） | `browser-use`, `browser_exec`, `browser_vault_*`(5), `cronjob_manage`, `kanban_attach*`(4), `manage_connections`, `process_manage`, `todo_list` |
| 仅 Vermes（5） | `cronjob`, `process`, `send_message`, `shenmotang`, `todo` |

| 前缀 | 上游 | Vermes |
|---|---|---|
| `browser_*` | 19 | 12 |
| `kanban_*` | 14 | 9 |
| `memory_*` | 1 | 1 |
| `skill_*` | 3 | 3 |

> **口径纪律（E3）**：只用核心列表；禁止用 `tool_guardrails` 注册名或全仓标识符计数。
> 「上游更收敛」**不成立**——browser/kanban 上游工具面更大。

---

## ② 同源文件行集合 Jaccard（≤0.15 = 深度分歧）

| 相对路径 | 上游行数 | Vermes 行数 | Jaccard |
|---|---:|---:|---:|
| `toolsets.py` | 485 | 983 | **0.09** |
| `model_tools.py` | 987 | 1112 | **0.12** |
| `utils.py` | 627 | 419 | **0.15** |
| `agent/context_compressor.py` | 5113 | 2171 | **0.09** |

→ 四条同源文件全部 ≤0.15，**深度分歧**成立；「同步上游」只能白名单 cherry-pick，不能全量 merge。

---

## ③ 工程资产

| 侧 | tests/test_*.py | CI workflows | AGENTS.md |
|---|---:|---:|---:|
| 上游 | 4578 | 35 | 12 |
| Vermes | 1453 | **17**（14 基线 + 本切片新增 3 条 lane 文件） | 1 |

> workflows 计数含本切片 `js-tests.yml` / `tests-os.yml` / `install-e2e.yml`；
> 其中后两条为 `if: false` 占位，**尚未在 PR 上真跑**。

---

## ④ 静默失败信号（本切片补充口径）

模式：`\|\| true` / `except: pass` / `except Exception: pass|continue`（补丁只计 **+ 行**）

| 指标 | 数值 |
|---|---|
| 上游 `agent/` 树模式命中 | ≈88（46 文件） |
| Vermes `agent/` 树模式命中 | ≈88（38 文件） |
| 上游 runtime（agent/gateway/tools/vermes_cli/cron） | ≈243 |
| Vermes runtime 同口径 | **≈935** |
| Vermes 近 90 天补丁**新增**静默模式 | **3 行 / 3 个提交**（扫描 cap=400） |

> runtime 面 Vermes 显著更高，但近 90 天**新增**很少（3）——存量偏多、增量受控。
> 该口径是**信号**不是 KPI；后续 `diverge_metrics.py` 月度跑即可画曲线（A4 度量层）。

---

## 基线结论（可引用）

1. 工具面：上游 60 / Vermes 49；browser/kanban 上游更大，**勿写「Vermes 工具面落后」**。
2. Jaccard 全 ≤0.15 → 维持「锁定基线 + 白名单移植」。
3. 制度层代理指标：AGENTS.md 12 vs 1；CI 类别仍缺完整对齐（lane 文件已补 3，生效策略另见 M3）。
4. 静默失败：runtime 存量 935 vs 243，近 90 天新增仅 3——防复发应盯**增量**与不变式测试，而非全仓 sweep。
