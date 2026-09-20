# P2 收口执行方案 · W-L4 + W-L5（加法，非模式裁决器）· 2026-09-20

> **状态**：待董董勾选 §6 后执行。本文只描述"做什么、改哪里、怎么测"，不写代码。
> **真源**：本文件；上游参考 `~/.hermes/hermes-agent/agent/coding_context.py`。
> **红线**：不移植 coding_context 整包（591 行）；不建 focus/auto 姿态状态机；不按 cwd/platform 分支裁决"是否 coding"；§12 冻结不 push。

---

## 0. 一句话

补两块**正确性/安全/信息前置**的"加法"，与"是否在写代码"的姿态开关**完全解耦**：
- **W-L4**：三条通用纪律文案（`path:line` / 不顺手重构 / 默认不 commit·push），并入现有通用 guidance。
- **W-L5**：工作区事实块（git root/branch/dirty/manifests/verify 命令），有 git 区就注入、探测失败就空、永不挡消息。

---

## 1. W-L4：三条纪律文案

### 1.1 缺口确认（MiMo 已逐条 diff，QClaw 复核属实）

| 纪律 | 现状 | 结论 |
|---|---|---|
| 先读再改 / 勿臆造 | ✅ `prerequisite_checks` + verify-first + context files | 已有 |
| 验证后再宣称完成 | ✅ OPENAI `<verification>` / `TASK_COMPLETION_GUIDANCE` | 已有 |
| **`path:line` 精确引用** | ❌ 未见 | **缺** |
| **只改任务相关、不顺手重构** | ❌ 未见 | **缺** |
| **默认不 commit/push**（除非用户明确要求） | ❌ 提示层未见；`tools/approval.py:402-406` 仅拦 `reset --hard`/`push --force`/`clean -f`/`branch -D` | **缺** |

### 1.2 并入点（精确）

- **文件**：`agent/prompt_builder.py`
- **位置**：`TASK_COMPLETION_GUIDANCE`（第 374 行起）——这是"完成任务纪律"的现有载体，语义最贴合。追加三段，或新增独立常量 `EDITING_GUARDRAILS_GUIDANCE` 并注册进注入表。
- **注入机制**（已核实，零新机制）：
  - `agent/prompt_builder.py` 定义常量 → `agent/system_prompt.py:55-73` `_PROCESSOR_FALLBACK` dict 加键 → `system_prompt.py:236-326` 注入点用 `_proc_or_default(name)` 调用。
  - 若独立常量：加 `_PROCESSOR_FALLBACK["editing_guardrails"]` + 在 `tool_guidance` 组装段（`:244-263` 附近）追加 `_proc_or_default("editing_guardrails")`，注入条件建议跟 `task_completion` 一致（`agent._task_completion_guidance` flag 且 `valid_tool_names` 非空）。

### 1.3 三条文案草拟（最终措辞执行时定）

```
# Editing guardrails
- When citing code or files, reference exact locations (`path:line`), never paste entire files or vague "somewhere in X".
- Change only what the task requires. Do not refactor, reformat, or "clean up" unrelated code.
- Do not run `git commit` / `git push` unless the user explicitly asked. Report your changes and let the user decide when to commit.
```

### 1.4 关于「默认不 commit/push」是否做工具层硬闸 —— **QClaw 保留：后置**

- approval.py 拦的是"危险命令"，而"默认不提交"是"工作流默认策略"，性质不同。
- 若做 approval 硬拦，会与现有 `git_commit_done` 完成态语义冲突（要么每次 commit 打断，要么静默默认不提交致用户误以为已提交）。
- **建议**：提示层（W-L4 文案）先行；硬闸**观察后置**——等出现"agent 乱提交"实例，再单独立安全工单。

---

## 2. W-L5：工作区事实块

### 2.1 参考实现（上游，已读全文）

`coding_context.py:517 build_coding_workspace_block(cwd)`，纯 cwd 驱动、fail-open：
- 输出 `Workspace (snapshot at session start — re-check with git before acting on it):`
- `- Root:` / `- Branch:`（含 detached/upstream/ahead/behind）/ `- Worktree:`（linked 检测）/ `- Status:`（clean 或 dirty 分类）/ `- Recent commits:`（3 条）
- `- Project:`（manifests + package managers）/ `- Verify:`（verify commands）/ `- Context files:`

### 2.2 Vermes 已有 vs 需新建

| 能力 | Vermes 现状 | 处置 |
|---|---|---|
| git root 探测 | ✅ `agent/prompt_builder.py:62 _find_git_root` | 复用 |
| project markers | ✅ `prompt_builder.py:1110 _PROJECT_MARKERS`（22 项，含 AGENTS.md/CLAUDE.md/.cursorrules） | 复用 |
| is_coding_dir | ✅ `prompt_builder.py:1134` | 可复用（但 W-L5 应**独立于** compact_skill_categories 的 auto 判定，见 §2.4） |
| `git` 命令执行 helper（`_git()`） | ❌ 无 | **新建**（subprocess，fail-open 返回空） |
| `git status --porcelain=2 --branch` 解析（`_parse_status`） | ❌ 无 | **新建** |
| manifest/package_manager/verify 探测（`detect_project_facts`） | ❌ 无 | **新建**（薄版：扫 manifests → 映射 package manager → 提取 verify commands） |

