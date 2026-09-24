# 接力报告 · 2026-09-24（会话收口，下一棒从 §3 开干）

> 状态：**S/P 线 + 治理收口全部落地**。下一刀 = **取长第 2 轮**。
> 本报告是给新会话的唯一交接文档，按 §1→§4 顺序读即可开工。

---

## 1. 工作空间（接棒先核）

| 项 | 值 |
|---|---|
| 分支 | `main` |
| HEAD | `82e5d378db`（docs: P3 退役定义去重） |
| 相对 origin/main | **ahead 0 / behind 0**（已 push） |
| 工作区 | **0 条**（干净） |
| 本地 tag | `v2.5.0→4297e0a10c` · `v2.5.1→888bf8a344` · `v2.5.2→f416dbf17d`（annotated，远端已验证 ref+`^{}` 各 3 行） |
| 边界闸门 | commits=85 · 未登记税 **0** · 已登记偏离 65 · core 登记率 **100% (9/9)** |
| canary | FAIL=0 · WARN=2（intake 1 待裁决 / coexistence 3 WARN）· **ab-sentinels 7 全绿** |

自测基线（本机 `.venv`）：
- `PYTHONPATH=. .venv/bin/python scripts/s2_snapshot.py --check` → 17×3 逐字绿
- `test_distribution_mechanism + test_d02 + test_l014 + test_s2_gold` → **44–50 passed**（口径见各刀报告）
- `test_file_write_safety + test_file_safety_secret_stores` → 19 passed

**勿夹带**：无。历史脏文件 `tools/feedback_tool.py` 已正式入库（`cc97079a13`）。

---

## 2. 本会话已完成（勿重做）

| 切片 | commit | 要点 |
|---|---|---|
| S2.4 退役 fallback map | `fdefdba929` | 4 键 YAML 回写 + 删 `_PROCESSOR_FALLBACK` + `computer_use` 惰性保留 |
| Hermes 收口 | `ef68162e96` | gold 机器指纹归一 `{{HOST}}/{{HOME}}/{{CWD}}` + missing 可见占位 |
| env-hints 真值断言 | `5dcff00d91` | 归一化前钉 cwd/home/host 真值（防填错行） |
| P3 禁用开关 | `19383671ec`→`f45a9b65c5` | 先 load_all 过滤，后重构为**策略单源** |
| 收 feedback_tool | `cc97079a13` | thumbs 归一 + target 回填 + TMPDIR 假红修 |
| 卫生刀 | `e21cc7218d` | 假税 3 条归 own + C-009 + 3.10 防呆 + boundary 新增提示 |
| L-014 max_chars | `04f4604715`→`de92b3eb7e` | 三层上限 + plugin_callable 接入 + 双入口统一 + 弱测试补牙 |
| 数字口径 + A/B 哨兵 | `c16764c1fe` | 窗口+HEAD+命令；canary step 6 |
| write-deny `/var` 旁路 | `34bba18558` | `/var/`+`/private/var/` 前缀（发现即刀） |
| T1 收口 | `c313c3df94` | D-009 登记 + §8.1 窗口措辞 |
| 探针自足 + D02/D03 | `d4a0ddce8d` | `manifest_path` 参数；D02 护栏；D03 标依赖 P4；canary 失败名 |
| P3 去重 | `82e5d378db` | 退役定义单一真源 |

**P3 退役定义（已写死，解读 B）**：禁用名单从 env 迁入 `config.yaml`（`agent.disable_prompt_sections`；env 留覆盖层）+ 桌面 GUI 开关。`_PROCESSOR_FALLBACK` 退役（S2.4）≠ 本项完成。退役=`v3.0.0-distribution` 前。**此项本身可算一条用户可见能力**。

---

## 3. 下一刀 = 取长第 2 轮（**用户已拍板，Hermes 三条约束不变**）

### 3.1 执行顺序

1. **重跑 intake**（当前 pin + `--max 8000`）用今天的清单 —— 上游 3 天涨了数千提交，对应物判定可能已变
2. **每条固定产出**：读 diff → 判 → 契约测 → §7c 登记（**含「上游后续变更次数」列**）+ 人时
   - 「上游后续变更次数」是「重写 vs 照搬上游形状」规则的证据，决定后续所有取长成本
