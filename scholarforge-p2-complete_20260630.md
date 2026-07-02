# ScholarForge P2 补齐完成

**时间**：2026-06-30 14:40
**分支**：feature/scholarforge
**提交**：bac0d0902

## 四项补齐

### P2-1: STORM 引擎依赖 ✅
- `pip install dspy-ai knowledge-storm` → 安装成功
- `storm_adapter.py` 的 `import dspy` + `import knowledge_storm` 不再失败

### P2-2: 6 免费源 429 冷却全覆盖 ✅
在 `search/__init__.py` 新增全局冷却机制：
- `_source_cooldowns` 字典记录每源冷却到何时
- 每个 free_source 搜索函数开头调 `_is_cooled_down()` 跳过
- 429 响应时调 `_set_cooldown()` 设 5 分钟冷却
- 原仅 semantic_scholar 有冷却，现 7 源全覆盖

### P2-3: 凭证解析三合一去重 ✅
- `__init__.py` 新增 `_load_vermes_config()` 公共入口 (~/.vermes/config.yaml + .env)
- `blueprint.py` 两处 `_resolve_credentials()` + `_list_configured_providers()` 改用共用入口
- `tools.py` `_resolve_credentials()` 改用共用入口
- 消除 ~60 行重复解析逻辑

### P2-4: PDF 导出修复 ✅
- `brew install gobject-introspection` → weasyprint 系统依赖
- `ln -sf libgobject-2.0-0.dylib` → cffi dlopen 兼容
- `pip install markdown-it-py` → _markdown_to_html() 依赖
- `export/full.py` export_pdf() 自动 setdefault DYLD_FALLBACK_LIBRARY_PATH
- 实测生成 26KB PDF ✅

## 改动文件
- `hermes_cli/scholarforge/__init__.py` — 新增 _load_vermes_config()
- `hermes_cli/scholarforge/search/__init__.py` — 429 冷却全覆盖
- `hermes_cli/scholarforge/blueprint.py` — 凭证解析去重
- `hermes_cli/scholarforge/tools.py` — 凭证解析去重
- `hermes_cli/scholarforge/export/full.py` — PDF 导出修复 (新文件)
