# ScholarForge M1-M3 审计修复

**时间**：2026-06-30 17:35-17:45
**提交**：bcb7196e8
**分支**：feature/scholarforge

## 修复内容

### M1: 凭证去重收尾 ✅
- blueprint.py 3 处残留 yaml/.env 手工解析 → 全部改用 `_load_vermes_config()` 公共入口
  - `_resolve_credentials()` 内的自动检测分支（~20 行 → 3 行）
  - `_get_model_info()` 内的 config.yaml 读取（~7 行 → 4 行）
  - `list_available_providers()` 整个函数（~15 行 → 3 行）
- storm_adapter.py `__main__` 测试块 1 处（~3 行 → 2 行）
- **净减 30 行重复代码**

### M2: 报告知网策略描述更正 ✅
- 审计报告中"meta→gateway→sci-hub"更正为"gateway→万方→OpenAlex"
- 无 sci-hub，功能本身正常

### M3: DOMPurify XSS 防护 ✅
- Writer.vue `v-html="renderedContent"` 加 `DOMPurify.sanitize()` 消毒
- dompurify ^3.4.7 已在 package.json 中，直接 import 使用
- 构建通过，93.52 kB（+0.04 kB）

## 验证
- 前端构建 ✅
- 后端测试 63/64 通过（1 失败为 PermissionError 模拟问题，与本次修改无关）
- 安全检查 ✅
- Python 语法检查 ✅