3. **T 表三类分派**：
   - 产品正确性（T5 `sync-version.sh` 静默失败 / T10 profile 门控三条 / T12 进程级会话状态①③）→ **直接修**
   - UX（T16 三条）→ **进「用户可见配额」**
   - 取长续期（T8 `file_safety` 复查点）→ 归本轮
4. 验收：boundary 持续 0 未登记 + 取长登记 12 → 15+；每条有「上游后续变更次数」+人时

### 3.2 用户可见能力配额（制度，已解释给用户）

- 数据：近两周 `feat(*)` 107，其中 `feat(ui/studio/frontend/desktop)` 仅 4（≈4%）
- 配额 = **每周强制 ≥1 条用户能感知的改进**（UI / 新工具 / GUI 提示），防治理吃光产能
- 判定：能在 GUI 看到或用到，且能写进 release notes「用户可见变化」小节；排除改文案/重构充数
- 发版触发条件之一：攒到 ≥1 条用户可见改进（呼应配额）

### 3.3 明确不做

- 形态 B / P5：等取长第 2 轮 + 一次真实上游升级数据后再拍
- gold 瞬态：记「未复现」，停止消耗注意力
- 再加治理机制：除非出现新的具体痛点

---

## 4. 纪律与环境坑（沿用 + 新增）

### 4.1 纪律

- gold 门闩先于一切；**不改注入文本、不改 gold**（除非像 Hermes 收口那样显式重写并说明）
- 只 add 本批文件；数字带**当场命令 + HEAD hash + 窗口/快照日**
- 双探针（应命中 + 应不命中）后再下结论
- **发现即刀**（用户明确要求）：顺带真发现当场修或当场登记，不「留以后」
- 每步一个分支、测过再合 main；发版窗口内冻结 main
- 多关键词搜索用 Grep 工具或 `/usr/bin/grep`（WorkBuddy grep shim 假阴性）
- core 改动进 §7d `CORE_DIVERGE_LEDGER`（C-xxx）；follow 区新文件先查上游同名 → 归 own 或登记 DIVERSION
- 防放水探针**不得依赖真账本**（用 `parse_diversion_ledger(manifest_path=tmp)` 自足台账）——已犯两次，根治于 `d4a0ddce8d`
- 推送前跑一次 canary（防「推送即红」）

### 4.2 环境坑

| 坑 | 处置 |
|---|---|
| Python 3.9 跑 `s2_snapshot` 炸 `\|` 注解 | 用 `.venv/bin/python`（3.11+）；脚本已有 3.10 防呆 |
| gold 曾钉 `os.getcwd()` | 已归一 `{{CWD}}` 等；任何 cwd 可跑 `--check` |
| stdin 探针在 cwd=含 `agent/` 时抢先 import | `sys.path.insert(0, that_worktree)` |
| `git push` 偶发 `LibreSSL SSL_ERROR_SYSCALL` | 重试或 `git -c http.version=HTTP/1.1 push` |
| `TMPDIR` 指到仓库内 → 工作区事实假红 | 测例已强制 `dir="/tmp"` + `.git` 探测 |
| 并发写窗口内跑测试 | 结果一律作废（Hermes 纪律） |
| 远端 tag 查询撞强推窗口 | 静默后再 `git ls-remote` 确认 ref+`^{}` |

---

## 5. 交付报告索引（本会话）

- `reports/qclaw/s24-retire-fallback_20260923.md`
- `reports/qclaw/hermes-s24-closeout_20260923.md`
- `reports/qclaw/p3-disable-prompt-sections_20260923.md`
- `reports/qclaw/p3-policy-layering_20260923.md`
- `reports/qclaw/collect-feedback-tool_20260923.md`
- `reports/qclaw/hygiene-zones-core_20260923.md`
- `reports/qclaw/l014-max-chars_20260923.md`
- `reports/qclaw/numbers-and-ab-sentinels_20260923.md`

— 完 —
