# UPSTREAM_SYNC.md — Vermes 与上游 Vermes Agent 同步策略

## 仓库关系

| 仓库 | remote | 角色 |
|---|---|---|
| `origin` | github.com/donghzs/vermes | Vermes 主仓库 |
| `upstream` | github.com/donghzs/vermes | 官方 Vermes Agent |

## 版本线状态

- **上游最新**：0.18.2+（commit 4a69a6620）
- **Vermes 当前**：v2.3.1（基于上游 fork，已自主演进）
- **分叉点**：约 2026-05-17，此后 Vermes 独立编号
- **差异规模**：~5743 文件，+238K/-1.2M 行（含上游大规模重构）

## 同步原则

1. **不自动全量合并** — 上游变动巨大，全量 merge 风险不可控
2. **选择性 cherry-pick** — 仅同步安全修复、关键 bug fix、高价值特性
3. **不追上游 provider** — 已由 `openai_compat` 通用 provider 解决
4. **保持 Vermes 独立演进** — 进化系统、ScholarForge、Electron 桌面化是差异化

## 已同步的 cherry-pick 记录

| commit | 说明 | 日期 |
|---|---|---|
| 447f5d6a1 | MCP resource blocks + mixed tool batch segmentation | 2026-07-xx |
| cb0819703 | thread-safe asyncio.Queue + agent timeout + SSE duration cap | 2026-07-xx |

## 同步流程

```
1. git fetch upstream
2. git log upstream/main --oneline -30  # 浏览最近提交
3. 评估每个 commit：
   - 安全修复 → 优先 cherry-pick
   - 关键 bug fix → 评估冲突后 cherry-pick
   - 新特性 → 评估是否对 Vermes 有价值
   - 重构 → 一般跳过（冲突风险高）
4. git cherry-pick <commit-hash>
5. 解决冲突 → 跑测试 → git push
```

## 不同步的类别

- **前端 UI 重构** — Vermes 前端已完全自定义
- **provider 适配** — openai_compat 通用方案覆盖
- **CLI 交互改造** — Vermes 桌面模式不需要 TUI 增强
- **CI/CD 管道** — Vermes 有自己的 GitHub Actions

## 版本编号规则

- Vermes 使用 `v2.x.y` 独立编号，不与上游 `v0.x.y` 对齐
- 安全/bug fix：patch 版本 +1（v2.3.1 → v2.3.2）
- 功能新增：minor 版本 +1（v2.3.1 → v2.4.0）
