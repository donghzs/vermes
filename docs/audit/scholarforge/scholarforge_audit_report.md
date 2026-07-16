# ScholarForge 前端深度审计报告

**审计时间**: 2026-07-02 19:39 CST  
**审计范围**: 11 个文件，约 3300 行代码  
**审计人**: Subagent (depth 1/1)

---

## 文件审计

---

### Writer.vue (2566 行)

**功能概述**: ScholarForge 主编辑器组件，集成了 Overleaf 双栏预览、大纲导航、AI 写作、文献管理、查重评分等全链路论文写作功能。

**问题清单**:

- **[P0] `citedPaperIds` 正则双重转义错误 — 行 ~1040**  
  `const cited = currentContent.value.match(/\\[(\\d+)\\]/g) || []`  
  在 JS 正则字面量中 `\\[` 匹配的是字面反斜杠加 `[`，而非 `[`。应为 `/\[(\d+)\]/g`。这导致「已引用」文献的绿色标记永远不会生效，`citedPaperIds` 始终为空 Set。  
  **修复**: 改为 `currentContent.value.match(/\[(\d+)\]/g) || []`，后续 `m.replace(/[\\[\\]]/g, '')` 也应改为 `m.replace(/[\[\]]/g, '')`。

- **[P0] `resumePipeline` SSE 解析错误 — 行 ~1370**  
  `const lines = buffer.split('\n')` 用单换行分割，但 SSE 事件以 `\n\n` 分隔。后续 `buffer = ''` 会把不完整行丢弃，导致 `continue_from` 恢复阶段的事件丢失或 JSON 解析失败。  
  **修复**: 改为 `const lines = buffer.split('\n\n')`，`buffer = lines.pop() || ''`，与 `sendToAI` 和 `runStormPipeline` 保持一致。

- **[P1] `doExport` 空函数残留 — 行 ~1430**  
  `const doExport = async () => {}  // Moved to WriterExportModal`  
  模板中导出按钮已绑定 `showExportPanel = true`，但 `doExport` 函数仍残留为空函数。虽然不影响功能，但属于死代码。  
  **修复**: 删除 `doExport` 定义。

- **[P1] `sendToAI` 和 `runStormPipeline` 大量重复代码 — 行 ~1200-1450**  
  两个函数的 SSE 解析逻辑几乎完全相同（outline/content/thinking/citation_replace/citation_verify/done/error 事件处理），约 200 行重复。  
  **修复**: 提取 `handleSSEEvent(evt, assistantMsg)` 公共函数，两个调用方共用。

- **[P1] `selectLiterature` 未使用 — 行 ~1030**  
  `const selectLiterature = (paper) => { selectedLiterature.value = paper }` 定义后从未在模板或代码中调用。`selectedLiterature` ref 也未在模板使用。  
  **修复**: 删除 `selectLiterature` 和 `selectedLiterature`。

- **[P1] `rightPanelWidth` 声明未使用 — 行 ~1075**  
  `const rightPanelWidth = ref(380)` 定义后未在模板或逻辑中使用。面板宽度通过 CSS class `w-80` 固定。  
  **修复**: 删除或实现拖拽调整面板宽度的功能。

- **[P1] `showModelPicker` 和 `modelPickerTarget` 未使用 — 行 ~750**  
  这两个 ref 定义后除了 `onModelSelect` 中引用 `showModelPicker` 外，模板中未使用。模型选择已改为 dropdown 方式。  
  **修复**: 删除 `showModelPicker`、`modelPickerTarget`、`onModelSelect`、`filteredProviders`、`providerSearch`（确认 `filteredProviders` 也未被模板使用）。

- **[P1] `showAICommands` 初始值 — 行 ~1170**  
  `showAICommands` 控制快捷指令面板，但模板中 `showAICommands = !showAICommands` 的 toggle 按钮在 AI 输入框右侧，用户可能不易发现。  
  **建议**: 默认展开快捷指令或加 tooltip 提示。

