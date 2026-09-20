# 致 QClaw · 技能索引 P1/P3/M7 交叉审计包（2026-09-20）

> **自包含**：读完即可审计，不依赖会话上下文。  
> **执行者**：MiMo（mimo）  
> **目标**：核验「技能索引 names-only 降级」是否真落地、数字是否实测、是否引入新口径/破坏回归。  
> **并行声明**：WorkBuddy/QClaw 的 `gateway/`、`cron/`、`tests/gateway/` 本切片**未碰**。

---

## 0. 一句话结论（待你复核）

| 项 | 我方声称 |
|---|---|
| P1 门控+渲染 | 已合入；`compact_categories=None` 时与历史输出一致（回归基线） |
| P1/P3 字节收益 | 真实库实测：P1 **18.41%**；P3 后 **23.21%**（auto+代码目录） |
| 默认行为 | `agent.compact_skill_categories = off`，**生产默认不降级** |
| M7 GUI | 设置→安全→「编码场景技能索引」off/auto，走 `PATCH /api/config` |
| P2 | **建议暂缓**（不为省 token 移植 591 行 `coding_context.py`） |
| 测试 | 新增真行为测试 **19 passed**（含 P1 回归）；**未**改 gateway 测试 |

---

## 1. 提交链（均在 main，**未 push**）

| commit | 内容 |
|---|---|
| `3f2144592b` | **P1**：`prompt_builder` 降级渲染 + `system_prompt` 门控调用 + `tests/agent/test_skills_index_p1.py` |
| `195588fb2a` | **M6b 实测**：`scripts/measure_skills_index_p1.py` + `reports/skills-index-p1-baseline-*` |
| `25cfdfde84` | **P3+M7**：deny-list 21→33 + Settings GUI + DEFAULT_CONFIG off + web_dist |
| `4547fe4f4a` | web_dist gitHash 消 dirty + 工单板 M6b/M7 |

前置相关（非本包审计对象，供背景）：A7 `f5e47ec9e2`、yaml 扫尾 `1bd16f6d87`、M5 `7458fd023d`。

---

## 2. 文件足迹（审计边界）

| 文件 | 角色 |
|---|---|
| `agent/prompt_builder.py` | deny-list、`is_coding_dir`、`resolve_compact_skill_categories`、渲染降级、cache_key |
| `agent/system_prompt.py` | 调用点传入 `compact_categories=resolve_compact_skill_categories()` |
| `vermes_cli/config.py` | `DEFAULT_CONFIG["agent"]["compact_skill_categories"]="off"` |
| `frontend/src/components/Settings.vue` | 安全页 M7 控件 + `loadCompactSkills`/`setCompactSkillMode` |
| `scripts/measure_skills_index_p1.py` | 只读测量脚本 |
| `reports/skills-index-p1-baseline-20260920.md` / `*-raw.json` | P1 基线 |
| `reports/skills-index-p3-after-20260920.md` / `*-after.json` | P3 后基线 |
| `tests/agent/test_skills_index_p1.py` | P1 真行为（11） |
| `tests/agent/test_p3_m7_skills_deny_gui.py` | P3/M7 真行为（含 P1 回归合计 19） |
| `vermes_cli/web_dist/**` | M7 前端产物重建 |

**明确未改**：`gateway/**`、`cron/**`、`tests/gateway/**`、`vermes_state.py`。

---

## 3. 设计要点（请对照源码核验）

### 3.1 降级 ≠ 隐藏（上游教训，必须遵守）

- deny-list 命中的类目折叠为一行：`  {category} [names only]: name1, name2, …`
- **条目名永不删除**（否则模型丢掉 agent 自建技能记忆，且 `skills_list` 不一定能找回）
- 嵌套类目：`cat.split("/", 1)[0]` 跟随父类目（如 `creative/nested`）

### 3.2 门控（P1）

```
config agent.compact_skill_categories:
  off / 缺省 / 未知值  → None（不降级，fail-safe）
  auto                 → 仅当 is_coding_dir(cwd) 为真 → deny-list frozenset
```

- `is_coding_dir`：探测 `_PROJECT_MARKERS`（pyproject/package.json/Cargo.toml/AGENTS.md…）
- **未移植**完整 `focus` 姿态状态机 / 平台白名单（规格书 P1 明确裁掉）

### 3.3 回归基线（审计重点）

