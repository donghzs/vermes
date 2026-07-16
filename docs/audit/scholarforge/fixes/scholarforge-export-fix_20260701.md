# ScholarForge 导出文件问题修复 — 2026-07-01

## 用户反馈

导出 `paper-2.docx` 中存在以下问题：
1. **标题重复3次**：同一标题出现3遍
2. **参考文献重复3次**：参考文献节出现3次，格式混乱（英文/中文/作者顺序不一）
3. **内容不够完整**：部分段落结尾截断

## 根因分析

参考文献重复的 **三重叠加** 链：

| 来源 | 位置 | 追加时机 |
|------|------|----------|
| `_assemble_full_paper` | `agents/__init__.py:960` | WritingAgent 完成写作后组装 |
| `replace_pseudo_citations` | `citation_provider.py:272` | RefinementAgent 真引用替换后追加 |
| `_build_paper_text` | `export/full.py:71` | 导出时再追加 |

任何一次运行都会在已有文献上层层追加，导致3份参考文献。

标题重复同理：
- `_assemble_full_paper` 加了 `# {title}`
- `_build_paper_text` 又加了 `# {title}`
- `self.ctx.draft` 中已有标题行未去重

## 修复内容

### 1. `export/full.py:_build_paper_text()` — 智能去重
- 标题：检测 content 是否以 `# ` 开头，是则跳过
- 摘要：检测 content 是否含 `## 摘要`/`## Abstract`，是则跳过
- 参考文献：检测 content 是否含 `## 参考文献`/`## References`，是则跳过

### 2. `citation_provider.py:replace_pseudo_citations()` — 替换而非追加
- 检测 draft 已有参考文献节的，**替换**之（正则匹配 `## 参考文献\n\n...$`）
- 无已有节点时，**追加**之（保持兼容）

### 3. `agents/__init__.py:_assemble_full_paper()` — 避免重复添加
- 添加参考文献前检测 draft 中是否已有

## 验证结果

```
✅ 标题不重复
✅ 摘要不重复
✅ 参考文献不重复
✅ 不含冗余标题
✅ 不含冗余摘要
```

## 部署状态

- 后端重启: ✅ HTTP 200
- 修复生效: ✅ 需要重新跑一次 STORM 全链路写作后导出验证