- **[P2] 组件内 ref 数量过多 (>40 个 ref/reactive/computed)**  
  粗略统计：`currentModel`, `currentProvider`, `events`, `projects`, `project`, `showProjectSwitcher`, `projectSwitcherRef`, `isLoadingProject`, `sectionContents`, `clientId`, `stages`, `activeStage`, `showStageDropdown`, `agentProviders`, `availableProviders`, `showModelPicker`, `modelPickerTarget`, `openAgentDropdown`, `providerSearch`, `outline`, `activeSection`, `editorRef`, `currentContent`, `viewMode`, `showSelectionMenu`, `selectionMenuStyle`, `selectedText`, `inlineEditLoading`, `literature`, `literatureSearch`, `searchLoading`, `selectedLiterature`, `expandedPaper`, `researchDepth`, `showSourceSelector`, `allSearchSources`, `activeSources`, `showPaidSourceConfig`, `paidSourceConfigTarget`, `paidSourceApiKey`, `paidSourceGatewayUrl`, `snapshots`, `snapshotsLoading`, `consensusLoading`, `consensusResults`, `plagLoading`, `plagResult`, `scoreLoading`, `scoreResult`, `aiInput`, `aiStreaming`, `aiAbortController`, `showCheckpointConfirm`, `pendingCheckpoint`, `showAICommands`, `aiMessages`, `leftCollapsed`, `showExportPanel`, `showRewriteModal`, `rewriteTarget`, `editingSectionId`, `editSectionNumber`, `editSectionTitle`, `saveStatus`, `citationIndex`, `citationMaxNum`, `citationResults`, `citationReplaced`, `citationReplacedList` — 约 65+ 个响应式状态。  
  **建议**: 将项目状态、文献状态、AI 状态、评分状态拆分到 Pinia stores 或 composables。

- **[P2] `handleKeyboard` 快捷键冲突 — 行 ~920**  
  `Ctrl+H` 被绑定为标题格式化，但在 macOS 上 `Cmd+H` 是系统级「隐藏窗口」快捷键。`Ctrl+Shift+C` 在某些浏览器中是「开发者工具」的快捷键。  
  **建议**: 对 `Cmd+H` 加 `e.preventDefault()` 已经存在，但考虑在 macOS 上改用其他快捷键或增加提示。

- **[P2] `parsePaperStructure` 正则可优化 — 行 ~980**  
  章节匹配正则 `^(?:#{2,3}\s*|(\d+[\.\d]*)\s+|第[一二三四五六七八九十\d]+章\s*)(.+)` 对于 `1.1.1` 多级编号匹配不够精确，且中文数字章节仅支持到「十」。  
  **建议**: 扩展中文数字匹配范围或使用更鲁棒的模式。

- **[P2] 自动保存防抖 800ms — 行 ~1520**  
  `scheduleAutosave` 使用 800ms 防抖。对于快速切换章节的场景，可能丢失未保存内容（`watch(activeSection)` 切换时未触发保存）。  
  **修复**: 在 `watch(activeSection, ...)` 中先调用 `saveCurrentSection()` 再切换内容。

- **[P2] `copyRichText` 中 HTML 转义顺序 — 行 ~1450**  
  先 `replace(/&/g, '&amp;')` 再 `replace(/</g, '&lt;')` 是正确的，但后续的 markdown 替换会引入新的 `<` 标签，这些标签不会被转义。如果用户内容中包含 `<script>` 标签的 markdown 文本，可能存在 XSS 风险。  
  **建议**: 使用 `DOMPurify.sanitize()` 对最终 HTML 进行清理，与 `renderedContent` 的处理保持一致。

- **[P2] `flushSseSaves` 中 `delete _sseSaveTimers[key]` 在 Promise 数组创建后立即执行 — 行 ~1505**  
  `delete _sseSaveTimers[key]` 在 `push` 之后立即执行，此时 fetch 尚未完成。虽然功能正确（定时器已清除），但如果 fetch 失败，内容会丢失。  
  **建议**: 在 `.catch()` 中添加重试逻辑或错误日志。

**亮点**:
- 异步组件加载 (`defineAsyncComponent`) 减少初始 bundle 体积
- Pinia store 管理面板共享状态，避免 prop drilling
- SSE 流式写作体验完善，支持 abort 和 checkpoint 恢复
- 浮动选择菜单（Jenni AI 风格）交互设计优秀
- 智能粘贴解析论文结构功能实用
- 暗色模式全面支持
- 键盘快捷键覆盖全面（B/I/H/T/K/S/E/Shift+C/Shift+F/Esc）
- 字数进度条可视化反馈清晰
- 事件日志系统提供操作可追踪性

