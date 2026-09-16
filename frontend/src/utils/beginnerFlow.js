/**
 * 工具箱「新手模式」的写作流程定义（2026-09-17 非技术用户上手专项）。
 *
 * 为什么要有它：28 个论文工具平铺给非技术用户是压倒性的 —— 他不知道先点哪个。
 * 新手模式只给一条路，每步配一句白话提示。
 *
 * 设计纪律：
 *  - hint 一律用**写作动作**描述，不用工具口径描述（「存成卡片」而非「保存文献卡片」）；
 *  - hint 里**禁止**出现 project_id / section_key / cohens_d 这类字段名
 *    —— 第一次来的人看到字段名只会更困惑；
 *  - 抽成独立模块是为了可单测，也让后端守卫测试（`test_tool_labels.py`）能静态扫描。
 */

import { toolLabel } from './toolLabels'

/** 写论文的顺序（组内顺序即展示顺序） */
export const BEGINNER_FLOW = [
  { name: 'scholarforge_search', hint: '先找和你题目相关的文献' },
  { name: 'scholarforge_save_literature_cards', hint: '把有用的文献存成卡片，写引用时直接取' },
  { name: 'scholarforge_outline', hint: '定章节结构，先有骨架再写正文' },
  { name: 'scholarforge_write', hint: '按大纲写一章' },
  { name: 'scholarforge_replace_citations', hint: '把正文里占位的 [1] 换成真实文献' },
  { name: 'scholarforge_polish', hint: '改语句，让它更像论文' },
  { name: 'scholarforge_score', hint: '看看这一章能得多少分、哪里还要改' },
  { name: 'scholarforge_export', hint: '导出成 Word，拿去交给导师' },
]

/**
 * 「不知道从哪下手」时的入口。它覆盖整条流程，故单列为 CTA 而不是第 1 步 ——
 * 放第 1 步会让人以为必须先跑它。
 */
export const BEGINNER_ENTRY = 'scholarforge_run_pipeline'

/** 新手模式下会出现的全部工具名（流程 8 步 + 入口 CTA） */
export const BEGINNER_NAMES = new Set([
  ...BEGINNER_FLOW.map((s) => s.name),
  BEGINNER_ENTRY,
])

/**
 * 工具搜索匹配（两种展示模式共用）。
 *
 * 🔴 必须匹配中文名：用户只会搜「三线表」「查重」，不会搜 stats_table。
 *    只匹配 name/description 的话，中文名配了也等于搜不到。
 *
 * @param {{name: string, description?: string}} t 工具对象
 * @param {string} kw 已 lower 的关键字（空串表示不过滤）
 */
export function matchTool(t, kw) {
  if (!kw) return true
  return (
    t.name.toLowerCase().includes(kw) ||
    (t.description || '').toLowerCase().includes(kw) ||
    // 🔴 不能用 `t.label || t.name` —— 工具对象上**没有** label 字段，
    //    那样会退回匹配英文标识符，中文名等于白配。必须走 toolLabel。
    toolLabel(t.name).toLowerCase().includes(kw)
  )
}
