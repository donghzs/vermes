# 发行版边界闸门（2026-09-21）

> 区间 `v2.4.9..main`（Vermes 侧 100 commits）。
> 判据：改动落在**上游跟随区**（`plugins/ tools/ harness/ cron/ .github/ docs/ scripts/`）= 契约税 —— 下次跟随上游时会冲突。要么登记（有意偏离），要么外置为插件。

## 1. 分区分布

| 分区 | 文件改动数 | 判定 |
|---|---|---|
| `own` | 478 | ✅ 发行版自有，正常 |
| `other` | 136 | — |
| `follow` | 48 | ⚠️ 契约税（需登记或外置） |
| `core` | 39 | 🔍 核心 diverge，个案评估 |

## 2. 契约税明细（跟随区改动）

| hash | 主题 | 跟随区路径 |
|---|---|---|
| `0af15752438b` | fix(security): finish plaintext-key cleanup — aux/custom_pro | `scripts/migrate_plaintext_provider_keys.py` |
| `b93a6e4cbbba` | fix(security): remove plaintext provider keys from config.ya | `scripts/migrate_plaintext_provider_keys.py` |
| `a56f345f0218` | docs(skill-routing): 三方决策简报 + QClaw 审计材料 + memory_provider 方 | `docs/AGENT_SKILLS_INDEX.md` |
| `a56f345f0218` | docs(skill-routing): 三方决策简报 + QClaw 审计材料 + memory_provider 方 | `docs/AUDIT_SKILLS_INDEX_BY_QCLAW_20260920.md` |
| `a56f345f0218` | docs(skill-routing): 三方决策简报 + QClaw 审计材料 + memory_provider 方 | `docs/QCLAW_POINTER_SKILL_TO_PASTE.md` |
| `a56f345f0218` | docs(skill-routing): 三方决策简报 + QClaw 审计材料 + memory_provider 方 | `docs/SPEC_coexistence_invariants_20260920.md` |
| `a56f345f0218` | docs(skill-routing): 三方决策简报 + QClaw 审计材料 + memory_provider 方 | `scripts/check_coexistence.py` |
| `c120e8cbea9b` | feat(skill-routing): pure channel-gate auto + SkillRouter pr | `scripts/measure_skill_usage.py` |
| `c120e8cbea9b` | feat(skill-routing): pure channel-gate auto + SkillRouter pr | `tools/skills_tool.py` |
| `c8a722c3cdb6` | docs: adopt QClaw P2 final stance — supersede coding-mode sp | `docs/SPEC_coding_mode_vermes_20260920.md` |
| `b64ac5c5a23d` | docs: Vermes coding-mode product spec (off default / auto=L1 | `docs/SPEC_coding_mode_vermes_20260920.md` |
| `1940d46a5de3` | docs: MiMo point-check A-prime channel gate — pass (034d41bb | `docs/POINT_CHECK_A1_CHANNEL_GATE_BY_MIMO_20260920.md` |
| `1940d46a5de3` | docs: MiMo point-check A-prime channel gate — pass (034d41bb | `docs/TASK_BOARD_20260920.md` |
| `82feb56cba5f` | docs: P2 brief absorbs QClaw A-prime channel-gate finding | `docs/DISCUSSION_p2_coding_context_mimo_20260920.md` |
| `b553c9e6c61d` | docs: MiMo P2 coding_context discussion brief (for QClaw + 董 | `docs/DISCUSSION_p2_coding_context_mimo_20260920.md` |
| `2a55fc432167` | docs: skills-index P1/P3/M7 handoff for QClaw cross-audit | `docs/HANDOFF_SKILLS_INDEX_TO_QCLAW_20260920.md` |
| `4547fe4f4aa1` | build(skills): web_dist gitHash 消 dirty + 工单板记 M6b/M7 | `docs/TASK_BOARD_20260920.md` |
| `195588fb2a68` | docs(skills): P1 auto 字节收益实测基线（18.4% / 5.2KB）+ P2 暂缓建议 | `scripts/measure_skills_index_p1.py` |
| `89efdd67cb9a` | docs(task board): restore M5 row beside M6 skills-index P1 | `docs/TASK_BOARD_20260920.md` |
| `6dbab9188156` | docs(task board): record skills-index P1 done (mimo 3f214459 | `docs/TASK_BOARD_20260920.md` |
| `7cbe8cae8faa` | docs(task board): W1 状态订正 + mimo 审 A7/yaml 扫尾结论 | `docs/TASK_BOARD_20260920.md` |
| `1bd16f6d878e` | fix(config): 6 处 yaml 写路径改就地 round-trip 保注释，消除注释抹除+默认值膨胀 | `plugins/memory/holographic/__init__.py` |
| `f5e47ec9e291` | feat(gateway): A7 first-DM auto home channel + M5-a yuanbao  | `docs/HANDOFF_TO_QCLAW_20260920.md` |
| `7458fd023dd3` | fix(mimo): M5 — 凭据写路径 config.yaml 保注释（关掉 M4/P1 残留） | `docs/TASK_BOARD_20260920.md` |
| `74ec7ff6c67d` | docs: point-check M4 rework e697aa9066 + open M5 follow-up | `docs/CROSS_AUDIT_M_20260920.md` |
| `74ec7ff6c67d` | docs: point-check M4 rework e697aa9066 + open M5 follow-up | `docs/TASK_BOARD_20260920.md` |
| `e697aa9066e1` | fix(mimo): M4 返工 — config.yaml 保注释原子写 + env 失败显式化 | `docs/TASK_BOARD_20260920.md` |
| `ea2f8619c297` | docs: cross-audit mimo M1-M4 (P1 config.yaml comment loss) | `docs/CROSS_AUDIT_M_20260920.md` |
| `ea2f8619c297` | docs: cross-audit mimo M1-M4 (P1 config.yaml comment loss) | `docs/TASK_BOARD_20260920.md` |
| `b02ffc3955ed` | fix(gateway): W3 email self-filter / W4 persistent notice de | `docs/TASK_BOARD_20260920.md` |
| `dcae24c0a4c6` | docs(task board): record W1/W2 verdicts | `docs/TASK_BOARD_20260920.md` |
| `f89435a43c52` | fix(gateway): reconnect queue lost its attempt counter and n | `plugins/platforms/irc/adapter.py` |
| `9bbf2f1ece13` | feat(p0a-mimo): M1–M4 上游对齐切片（度量基线/文档/CI lane/home channel GU | `.github/workflows/install-e2e.yml` |
| `9bbf2f1ece13` | feat(p0a-mimo): M1–M4 上游对齐切片（度量基线/文档/CI lane/home channel GU | `.github/workflows/js-tests.yml` |
| `9bbf2f1ece13` | feat(p0a-mimo): M1–M4 上游对齐切片（度量基线/文档/CI lane/home channel GU | `.github/workflows/tests-os.yml` |
| `9bbf2f1ece13` | feat(p0a-mimo): M1–M4 上游对齐切片（度量基线/文档/CI lane/home channel GU | `docs/TASK_BOARD_20260920.md` |
| `9bbf2f1ece13` | feat(p0a-mimo): M1–M4 上游对齐切片（度量基线/文档/CI lane/home channel GU | `scripts/diverge_metrics.py` |
| `e65f217752a0` | docs: parallel task board + self-contained handoff package f | `docs/HANDOFF_TO_MIMO_20260920.md` |
| `e65f217752a0` | docs: parallel task board + self-contained handoff package f | `docs/TASK_BOARD_20260920.md` |
| `81c28cc3e727` | docs: register MiMo Desktop skill roots in AGENT_SKILLS_INDE | `docs/AGENT_SKILLS_INDEX.md` |
| `d8c6f8fa1cd2` | docs: cross-agent skill index + roadmap note (E10 follow-up) | `docs/AGENT_SKILLS_INDEX.md` |
| `b7305707d4d2` | fix(gateway): unify home-channel resolution across notice, c | `cron/scheduler.py` |
| `c15631525cd7` | fix(2.5 B7): 修复 _verify_kanban_complete/_block 定义顺序导致模块 impo | `tools/kanban_tools.py` |
| `1f7d6105df54` | feat(2.5 S3): 文档记忆腿 A+B — 清 anti_patterns 僵尸读 + handoff 多域发射 | `tools/kanban_tools.py` |
| `11b9ca03fa5a` | feat(2.5 C3): stability 热路径探针产品化 — 默认关 + env/配置/Settings 开关 | `harness/__init__.py` |
| `11b9ca03fa5a` | feat(2.5 C3): stability 热路径探针产品化 — 默认关 + env/配置/Settings 开关 | `harness/stability_hotpath.py` |
| `6d56603c3d9b` | feat(2.5 B7): verify_fn 再扩容 — kanban_complete/block 状态外证 + 快 | `tools/kanban_tools.py` |
| `b1ebfbd58ded` | feat(2.5 B1): verify_fn 扩容 — kanban_create DB 外证 + export/sa | `tools/kanban_tools.py` |

## 3. 闸门结论

**FAIL** —— 48 处契约税，需优先外置为插件，否则跟随上游的成本将持续累积。