---

### LiteraturePanel.vue (169 行)

**功能概述**: 文献库面板，支持多源检索、BibTeX 导入、付费源配置、文献展开查看摘要和引用。

**问题清单**:

- **[P1] 文献列表无分页 — 行 ~100**  
  `v-for="paper in filteredLiterature"` 直接渲染所有文献，当文献数量 >100 时会有明显的性能问题。  
  **修复**: 添加虚拟滚动或分页（每 20 条加载更多）。

- **[P1] `source-badge-class` emit 声明但未使用 — 行 ~55**  
  emits 数组中包含 `'source-badge-class'`，但组件内已定义了 `sourceBadgeClass` 函数，从未 emit 此事件。  
  **修复**: 从 emits 中移除 `'source-badge-class'`。

- **[P2] 付费源配置弹窗 z-index 不够 — 行 ~70**  
  `fixed inset-0 z-50` 的弹窗在 Writer.vue 中可能被 `z-[60]` 的 Agent dropdown 覆盖。  
  **修复**: 提升到 `z-[100]` 或使用 Teleport。

- **[P2] 搜索输入框无防抖 — 行 ~90**  
  `localLiteratureSearch` 通过 watch 实时同步到父组件，每次按键都触发 `filteredLiterature` 重算。  
  **建议**: 在 Writer.vue 的 `filteredLiterature` computed 中加防抖，或在 LiteraturePanel 中使用 `watchDebounced`。

- **[P2] 空状态引导文案可以更明确 — 行 ~95**  
  当前文案「运行「文献综述」Agent 或点击上方检索按钮」未说明如何运行 Agent。  
  **建议**: 添加链接或按钮直接跳转到 AI 面板。

**亮点**:
- 付费源配置流程完整，支持 API Key + 网关 URL
- 搜索源选择器显示在线/离线状态
- BibTeX 展开预览带语法高亮和复制按钮
- 已引用文献用绿色左边框标记（虽然 `citedPaperIds` 有 bug）

---

### PlagPanel.vue (112 行)

**功能概述**: 查重 + AIGC 检测结果展示面板，包含重复率环形图、AIGC 痕迹条、重复片段和 AI 段落列表。

**问题清单**:

- **[P2] 重复率环形图 stroke-dasharray 硬编码 151 — 行 ~25**  
  `:stroke-dasharray="Math.round(result.overall_similarity * 151) + ' 151'"` 中 `151` 是 `2 * π * 24` 的近似值，但精确值应为 `150.8`。  
  **修复**: 使用 `(2 * Math.PI * 24).toFixed(1)` 计算周长。

- **[P2] 无结果重试引导 — 行 ~95**  
  空状态仅显示「尚未检测」，未引导用户先写作再检测。  
  **建议**: 添加「请先完成论文写作」的提示文案。

- **[P2] 重复段落 `line-clamp-3` 未定义 — 行 ~75**  
  使用了 `line-clamp-3` class 但 PlagPanel 没有 scoped style 定义，依赖 Tailwind 的 `line-clamp-3` 工具类（Tailwind 3.3+ 内置）。  
  **确认**: 如果 Tailwind 版本 < 3.3，需要安装 `@tailwindcss/line-clamp` 插件。

**亮点**:
- 综合评分卡片设计清晰，重复率和 AIGC 分开展示
- 颜色分级（绿/黄/红）直观
- 改进建议区块提供可操作反馈
- 暗色模式适配完整

---

### ScorePanel.vue (83 行)

**功能概述**: 论文评分面板，展示三维度（原创性/逻辑性/引用完整性）评分和综合分数。

**问题清单**:

- **[P2] fallback 模式提示 — 行 ~22**  
  `result._is_fallback` 时显示「以下为启发式估算」，但未提供「配置 API Key」的操作按钮。  
  **建议**: 添加「前往配置」按钮跳转到设置页。

