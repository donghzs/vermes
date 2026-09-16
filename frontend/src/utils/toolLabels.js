/**
 * 工具名 → 中文显示名（单一真源）
 *
 * 背景：工具的内部 name（如 `scholarforge_check_stats`）是给 LLM function-calling 和
 * 后端调度用的**标识符**，不能随便改；但直接把它显示给用户，对非技术用户就是天书。
 * 所以显示层统一走这里做一次映射。
 *
 * 使用处：
 *   - `components/MessageList.vue` —— 对话里工具调用的胶囊标签
 *   - `components/scholar/ToolBox.vue` —— 论文工具箱的卡片标题与搜索
 *
 * 🔴 维护纪律：新增工具必须同步加中文名。这不是靠自觉——
 *    `vermes_cli/scholarforge/tests/test_tool_labels.py` 会枚举注册表里的**真实工具**，
 *    逐个来这个文件里比对，漏配/拼写错/残留废弃键都会让测试挂掉。
 *    同理，改了中文名而后端没同步，也会被那条测试抓到。
 */

/** 通用工具（对话里最常出现的那批） */
const GENERAL_LABELS = {
  read_file: '读取文件',
  write_file: '写入文件',
  search_files: '搜索文件',
  terminal: '终端',
  web_search: '网页搜索',
  vision_analyze: '图片分析',
  list_directory: '列出目录',
  edit_file: '编辑文件',
  memory: '记忆',
  execute_command: '执行命令',
  google_search: '搜索',
  browse_url: '浏览网页',
  browser_navigate: '浏览网页',
  browser_click: '点击页面',
  browser_type: '输入文本',
  browser_snapshot: '截取页面',
  browser_console: '控制台',
  lsp_completion: '代码补全',
  lsp_diagnose: '诊断代码',
  code_execution: '执行代码',
}

/**
 * 论文工具（scholarforge，共 28 个）
 *
 * 取名原则：**说人话、说清"我能拿它干什么"**，而不是直译英文标识符。
 * 目标读者是写论文的非技术用户（例如在职读研的教师），
 * 所以宁可「统计转三线表」这样带宾语，也不要「统计表」这种半吊子简称。
 */
const SCHOLARFORGE_LABELS = {
  // ── 写作主链 ──
  scholarforge_search: '找文献',
  scholarforge_outline: '生成大纲',
  scholarforge_write: '写章节',
  scholarforge_polish: '润色',
  scholarforge_score: '论文评分',
  // ── 引用与文献 ──
  scholarforge_replace_citations: '补真实引用',
  scholarforge_format_refs: '规范参考文献',
  scholarforge_verify_citations: '验引用真假',
  scholarforge_save_literature_cards: '存文献卡片',
  scholarforge_literature_matrix: '做综述矩阵',
  scholarforge_research_map: '拆解选题',
  // ── 质量检查 ──
  scholarforge_plagiarism_check: '查重',
  scholarforge_deaigc: '去除 AI 味',
  scholarforge_quality_gate: '全文体检',
  scholarforge_detect_design_flaws: '查研究设计',
  scholarforge_review_claims: '审论点与证据',
  scholarforge_review: '审稿',
  // ── 统计与表格 ──
  scholarforge_stats_table: '统计转三线表',
  scholarforge_check_stats: '核统计数据',
  // ── 项目与导出 ──
  scholarforge_export: '导出文件',
  scholarforge_manage_snapshots: '版本快照',
  scholarforge_apply_template: '套用模板',
  scholarforge_list_projects: '我的论文项目',
  scholarforge_set_active_project: '切换当前论文',
  scholarforge_read_section: '查看章节',
  // ── 其它 ──
  scholarforge_learn_style: '学我的文风',
  scholarforge_citation_graph: '引用关系图',
  scholarforge_run_pipeline: '一条龙写作',
}

export const TOOL_NAME_MAP = { ...GENERAL_LABELS, ...SCHOLARFORGE_LABELS }

/**
 * 取工具的中文显示名。没有配过就退回原始 name（宁可露英文，也不要显示空白或 undefined）。
 * @param {string} name 工具内部标识符
 * @returns {string}
 */
export function toolLabel(name) {
  if (!name) return ''
  return TOOL_NAME_MAP[name] || name
}