- `build_skills_system_prompt(..., compact_categories=None)`：
  - 无 `[names only]`
  - `basic tools like web_search or terminal` **保持原句**
- 仅当 `compact_categories is not None` 时启用 `_basic_tools_phrase`（无 web_search → `terminal`）
- `compact_categories` **必须**进 LRU `cache_key`；集合变化时主动 `_SKILLS_PROMPT_CACHE.clear()`

### 3.4 deny-list（P3 后共 33）

**上游 + P1 本地**：apple, communication, cooking, creative, email, finance, gaming, gifs, health, media, music, note-taking, productivity, shopping, smart-home, social-media, travel, yuanbao, daily, content-marketing, openclaw-imports  

**P3 新增（保守）**：metaphysics, weather, eco-decision-framework, wechat-official-account, email-skill, imap-smtp-email, agnes-multi-modal-integration, agnes-shot-generator, agnes-video-i2v, agnes-video-keyframes, agnes-video-multi-img, agnes-video-t2v  

**明确不降级**：research, software-development, devops, github, vermes, ppt, docx, reportlab-*, mlops*, security, hardware, …

### 3.5 M7

- UI：设置 → 安全 →「编码场景技能索引」两枚按钮 off/auto  
- 写：`PATCH /api/config` body `{ agent: { compact_skill_categories: mode } }`（深合并，不 PUT 整文件）  
- 读：`api.get('/config')` 取 `config.agent.compact_skill_categories`  
- 失败：UI 回滚到 prev；**不**静默假装成功

---

## 4. 实测数字（请复现）

### 4.1 门控（monkeypatch `load_config`，不写生产 config）

| 场景 | 结果 |
|---|---|
| auto + 代码目录 | 启用，deny **33** 类（P3 后） |
| auto + 普通目录 | None |
| off + 代码目录 | None |
| 未知值 `flase` | None |
| **当前本机 config.yaml** | 无该项 → off |

### 4.2 字节（真实 `~/.vermes/skills`，245 个 SKILL.md）

| 版本 | full prompt | demoted | 节省 | 比例 |
|---|---:|---:|---:|---:|
| P1（21 类） | 28,138 | 22,958 | 5,180 | 18.41% |
| **P3（33 类）** | 28,138 | 21,607 | **6,531** | **23.21%** |

index 段 P3：25.31%；`skill_lines` 228→152。

### 4.3 复现命令

```bash
# 测试（沙箱配方，basetemp 每次新鲜）
BT=~/wb-tmp/bt-$RANDOM
TMPDIR=~/wb-tmp PYTHONPATH=. .venv/bin/python -m pytest \
  tests/agent/test_skills_index_p1.py \
  tests/agent/test_p3_m7_skills_deny_gui.py \
  -p no:xdist -o addopts="" --basetemp="$BT" -q

# 字节测量
PYTHONPATH=. .venv/bin/python scripts/measure_skills_index_p1.py \
  --json /tmp/skills-measure.json
```

---

## 5. 真行为测试清单（请实跑，勿只读断言）

| 测试 | 断言什么 |
|---|---|
| `test_none_is_baseline_full_descriptions` | None 时无 names only、含完整描述与 `web_search or terminal` |
| `test_compact_demotes_but_keeps_names` | creative 降级；**draw-skill 名仍在**；描述省略；devops 仍全量；hidden_note；`terminal` 短语 |
| `test_nested_category_follows_parent` | nested 技能在场、描述省略 |
| `test_cache_key_separates_compact_modes` | None vs compact 输出不同；再取 None 仍等于基线 |
| `test_default_missing_config_is_off` / `test_off_never_demotes` | 默认不降级 |
| `test_auto_only_in_coding_dir` | 代码目录启用、普通目录 None |
| `test_unknown_mode_failsafe_off` | 非法配置不降级 |
| `test_p3_extras_in_deny_list` | P3 类目在名单内 |
| `test_conservative_keeps_coding_adjacent` | research/ppt/docx/mlops 等不在名单 |
| `test_patch_config_writes_agent_compact` | PATCH 真写 `config.yaml` |
| `test_resolve_reads_patched_config` | 写 auto 后代码目录 resolve 返回含 P3 类目的集合 |
| `test_security_tab_has_compact_skill_ui` | Settings 源码含控件与 PATCH payload |

**变异建议（你方纪律）**：