- **[P2] 评分环形图周长硬编码 176 — 行 ~35**  
  `:stroke-dasharray="Math.round(result.overall / 10 * 176) + ' 176'"` 中 `176` 是 `2 * π * 28` 的近似值（精确值 `175.93`）。  
  **修复**: 使用 `(2 * Math.PI * 28).toFixed(1)` 计算周长。

**亮点**:
- 三维度评分展示清晰，带进度条和评分理由
- 底部展示权重公式，增加透明度
- fallback 模式提示用户配置 API Key
- 暗色模式完整支持

---

### ConsensusPanel.vue (80 行)

**功能概述**: 共识度分析面板，展示论文论断在文献中的支持/反对/中立分布。

**问题清单**:

- **[P1] `_expanded` 直接修改 prop 对象 — 行 ~65**  
  `@click="r._expanded = !r._expanded"` 直接修改了 `results` 数组中对象的属性。虽然 Vue 3 的 reactive props 允许这种操作（因为修改的是嵌套对象而非 prop 本身），但违反了单向数据流原则。  
  **修复**: 使用 emit 通知父组件切换展开状态，或在本地维护一个展开状态 Map。

- **[P2] 无 loading 骨架屏 — 行 ~20**  
  `loading` prop 仅控制按钮 spinner，分析过程中面板内容区域无变化。  
  **建议**: 添加 loading 骨架屏或「分析中...」占位文案。

**亮点**:
- 支持/中立/反对三色堆叠柱状图直观
- 置信度分级（高/中/低共识）清晰
- 逐文献详情可展开，信息层次合理
- 空状态引导文案明确

---

### CitationPanel.vue (65 行)

**功能概述**: 引用核查结果面板，展示每个引用的评分、理由和匹配的真实文献。

**问题清单**:

- **[P1] 无「重新核查」按钮 — 行 ~10**  
  面板只有关闭按钮，用户如果想重新核查引用需要运行「润色」Agent，不够直观。  
  **修复**: 添加 `@run` emit 和对应按钮，调用后端重新核查。

- **[P2] `replacedList.slice(0, 5)` 硬编码截断 — 行 ~30**  
  只展示前 5 篇匹配文献，用户无法查看完整列表。  
  **修复**: 添加「查看全部」按钮或使用可展开列表。

**亮点**:
- 绿/黄/红三色评分卡片直观
- 真实文献匹配状态展示增加可信度
- 错误/警告计数在 header 实时显示
- 空状态文案引导清晰

---

### AIPanel.vue (64 行)

**功能概述**: AI 写作助手面板，展示对话历史、研究深度选择器和快捷操作。

**问题清单**:

- **[P1] 对话消息无滚动到底部 — 行 ~15**  
  `v-for="(msg, idx) in messages"` 渲染的消息列表没有自动滚动到底部的逻辑。新消息生成时用户看不到最新内容。  
  **修复**: 在 Writer.vue 中 watch `aiMessages.length` 后 `nextTick(() => scrollChatToBottom())`，或在 AIPanel 中使用 `ref` + `scrollIntoView`。

- **[P2] 研究深度选择器缺少描述 — 行 ~35**  
  `researchDepths` prop 传入的 `depthOptions` 包含 `desc` 字段，但模板中只显示 `d.label`。  
  **修复**: 添加 `title="d.desc"` 属性展示描述。

- **[P2] `close` emit 声明但未使用 — 行 ~45**  
  `defineEmits` 包含 `'close'`，但模板中没有关闭按钮触发 `$emit('close')`。  
  **修复**: 添加关闭按钮或从 emits 中移除 `'close'`。

- **[P2] AI 消息内容未渲染 Markdown — 行 ~25**  
  `{{ msg.content }}` 使用文本插值，AI 回复中的 Markdown 格式（如代码块、列表、加粗）不会渲染。  
  **建议**: 使用 `v-html` + `DOMPurify.sanitize(md.render(msg.content))` 渲染 AI 消息。

**亮点**:
- 全链路写作按钮使用渐变色突出
- 快捷操作网格布局紧凑
- 研究深度三档选择器设计简洁
- 在线状态指示器增加信任感

---

### SnapshotsPanel.vue (48 行)

**功能概述**: 版本历史面板，创建/恢复/删除快照。

**问题清单**:

