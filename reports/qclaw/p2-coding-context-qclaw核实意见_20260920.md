# P2 coding_context 大移植 — QClaw 核实后的意见（2026-09-20）

## 背景
董董纠结：Vermes 要不要像上游 Hermes 一样有 coding 模式（自动/手动开关）、
"大移植 591 行"本质是 coding 能力到哪一层。MiMo 已出 L1–L5 分层 + 三态开关方案。
本记录 = QClaw 读源码核实后的增量意见。

## 核实事实（直接读 ~/.hermes/hermes-agent/agent/coding_context.py，591 行）
- INTERACTIVE_CODING_PLATFORMS = {"cli","tui","acp","desktop",""} —— 含空字符串 ""
- _PROJECT_MARKERS + _CONTEXT_FILES + _CODE_EXTENSIONS + _has_code_files（有界扫描防 $HOME 误判）
- _marker_root（≤6 级向上找项目根，跳过 $HOME 和 /tmp）
- CODING_AGENT_GUIDANCE（~67 行英文简报 = L4 核心）
- _detect_profile（off/on/focus/auto 四态）；RuntimeMode（toolset_selection / system_prompt_parts / compact_skill_categories）
- build_coding_workspace_block + detect_project_facts（L5 现场感知）

## 增量发现 1：Vermes 已有 L4 半套（模型家族维度）
- Vermes agent/prompt_builder.py 已有 OPENAI_MODEL_EXECUTION_GUIDANCE(~70 行) + GOOGLE_MODEL_OPERATIONAL_GUIDANCE(~30 行)
- 它们是「模型家族驱动」（gpt/codex/grok 一套、gemini/gemma 一套），已覆盖 plan_then_act/tool_persistence/verification/verify-before-edit
- 上游 CODING_AGENT_GUIDANCE 是「场景驱动」（写代码 vs 不写代码）
- 结论：不是"Vermes 缺 L4"，是"Vermes 的 L4 是模型家族维度，缺场景维度"，两者正交可叠加

## 增量发现 2：空 platform 语义差异
- 上游：platform 空 → 不排除 → 继续 cwd 判断（放行，信任 cwd）
- QClaw A′：空 platform → 不在白名单 → 拒绝降级（fail-safe）
- 对 Vermes 正确：gateway 单进程，空 platform 更可能是漏传而非真的 CLI

## QClaw 核心意见
同意按层买、不做 591 行大移植。真正要拍板的是「默认值 + auto 开哪几层」。

误伤风险排序（硬结论）：
- L1 技能降级 → auto（列表变短，可逆，低风险）✅ 已做 P1/P3
- L2 渠道门 → auto 硬门 ✅ 已做 A′
- L4 编码简报 → on（手动），不挂 auto（人设剧变，web/desktop 日常聊天可能被 cwd 误判，A′ 挡不住 web/desktop）
- L5 现场快照 → 桌面 Workspace 卡片 + Verify 按钮（交互价值 > prompt）
- L3 工具集 focus → 最后，仅手动（砍工具风险最高）

## 排序建议
1. 已做 L1+L2
2. 下一刀：L4 场景版简报做成 on 手动开关（~67 行薄移植，非 591 行）
3. 第二优先：L5 桌面卡片（上游 project_facts_for 本就给 UI 用）
4. 最后：L3 工具集 focus

## 给董董决策
首版三态：off（默认）/ auto（只开 L1+L2）/ on（L1+L2+L4 简报，手动"编码模式"）
默认值才是"像不像 Hermes"的分水岭（上游默认 auto，我们现状 off）。
下一步 = MiMo 出「编码模式」产品规格（模式语义 + M7 迁移 + auto 判定伪代码 + IM 否定测试清单），不是写代码。
