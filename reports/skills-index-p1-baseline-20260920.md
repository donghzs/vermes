# 技能索引 P1 · auto 字节收益实测基线（2026-09-20）

- **脚本**：`scripts/measure_skills_index_p1.py`（只读测量，不改生产 config）
- **原始 JSON**：`reports/skills-index-p1-baseline-raw.json`
- **技能库**：`~/.vermes/skills`（实测 **245** 个 `SKILL.md`）
- **P1 实现**：`3f2144592b`（默认 `agent.compact_skill_categories=off`）
- **与 W/QClaw 并行**：本切片仅 `scripts/` + `reports/`，未碰 `gateway/`、`cron/`、`tests/gateway/`

---

## 1. 门控真行为（monkeypatch config）

| 场景 | `resolve_compact_skill_categories()` |
|---|---|
| config=`auto` + 代码目录（含 `pyproject.toml`） | **启用**，deny-list **21** 类 |
| config=`auto` + 普通目录 | **None（不降级）** |
| config=`off` + 代码目录 | None |
| config 未知值（如 `flase`） | None（fail-safe off） |
| **当前本机 config.yaml** | 未配置该项 → **off**（`current_config_auto_enabled_in_coding_dir=false`） |

→ 生产默认 **不降级**；打开 auto 后仅在代码目录生效。

---

## 2. 字节收益（真实技能库，deny-list 含本地补充）

| 口径 | 全量 prompt | 降级 prompt | 节省 | 比例 |
|---|---:|---:|---:|---:|
| **System prompt 总字节** | 28,138 | 22,958 | **5,180** | **18.41%** |
| `<available_skills>` index 段 | 26,453 | 21,110 | **5,343** | **20.2%** |
| 描述条目行数 | 228 | 164 | 64 行 | — |

与规格书预估对照：

| 来源 | 预估 |
|---|---|
| 规格书（仅上游 deny-list） | 15.6% / ~4.7KB |
| 规格书（+本地补充 daily/marketing/openclaw） | ~24% |
| **本机 P1 实测** | **18.41% / 5.18KB** |

落在预估区间内；未到 24% 的主因：`research` 等编码相关类目按规格**保守保留全量**。

---

## 3. 降级了哪些类目（names only，条目名仍在）

| 类目 | 索引内技能数 | 全量描述字节 |
|---|---:|---:|
| **creative** | **26** | **1,628** |
| content-marketing | 2 | 122 |
| daily | 4 | 550 |
| apple | 6 | 338 |
| productivity | 9 | 513 |
| media | 5 | 266 |
| social-media | 3 | 192 |
| travel | 1 | 162 |
| openclaw-imports | 2 | 120 |
| gaming | 2 | 100 |
| 其余（email/note-taking/smart-home/yuanbao…） | 6 | 221 |
| **合计（被降级）** | **64** | **4,212** |

差值说明：prompt 级节省 **5,180** > 描述字节 **4,212**，多出部分来自「多行 `- skill: desc` 折叠成单行 names-only + 类目头/换行」的结构开销，符合预期。

**未降级（保全量）且字节靠前的编码相关类目**（P2 若扩名单需谨慎）：

- `software-development` 49 技 / 3,689B
- `devops` 20 / 1,520B
- `research`（规格明确保留）等

---

## 4. P2 建议（基于实测，非拍脑袋）

| 问题 | 结论 |
|---|---|
| **字节收益是否支撑移植 591 行 `coding_context.py`？** | **不支撑。** P1 已拿到 **~18% / ~5KB per system prompt**；P2 增量收益主要不在「再省描述」，而在完整 focus 姿态 / 工具集折叠 / 平台白名单。 |
| **建议** | **暂不启动 P2。** 维持 P1；观察 1–2 周真实会话。 |
| **若要继续优化 prompt 字节** | 优先 **P3 名单校准**（评估 `metaphysics`/`agnes-video-*` 等是否在纯编码场景降级）与 **技能库治理**（归档不用的 creative 类），成本远低于 P2。 |
| **若要做 P2** | 触发条件应是：需要 messaging/CLI/desktop **统一 focus 姿态**、工具集按 coding collapse、或跨 agent 与上游行为对齐——不是为了 token。 |
| **默认是否改 auto？** | 实测 auto 在代码目录安全可用，但上游教训是「index 变化对用户 surprise」。**建议保持默认 off**，由用户/产品在设置里显式开 `auto`（GUI 开关可作后续 M7，非本切片）。 |

### 启用方式（给愿意试的用户）

```yaml
# ~/.vermes/config.yaml
agent:
  compact_skill_categories: auto   # off=默认；auto=仅代码目录 names-only
```

在含 `pyproject.toml` / `package.json` 等标记的目录下，系统 prompt 技能索引约少 **5KB（18%）**；技能仍可 `skill_view(name)` 加载。

---

## 5. 复现

```bash
cd <repo>
PYTHONPATH=. .venv/bin/python scripts/measure_skills_index_p1.py \
  --json reports/skills-index-p1-baseline-raw.json
```

脚本会打印 gate_simulation / env_platform / macos_platform / recommendation_inputs。

---

## 6. 并行边界声明

| 路径 | 本切片 |
|---|---|
| `scripts/measure_skills_index_p1.py` | ✅ 新增 |
| `reports/skills-index-p1-baseline-*.md/json` | ✅ 新增 |
| `agent/prompt_builder.py` 等 | 不改（P1 已合入） |
| `gateway/` `cron/` `tests/gateway/` | **未碰**（W/QClaw 并行） |
| 生产 `config.yaml` | **未改**（测量用 monkeypatch） |
