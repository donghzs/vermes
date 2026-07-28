# Task Artifact: Vermes 家底复合审计（加固版）

**Time**: 2026-07-28 20:12 GMT+8
**Objective**: 对用户提供的初版审计报告（8.1 分）做复合加强审计——逐条用真实代码/仓库取证，修正口径错误与事实偏差，为下一步优化方案做准备。

**Key Reasoning / 取证结论**:
- 初稿多数结论方向对，但**量化数字大量需校准**：
  - 生产源码真实 **544,006 行**（非 42 万，初稿漏算 cli.py/run_agent.py/tui_gateway 等顶层）；测试 **458,563 行 / 1309 文件**（精确吻合）；测试:生产 **≈0.85:1**（非 1:1）。
  - 上帝文件 **25+ 个 >3000 行**（非 20）；渠道 adapter **25 个**（非 35，初稿高估 10）。
  - **静默吞异常仅 8 处**（非 247，初稿把 `except: logger.warn` 也算入，需分 L0/L1/L2 三级）。
  - **migrations 机制并非缺位**（初稿"缺位"论断错误）：`migrations/` 目录存在 + `update_manager.run_migrations` 驱动，但文件稀疏（仅 1 个），`SCHEMA_VERSION=14` 仍内联 ALTER 兜底 → 实为双轨并存。
- **P1-① 更新断路成立但根因更隐蔽**：不是"catch 不回退 web"，而是 `update.js:73` 的 `if (isDesktop && window.vermes?.checkForUpdates)` 存在性判断永远为真（preload 暴露了孤儿 API）→ 桌面端永远卡注定失败的 electron 路径、永不降级 web。Agent 框架更新不受影响是因 `agent:*` handler（main.js L731/L746）根本没被删，非"走 web"。
- **P1-② CI 门禁转硬 已落地（非待办）**：原 `|| true` 软化确证于 `ci-quality-gate.yml:40`，但已在 P1 落地时修复（commit 227928f7b，test-baseline 转硬）。真正缺口是覆盖窄（仅 tests/gateway/ ~25%），降级为 P2 重定义为"扩大 CI 测试覆盖"（三层守门模型，见报告 §6）。
- 安全(HARDLINE)、i18n 零前端、四大成熟渠道(~5k行) 等论断精确吻合。

**Conclusions / 下一步**:
- 报告落盘：`docs/report-vermes-audit-20260728-reinforced.md`（含五维分项、P1 复核、静默吞分级、带证据优化优先级 P1/P2/P3、可复现取证命令）。
- 优化优先级（修正）：P1（修更新断路，已落方案 B）→ P2（CI 测试覆盖扩展[原 P1-② 降级重定义] / migrations 追缴 / 上帝文件分治）→ P3（i18n / 无障碍+桌面增强 / 启动守卫 harness 进 L0）。
- 综合评分维持 **8.1/10**，但支撑数字已诚实校准，决策干扰已清除。
- 当前分支 `feature/c5-g6-image-evict`（c5 已本地提交未 push）；本审计为文档/取证工作，未改生产代码，不改变任何 commit。