- **[P2] 恢复快照无 loading 状态 — 行 ~30**  
  `@click="$emit('restore', snap)"` 恢复操作期间无 loading 指示。  
  **修复**: 添加 `restoring` 状态 ref，在 Writer.vue 的 `restoreSnapshot` 中设置。

- **[P2] 快照列表无创建中状态 — 行 ~25**  
  点击「💾 存快照」后列表无即时反馈。  
  **建议**: 添加 optimistic update 或 loading placeholder。

- **[P2] `formatTime` 函数重复定义 — 行 ~40**  
  `formatTime` 在 Writer.vue、AIPanel.vue、SnapshotsPanel.vue 中均有定义。  
  **修复**: 提取到 `composables/useFormat.js` 或 utils 文件。

**亮点**:
- 界面简洁，功能明确
- 快照大小和创建时间展示清晰
- 空状态引导文案到位

---

### WriterExportModal.vue (141 行)

**功能概述**: 导出弹窗，支持 PDF/LaTeX/Word/Markdown/BibTeX 五种格式和 15 个 LaTeX 模板。

**问题清单**:

- **[P1] `exportFormats` 数组有 5 项但 grid 是 3 列 — 行 ~12, ~95**  
  `grid-cols-3` 的网格中放 5 个格式按钮，最后一行只有 2 个，布局不对称。  
  **修复**: 改为 `grid-cols-5` 或 `grid-cols-2`，或减少到 4/6 个格式选项。

- **[P1] 导出错误处理使用 `alert()` — 行 ~115**  
  `alert(err.detail || '导出失败')` 在 Electron 环境中可能被阻塞或样式不统一。  
  **修复**: 使用 toast 通知或自定义错误弹窗组件。

- **[P2] `filename` computed 无法手动修改 — 行 ~75**  
  `filename` 是 computed，用户无法自定义文件名。虽然模板中有 `<input v-model="filename">`，但 computed 是只读的，修改会报错。  
  **修复**: 改为 `ref` + `watch(() => props.projectTitle)` 初始化。

- **[P2] `export-start` emit 未在父组件处理 — 行 ~80**  
  `emit('export-start', { format: fmt })` 发出事件但 Writer.vue 中 `<WriterExportModal>` 未监听 `@export-start`。  
  **修复**: 在 Writer.vue 中添加 `@export-start` 处理或移除该 emit。

- **[P2] LaTeX 模板列表硬编码 — 行 ~85**  
  15 个模板硬编码在前端，无法动态更新。  
  **建议**: 从后端 API 加载模板列表。

**亮点**:
- 支持国际期刊/会议 + 国内期刊模板
- 导出前自动保存当前章节
- Blob 下载处理完善
- LaTeX 模板分类展示（期刊/会议/中文）

---

### WriterRewriteModal.vue (119 行)

**功能概述**: 章节重写弹窗，支持润色/扩写/精简/重组/加数据/学术化/通俗化 7 种模式。

**问题清单**:

- **[P1] `REWRITE_MODES` 有 7 项但 grid 是 4 列 — 行 ~15, ~70**  
  `grid-cols-4` 的网格中放 7 个模式按钮，第二行只有 3 个，布局不对称。  
  **修复**: 改为 `grid-cols-3`（3+3+1）或添加第 8 个模式使 4+4 对称。

- **[P2] 流式响应错误处理不完善 — 行 ~100**  
  `catch (e) { if (e.message && !e.message.includes('JSON')) throw e }` 会吞掉 JSON 解析错误，但某些情况下可能隐藏真正的问题。  
  **修复**: 添加 `console.warn` 日志记录被忽略的错误。

- **[P2] 重写完成后未刷新字数统计 — 行 ~95**  
  `emit('rewrite-done', { sectionKey, text: evt.text })` 触发父组件更新内容，但未显式触发字数统计更新。  
  **确认**: 父组件 `onRewriteDone` 中已更新 `section.wordCount`，此问题不严重。

- **[P2] `loading` 状态为本地 ref — 行 ~30**  
  如果用户在重写过程中关闭弹窗，`loading` 不会通知父组件。  
  **建议**: 通过 emit 或 prop 同步 loading 状态。