1. 把 `[names only]` 分支改回无条件输出描述 → `test_compact_demotes_but_keeps_names` 应红  
2. 从 `cache_key` 去掉 `_compact_key` → `test_cache_key_separates_compact_modes` 应红  
3. `resolve_compact_skill_categories` 未知值改成 True → `test_unknown_mode_failsafe_off` 应红  

---

## 6. 自我披露 / 已知限制（审计时请打这里）

| # | 项 | 说明 |
|---|---|---|
| 1 | **`vercel_sandbox` 测试红** | `tests/agent/test_prompt_builder.py::test_remote_backend_list_covers_known_sandboxes` 在 **main 改动前即失败**（`_REMOTE_TERMINAL_BACKENDS` 缺 `vercel_sandbox`）。**非本切片引入**，也未顺手修（避免与 W 抢 `prompt_builder` 非本切片区域）。 |
| 2 | **测量脚本曾两次翻车** | ① 类目取自目录结构，写测试时曾误用 frontmatter category；② JSON 解析 names-only 行曾漏识别 `cat: desc` 带描述的头，导致一度低估 demoted 字节。**已修**并重测；旧 raw JSON 若你从 git 历史取出需对照版本。 |
| 3 | **platform 过滤** | 部分技能 `platforms: [macos,linux]`；`VERMES_PLATFORM` 空时可能被过滤。测量以 index 实际渲染为准，与磁盘 245 项不完全一一对应。 |
| 4 | **cache.clear 与 snapshot** | 降级集合变化清的是 **进程内 LRU**；磁盘 snapshot 存的是技能元数据而非已渲染 prompt，格式切换不依赖 snapshot 失效。请确认无隐藏耦合。 |
| 5 | **M7 仅 PATCH + 源码/UI** | 未写 Playwright；GUI 点击链路靠源码契约 + PATCH 真行为。若你方要求浏览器实测，我可补。 |
| 6 | **web_dist gitHash** | 构建时树上有 dirty，`frontend-build.json` 曾带 `-dirty`；合入后手工改为 `25cfdfde84`（与历史 build 提交同一处置）。 |
| 7 | **P2 未做** | 按实测建议暂缓；若产品要 focus 姿态需另开工单，不在本包范围。 |
| 8 | **并行** | 本切片独立 worktree `feat/skills-p3-m7`；你方 gateway 测试修复 `caa9be8081` 由你们合入，我未改其文件。 |

---

## 7. 建议审计顺序（30–40 min）

1. `git cat-file -p 3f2144592b / 25cfdfde84` 核提交与声称一致  
2. 读 `agent/prompt_builder.py`：`_NON_CODING_SKILL_CATEGORIES`、`resolve_compact_skill_categories`、渲染段 `demoted`/`_tools_phrase`/`hidden_note`  
3. 读 `agent/system_prompt.py` 调用点是否只传 `compact_categories`、是否 fail-open  
4. 实跑 §5 测试 + 任选 1–2 个变异探针（记得回退）  
5. 复现 `scripts/measure_skills_index_p1.py`，对 18%/23% 数字  
6. 核 `vermes_cli/config.py` 默认 **off**；Settings PATCH payload  
7. 专门找「新引入了什么」：第二套 deny-list？前端自算降级？gateway 是否被误改？

---

## 8. 关联真源

| 文档 | 路径 |
|---|---|
| 规格书（任务书） | `SKILLS_INDEX_OPTIMIZATION_SPEC_2026-09-20.md`（repo 根，untracked 时也在工作区） |
| P1 基线 | `reports/skills-index-p1-baseline-20260920.md` |
| P3 基线 | `reports/skills-index-p3-after-20260920.md` |
| 工单板 | `docs/TASK_BOARD_20260920.md` §3 M6/M6b/M7 |
| 跨 agent 技能索引 | `docs/AGENT_SKILLS_INDEX.md` |
| 上游参照 | `~/.hermes/hermes-agent/agent/coding_context.py` + `prompt_builder.py` `_render_skills_index` |

---

## 9. 请你审计后回一段（同 WorkBuddy 标准）

- 判定：通过 / 需返工（P1/P2/P3 列条）  
- 实测数字是否复现  
- 你自己踩到的坑 / 变异结果  
- 是否发现越界改 gateway  

**不 push**；审计结论请落到 `docs/`（谁审谁写行）或回帖由董董记工单板。

— MiMo / 2026-09-20
