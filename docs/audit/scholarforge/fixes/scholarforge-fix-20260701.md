# ScholarForge 7月1日修复记录

## 修复内容

### Bug 1: 文献引用格式错误（P0）
**现象**: LLM 生成论文时使用 `[@金旭杨2025]`、`[金旭杨2025]`、`(Autor, 2020)` 等非法引用格式，而非 `[1][2]` 纯数字格式
**根因**: 即使 prompt 明确要求 `[n]` 格式，DeepSeek/openai 模型有时仍会生成 `[@Author]` 格式
**修复**:
1. 新增 `_clean_citation_format()` 函数（正则清理）：
   - `[@中文名年份]` → 删除
   - `[非数字+年份]` → 删除
   - `(作者, 2020)` → 删除
   - `[Author, 2020]` → 删除
2. 在所有 LLM 输出点调用清理：TopicAgent、LiteratureAgent (depth=1/2/3)、WritingAgent (per-section)、OutlineAgent
3. 强化 prompt 用"铁律"措辞 + 正确/错误示例对比

**影响范围**: `agents/__init__.py` - `_clean_citation_format()` 新增 + 5处调用

### 此前修复（本次会话中）:
- 右侧面板交互改造（activeRightPanel 状态管理）
- 导出内容为空（WritingAgent _assemble_full_paper + DB section 持久化）
- 论文结构残缺（摘要/关键词/结论/参考文献）
- OutlineAgent 误过滤摘要/关键词
- 大纲未持久化到 DB

## 构建状态
- 后端：✅ 200 /api/scholar/agents
- 测试：63 passed, 1 pre-existing fail (PermissionError mock)
- 前端：构建通过 (1.27s)

## 验证方法
```python
from vermes_cli.scholarforge.agents import _clean_citation_format
test = "[@金旭杨2025] 提出了方法。"
assert _clean_citation_format(test) == "提出了方法。"
```