**亮点**:
- 7 种重写模式覆盖常见学术写作场景
- SSE 流式响应实时展示
- 额外要求输入框支持自定义指令
- 防重复提交（loading 禁用按钮）

---

### scholar-panel.js (26 行)

**功能概述**: Pinia store 管理右侧面板共享状态（activeRightPanel、showLiteraturePanel、showAIPanel 等）。

**问题清单**:

- **[P2] `togglePanel` 和 `toggleRightBar` 未在 Writer.vue 中使用 — 行 ~15, ~20**  
  store 定义了 `togglePanel` 和 `toggleRightBar` actions，但 Writer.vue 中直接通过 `panelStore.togglePanel(name)` 调用了一次，`toggleRightBar` 则通过 `panelStore.toggleRightBar()` 调用。实际上 Writer.vue 中 `toggleRightPanel` 函数包装了 `panelStore.togglePanel`，而 `toggleRightBar` 直接调用 store action。这部分设计可以简化。  
  **建议**: 统一使用 store actions，去除 Writer.vue 中的包装函数。

- **[P2] store 状态过少 — 行 ~5**  
  仅管理 5 个状态，但 Writer.vue 中仍有大量面板相关状态（如 `showExportPanel`、`showRewriteModal`、`showStageDropdown` 等）未纳入 store。  
  **建议**: 将更多共享状态纳入 store，特别是需要在子组件中访问的状态。

**亮点**:
- Pinia store 使用 Options API 风格，清晰易读
- 面板互斥逻辑通过 `togglePanel` 封装
- 作为面板间通信的中心化方案

---

## 汇总表

| 严重度 | 文件 | 问题 | 修复建议 | 工作量 |
|--------|------|------|----------|--------|
| P0 | Writer.vue:~1040 | `citedPaperIds` 正则双重转义，已引用标记永远不生效 | 修正正则 `/\[(\d+)\]/g` | 5min |
| P0 | Writer.vue:~1370 | `resumePipeline` SSE 用单换行分割，事件丢失 | 改为 `\n\n` 分割 | 5min |
| P1 | Writer.vue:~1430 | `doExport` 空函数残留 | 删除 | 1min |
| P1 | Writer.vue:~1200 | `sendToAI` 和 `runStormPipeline` 200+ 行重复 | 提取公共 `handleSSEEvent` | 1h |
| P1 | Writer.vue:~1030 | `selectLiterature` 和 `selectedLiterature` 死代码 | 删除 | 2min |
| P1 | Writer.vue:~1075 | `rightPanelWidth` 未使用 | 删除或实现拖拽 | 1min/2h |
| P1 | Writer.vue:~750 | `showModelPicker`/`modelPickerTarget`/`onModelSelect` 等死代码 | 删除 | 5min |
| P1 | Writer.vue:~1520 | 切换章节时未触发保存，可能丢内容 | watch 中先 save 再切换 | 10min |
| P1 | Writer.vue:~1450 | `copyRichText` 未用 DOMPurify，潜在 XSS | 添加 sanitize | 5min |
| P1 | LiteraturePanel.vue:~100 | 文献列表无分页，大列表性能差 | 虚拟滚动或分页 | 2h |
| P1 | LiteraturePanel.vue:~55 | `source-badge-class` emit 声明未使用 | 从 emits 移除 | 1min |
| P1 | ConsensusPanel.vue:~65 | 直接修改 prop 对象 `_expanded` | 本地维护展开状态 | 15min |
| P1 | CitationPanel.vue:~10 | 无「重新核查」按钮 | 添加 @run emit | 10min |
| P1 | AIPanel.vue:~15 | 对话消息无自动滚动到底部 | watch + scrollIntoView | 15min |
| P1 | WriterExportModal.vue:~12 | 5 项格式在 3 列网格中布局不对称 | 改 grid-cols-5 | 2min |
| P1 | WriterExportModal.vue:~115 | 导出错误用 `alert()`，Electron 体验差 | 改用 toast | 30min |
| P1 | WriterExportModal.vue:~75 | `filename` 为 computed 但需可编辑 | 改为 ref + watch | 5min |
| P1 | WriterRewriteModal.vue:~15 | 7 项模式在 4 列网格中布局不对称 | 改 grid-cols-3 | 2min |
| P2 | Writer.vue:~920 | `Ctrl+H` 与 macOS 系统快捷键冲突 | 加提示或改键 | 15min |
| P2 | Writer.vue:~980 | `parsePaperStructure` 中文数字仅支持到「十」 | 扩展正则 | 15min |
| P2 | Writer.vue:~65 | 65+ 个响应式状态，应拆分到 composables | 提取 useProject/useLiterature/useAI | 4h |
| P2 | PlagPanel.vue:~25 | 环形图 stroke-dasharray 硬编码 | 用 `2πr` 计算 | 5min |
| P2 | ScorePanel.vue:~35 | 同上 | 同上 | 5min |
| P2 | ScorePanel.vue:~22 | fallback 模式无操作引导 | 添加配置按钮 | 10min |
| P2 | AIPanel.vue:~25 | AI 消息未渲染 Markdown | v-html + sanitize | 15min |
| P2 | AIPanel.vue:~45 | `close` emit 声明未使用 | 移除或添加关闭按钮 | 2min |
| P2 | SnapshotsPanel.vue:~30 | 恢复快照无 loading 状态 | 添加 restoring ref | 10min |
| P2 | SnapshotsPanel.vue:~40 | `formatTime` 重复定义 | 提取到 utils | 10min |
| P2 | WriterExportModal.vue:~80 | `export-start` emit 未被父组件监听 | 添加监听或移除 | 5min |
| P2 | WriterExportModal.vue:~85 | LaTeX 模板硬编码 | 从 API 加载 | 1h |
| P2 | WriterRewriteModal.vue:~100 | JSON 解析错误被静默吞掉 | 添加 console.warn | 2min |
| P2 | scholar-panel.js:~5 | store 状态过少，多个共享状态未纳入 | 扩展 store | 1h |
| P2 | LiteraturePanel.vue:~90 | 搜索输入无防抖 | 加 watchDebounced | 10min |
| P2 | ConsensusPanel.vue:~20 | 无 loading 骨架屏 | 添加占位 | 15min |
| P2 | CitationPanel.vue:~30 | `replacedList.slice(0,5)` 硬编码截断 | 查看全部按钮 | 15min |