### 2.3 注入点

- 新建 `agent/workspace_facts.py`（或并入 `prompt_builder.py`）：`build_workspace_block(cwd=None) -> str`，返回空串当无 git 区/探测失败。
- 注入到 `agent/system_prompt.py` 的 **context tier**（`:444-462` 附近，`context_parts` 段，与 `build_context_files_prompt` 并列）——它是"cwd-dependent、会话间可变"的正确层级，**不进 stable_parts**（避免污染 system prompt 缓存）。
- `cwd` 来源：对齐现有 `_context_cwd = os.getenv("TERMINAL_CWD")`（`:454`），gateway 下用 TERMINAL_CWD，桌面/CLI 用进程 cwd。

### 2.4 关键：W-L5 的注入判据 = 「有无 git 区」，**不是**渠道/平台

> 这是 QClaw 对 MiMo "W-L5 先桌面/交互式"的唯一推进，也是董董「渠道是承载不是内容」的直接推论：
> - "防过期 git 状态瞎改"的价值在 **IM 渠道同样成立**（telegram 上说"改个 bug"，agent 同样要知道当前 branch/dirty）。
> - 注入判据应为 `build_workspace_block()` 是否返回非空，**不按 platform 分支**（避免重蹈 A′ 渠道门误判）。
> - 但 gateway 单进程 cwd=仓库根的风险仍在：TERMINAL_CWD 为空时，`build_workspace_block()` 会探测到安装目录的 git 区。**须复用 A′ 的教训**——用 TERMINAL_CWD 优先，无 TERMINAL_CWD 且 platform 为 messaging 时不注入（此处 platform 只是"拿不到工作目录时的兜底"，不是内容判据）。

### 2.5 成本护栏

- 注入块控制在 ~10-15 行（Root/Branch/Status/Verify 即可，Recent commits 可省或限 1 条）。
- 仅当 git 区存在才注入；纯聊天（无 git 区）零成本。
- byte-stable：同 cwd 同 git 状态下输出稳定，便于缓存（若放 volatile tier 则无需缓存考虑）。

---

## 3. 测试方案（真行为测试，禁读源码字符串弱断言）

### 3.1 W-L4
- `tests/agent/test_editing_guardrails.py`：
  - 断言 `TASK_COMPLETION_GUIDANCE`（或新常量）注入后，system prompt 含 `path:line` / `Do not refactor` / `Do not run git commit` 子串。
  - 断言 `_proc_or_default("editing_guardrails")` 在 processor 缺失时 fallback 到常量（对齐现有 `_PROCESSOR_FALLBACK` 测试模式）。
  - 断言普通 `git commit` **不**触发 approval（若做了硬闸才测，本方案不硬闸）。

### 3.2 W-L5
- `tests/agent/test_workspace_facts.py`：
  - tmp git repo（`git init` + 一个 dirty 文件 + pyproject.toml）→ `build_workspace_block(cwd)` 含 Root/Branch/Status 非 clean/Verify。
  - 非 git 目录 → 返回 `""`。
  - `git` 命令失败（PATH 伪造或空 git）→ 返回 `""`，不抛异常（fail-open）。
  - 无 `TERMINAL_CWD` 且 platform=telegram → 不注入（兜底逻辑）。
  - 有 `TERMINAL_CWD` 指向 git 区 → 注入（IM 渠道也注入，验证"按 git 区不按渠道"）。

---

## 4. 排期（MiMo §6，QClaw 无异议）

| ID | 内容 | 量级 | 风险 |
|---|---|---|---|
| P-a | W-L4 三条文案并入 guidance | 极小 | 无 |
| P-b | W-L5 探测 + prompt 块（§2.4 判据） | 小–中 | 设计 fail-open + TERMINAL_CWD 兜底 |
| P-d | M7 token 阈值降级（独立，另行） | 小 | 董董拍板阈值 |

---

## 5. 与 A′ / M7 的关系（防回归）

- **A′（034d41bb69）不动**：它管的是 `resolve_compact_skill_categories` 的"技能索引降级"渠道门，与 W-L4/W-L5 是两条独立链路。
- **M7 token 阈值降级**：独立于本方案，若批则另行实施；本方案不改 `resolve_compact_skill_categories` 的 auto 语义。

---

## 6. 待董董勾选

- [ ] **批 W-L4**（三条纪律文案并入通用 guidance）
- [ ] **批 W-L5**（工作区事实块，按 §2.4「有 git 区就注入」而非渠道）
- [ ] **「默认不 commit/push」**：仅提示层先行（QClaw 建议），还是同时立项硬闸（观察后置）？
- [ ] **M7 token 阈值**：并行批 or 另行？

**QClaw 默认建议**：批 W-L4 + W-L5（提示层）；"默认不 commit"提示层先行、硬闸观察后置；M7 另行。

— QClaw 整理 · 待董董在 §6 勾选后执行
