# 收 feedback_tool + 修 TMPDIR 假红 · 2026-09-23

> 状态：**已落地**。分支 `feat/collect-feedback-tool`（worktree `/Users/dongzusheng/Projects/vermes-electron-feedback`）。
> 基线：`f45a9b65c5`。清掉最长悬挂脏文件 + WorkBuddy 点的 TMPDIR 假红。

---

## 1. `tools/feedback_tool.py` 单独收（+58/−3）

多份报告写过「单独收、勿夹带」，本刀正式入库。内容（前会话 WIP）：

| 件 | 行为 |
|---|---|
| `_resolve_thumbs_kind` | `up/down` + legacy 别名（`like/dislike/1/0/positive`）归一，防 schema 漂移静默落无意义 `thumbs_down` |
| `_resolve_feedback_target` | target 缺省时从 `ctx.task_id/session_id` 或 kwargs 回填；全空 → `(unspecified)`，点踩不得落空 target |
| `_resolve_agent` | dispatcher 未传 `agent` 时优雅降级（注释已说明） |
| `thumbs` / `submit_correction` | 接上述归一/回填后落库 |

## 2. `test_non_git_dir_empty` TMPDIR 假红（WorkBuddy）

**根因**：`tempfile.mkdtemp()` 尊重 `TMPDIR`；若指到仓库内（如 `$PWD/.pytest-tmp`），临时目录落在 git 区，`build_workspace_block` 正确识别为 git 区 → 断言 `""` 假红。

**修法**：强制 `dir="/tmp"` + 断言前向上探测 `.git`（若仍落在 git 区则 `skipTest` 而不是误红）。

| 环境 | 修前 | 修后 |
|---|---|---|
| 系统 TMPDIR | 绿 | 绿 |
| `TMPDIR=$PWD/.pytest-tmp` | 红 | **绿** |

## 3. 契约测（+3）

- `test_thumbs_kind_normalization`：别名/大小写/空/None 全覆盖
- `test_feedback_target_fallback`：ctx / kwargs / 全空
- `test_thumbs_handler_normalizes_legacy_feedback`：端到端 legacy `like` + 空 target 落库

## 4. 测试口径

```
test_feedback_learning (8) + test_w_l4_l5_m7_threshold        →  21 passed
s2_snapshot --check                                           →  17×3 逐字绿（注入未动）
```

## 5. 改动清单

| 文件 | 摘要 |
|---|---|
| `tools/feedback_tool.py` | 收 WIP：thumbs 归一 + target 回填 + agent 降级 |
| `tests/agent/test_feedback_learning.py` | +3 契约测 |
| `tests/agent/test_w_l4_l5_m7_threshold.py` | TMPDIR 强制 `/tmp` + git 区探测 |

**未夹带**：无其他脏文件；gold/注入零变化。

## 6. 之后排队

| 项 | 备注 |
|---|---|
| L-014 `max_chars` | 单片段长度上限（工单 §8 唯一缺口） |
| 卫生刀 | 交接报告入库 + 工单 P3 措辞对齐 |
| P3 退役 | `v3.0.0-distribution` 前 |

— 完 —
