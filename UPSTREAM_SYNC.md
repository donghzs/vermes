# UPSTREAM_SYNC.md — Vermes 与上游 Hermes Agent 同步策略

> **文档纪律**：本文件中的版本号、remote、资产计数**全部来自 2026-09-20 实测**，禁止沿用历史数字。
> 实测命令见文末「复现」。坏的是旧文档描述，**不是 `git remote` 管道**——不要去「修 remote」。

---

## 1. 仓库关系（`git remote -v` 实测 2026-09-20）

| remote | URL | 角色 |
|---|---|---|
| `origin` | `https://github.com/donghzs/vermes.git` | Vermes 主仓库 |
| `upstream` | `https://github.com/NousResearch/hermes-agent.git` | 上游 Hermes Agent |
| `upstream2` | `https://github.com/NousResearch/hermes-agent.git` | 上游别名（同上） |

本地另有上游检出：`~/.hermes/hermes-agent` @ `5a0c2fb89e`（`git describe` = `v2026.9.14-3154-g5a0c2fb89e`，总提交 **37,857**）。

> 历史文档曾把 `upstream` 写成 `donghzs/vermes`——**那是文档错误**，remote 配置一直正确。

---

## 2. 版本线状态（实测 2026-09-20）

| 项 | 实测值 | 来源 |
|---|---|---|
| **Vermes 当前** | **2.5.0** | `version.txt`；`package.json` / `frontend/package.json` / `electron/package.json` 三处一致 |
| **上游最新 release** | **v0.21.3**（GitHub tag `v2026.9.14`，name「Hermes Agent v0.21.3」） | GitHub Releases API |
| 上游前序 | v0.21.2 = `v2026.9.11`；v0.21.1 = `v2026.9.7` | 同上 |
| 上游 v0.21.0 | 「Pantheon / Bot Mode」基线（路线图引用） | 路线图 FINAL |
| 分叉规模 | ~5743 文件 / +238K / −1.2M 行量级（持续漂移） | 历史审计 + 路线图 |
| 同源 Jaccard | `toolsets.py` 0.09 / `model_tools.py` 0.12 / `utils.py` 0.15 / `context_compressor.py` 0.09 | `scripts/diverge_metrics.py` 实测，见 `reports/diverge-baseline-20260920.md` |

**旧文档数字（已废弃，禁止再引用）**：上游约 0.18.x / Vermes 约 v2.3.x。

---

## 3. 同步原则（不变）

1. **不自动全量合并** — 同源文件 Jaccard ≤0.15，属深度分歧；全量 merge 风险不可控。
2. **选择性 cherry-pick** — 仅安全修复、关键 bug、高价值特性；见路线图 P2 白名单。
3. **不追上游 provider 适配** — 本地 `openai_compat` 已覆盖。
4. **不覆盖 Vermes 领先项（红线）** — workflow DAG、memory fabric、自进化、中文平台（微信/元宝/飞书）、运行时技能库、PyInstaller/Inno 打包链、ScholarForge、cadir_build。
5. **制度层优先于能力层** — AGENTS 目录化 / 契约测试 / CI lane 先补，再谈功能移植。

---

## 4. 不同步类别清单（对齐 v0.21.3 语境）

| 类别 | 处置 | 备注 |
|---|---|---|
| CI/CD 管道结构 | **部分取长** | 补 `js-tests` / `tests-os` / `install-e2e`（M3）；`uv-lockfile-check.yml` 本地已有 |
| 按目录 `AGENTS.md` | **制度层取长** | 上游 12 vs 本地 1 → P1 目录化（路线图 C1） |
| home channel 单一口径 | **已落地最小版** | A1/A2 + GUI 入口（A7/M4）；secret scope 完整对齐属 P1 |
| state.db WAL 六件套 | **暂缓** | 等 2.5 A2A/kanban 收口；**禁止现在改 `vermes_state.py`** |
| 浏览器/kanban 细粒度工具面 | **产品取舍，非跟进项** | 上游 browser 19 / kanban 14 > Vermes 12 / 9；「上游更收敛」不成立 |
| provider 大盘 / UI 重构 / TUI | **明确不做** | 与 Vermes 差异化冲突或 ROI 低 |
| secret scope / RoutingIdentity | **P1 greenfield** | 本地零命中，不是 cherry-pick |

---

## 5. 已同步 / 在途（摘录）

| 项 | 状态 | 证据 |
|---|---|---|
| 2.5 长尾 C1–C5（channel_push / 鉴权 / peer / MCP 指挥中心 / ⌘K） | 已合入本地 main（未 push） | 提交链 `1890ff7fe8`…`eaa63411a2` |
| home channel A1/A2 | 已 commit `b7305707d4` | 共享解析器 `gateway/gateway_utils.py` |
| 分歧度量脚本 + 基线 | 本切片 | `scripts/diverge_metrics.py` + `reports/diverge-baseline-20260920.md` |
| CI 三 lane | 本切片 | `.github/workflows/{js-tests,tests-os,install-e2e}.yml`（后两条 `if: false`） |

---

## 6. 同步操作清单（可执行）

```bash
git fetch upstream
git --no-pager log upstream/main --oneline -30
# 逐条评估：安全修复 → cherry-pick；新特性 → 对照路线图 P2 矩阵
# 禁止：git merge upstream/main
```

评估模板：价值 / 冲突文件是否命中红线 / 是否与 2.5 在途抢 `vermes_state.py` / 验收用例。

---

## 7. 复现（数字核验）

```bash
cat version.txt
git --no-pager remote -v
curl -s "https://api.github.com/repos/NousResearch/hermes-agent/releases?per_page=5"
python3 scripts/diverge_metrics.py ~/.hermes/hermes-agent .
ls .github/workflows | wc -l
```

---

## 8. 关联文档（真源）

- 路线图：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`
- 度量基线：`reports/diverge-baseline-20260920.md`
- 并行工单：`docs/TASK_BOARD_20260920.md`
- 交接包：`docs/HANDOFF_TO_MIMO_20260920.md`
- 跨 agent 技能索引：`docs/AGENT_SKILLS_INDEX.md`
