# ScholarForge P2-15~P2-18 修复记录

**时间**: 2026-07-01
**分支**: feature/scholarforge

---

## P2-15: 写作字数提示增加 ✅

**改动文件**: `frontend/src/components/Writer.vue`

- 编辑区底部新增浮动字数进度条（深色毛玻璃背景 `bg-gray-900/80 backdrop-blur`）
- 显示: 当前字数 / 目标字数 + 百分比进度条
- 三段色: <60% 蓝色、60-100% 琥珀色、≥100% 绿色
- 仅当 `project.targetWords > 0` 时显示

---

## P2-16: 文献搜索无 loading 态 ✅

**改动文件**: `frontend/src/components/Writer.vue`

- 新增 `searchLoading` ref (默认 false)
- 两个检索引擎按钮（顶部 + 文献库空态）加 `:disabled` + spinner 动画
- `searchLiterature()` 开头设 `true`，`finally` 设 `false`

---

## P2-17: 文献已引用无标记 ✅

**改动文件**: `frontend/src/components/Writer.vue`

- 新增 `citedPaperIds` computed: 从 `currentContent` 正则提取 `[n]` 格式引用编号，匹配文献列表
- 文献卡片加 `border-l-2 border-l-green-400` 左边框（已有引用时）
- 文献卡片底部加 `✅ 已引用` 绿色 badge

---

## P2-18: 正文 @image#n 占位符未清除 ✅

**改动文件**: `vermes_cli/scholarforge/agents/__init__.py`

- `_clean_citation_format()` 末尾新增正则清理: `@(image|figure|table|chart)#\d+` → 空字符串
- 对所有 Agent 产出生效 (WritingAgent/LiteratureAgent/OutlineAgent 四调用点)

---

## 构建验证

- 前端构建: ✅ 通过 (1.28s)
- 构建产物同步: ✅
- 后端重启: ✅ HTTP 200
