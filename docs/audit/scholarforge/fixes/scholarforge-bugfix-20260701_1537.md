# ScholarForge Bug 修复记录 — 2026-07-01

## 问题 1: `@image#n` 占位符未被清理 ✅

**根因**: `_clean_citation_format()` 只在部分 Agent 输出路径调用，遗漏了多处 `yield {"type": "content", ...}`

**修复**: `hermes_cli/scholarforge/agents/__init__.py`
- Line 434: 第1轮领域概览输出
- Line 519: 第3轮研究空白识别输出  
- Line 919: WritingAgent 逐节输出
- Line 973: RefinementAgent 完整论文输出
- Line 1136: RefinementAgent 润色完成提示

全部加上 `_clean_citation_format()` 包装。

## 问题 2: 论文结构识别失败 + 参考文献带 # 号 ✅

**根因**: 
1. 参考文献节正则 `^#{1,3}\s*参[考考]文[献献]` 字符类重复（`[考考]` 应为 `[考献]`）
2. 参考文献条目匹配太严格（要求行首 `[n]`，不允许前导空格）
3. 解析逻辑简单按点号分割，无法处理复杂标题

**修复**: `frontend/src/components/Writer.vue` `parsePaperStructure()`
- 修正正则：`/^#{1,3}\s*参考文献/`（移除重复字符类）
- 放宽条目匹配：`/^\s*\[(\d+)\]\s*(.+)/`（允许前导空格）
- 改进解析：更鲁棒的作者/标题/年份/期刊提取

## 构建验证

- 前端构建: ✅ 通过 (1.31s)
- 后端重启: ✅ HTTP 200

## 待验证

- [ ] `@image#n` 测试不再弹出图片内容
- [ ] 粘贴含参考文献的论文能正确识别结构