---

## 架构评估

### 组件拆分
Writer.vue 2566 行仍然过大。建议进一步拆分：
1. **ProjectHeader.vue** — 顶部导航栏（项目切换器 + 阶段选择 + 工具栏），约 250 行模板
2. **OutlineSidebar.vue** — 左栏大纲导航，约 100 行模板
3. **EditorToolbar.vue** — 编辑器工具栏（格式化按钮 + 视图切换），约 50 行模板
4. **AIBottomBar.vue** — 底部 AI 输入栏，约 80 行模板
5. **useSSEStream.js** — SSE 流式处理 composable，约 300 行逻辑
6. **useAutosave.js** — 自动保存 composable，约 50 行逻辑
7. **usePaperStructure.js** — 论文结构解析 composable，约 150 行逻辑

### Pinia Store
当前 `scholar-panel.js` 仅管理 5 个状态，建议扩展为：
- `scholar-project.js` — 项目状态（projects, project, outline, sectionContents）
- `scholar-literature.js` — 文献状态（literature, searchLoading, activeSources）
- `scholar-ai.js` — AI 状态（aiMessages, aiStreaming, aiInput）
- `scholar-panel.js` — 面板 UI 状态（保持现有）

### 事件流
当前事件流基本清晰：
- 子面板 → emit → Writer.vue 处理
- Writer.vue → props → 子面板
- 共享状态 → Pinia store

但 Writer.vue 作为「上帝组件」承担了过多协调职责，部分 emit 处理函数超过 50 行。

---

## 总体评价

ScholarForge 前端整体质量**中上**。功能覆盖论文写作全链路（选题→文献→大纲→写作→查重→评分→导出），交互设计参考了 Jenni AI、Overleaf 等成熟产品，暗色模式和响应式布局基本完善。

**最紧急的 2 个 P0 问题**（正则双重转义 + SSE 解析错误）会导致核心功能失效，应立即修复。

**最大的架构债务**是 Writer.vue 的 2566 行巨型组件，建议通过 composables 拆分响应式逻辑，通过子组件拆分模板。
