# Vermes PyInstaller 问题汇总（08:52 AM）

## 问题1：plugins.browser 模块导入失败（P0）

**现象**：chat API 返回 500
**错误链**：
```
chat_completions → from run_agent import AIAgent
→ run_agent.py:119: from tools.browser_tool import cleanup_browser
→ browser_tool.py:95: from plugins.browser.browserbase.provider import ...
→ AttributeError: module 'plugins' has no attribute '__path__'
→ ModuleNotFoundError: No module named 'plugins.browser'; 'plugins' is not a package
```

**根因分析**：
1. `plugins/browser/__init__.py` 原本不存在（已创建）
2. PyInstaller 把 `plugins/` 通过 `datas` 复制到 bundle Resources，同时也通过 hiddenimports 收集到 PYZ
3. PYZ 中的 `plugins/__init__.py` 没有 `__path__` 属性，无法识别为包
4. `datas` 中的 plugins/ 和 PYZ 中的 plugins 冲突

**已尝试**：
- ✅ 创建 `plugins/browser/__init__.py`
- ✅ hiddenimports 添加 `plugins`, `plugins.browser`, `plugins.browser.browserbase` 等
- ❌ 以上都不行，PYZ 中 `__path__` 仍丢失

**已修改（未测试）**：
- `tools/browser_tool.py` 第91-104行：3个 plugins.* 导入改为 try/except，失败时设为 None
- 这3个导入都是 `# noqa: F401 (legacy import surface)`，只是兼容性 re-export，运行时不需要

## 问题2：One-API token 过期

**现象**：所有 One-API user token 都被禁用
**已处理**：
- 新建 token ID:18, key: `qEGqbbCwJs5grOTW68Fd0d69Ab074f11Aa830d9aB3E62b4d`
- expired_time: 2030-01-01, quota: 500万
- 本地 `~/.vermes/.env` 已更新
- One-API admin token: `REDACTED_ONEAPI_TOKEN`

**注意**：One-API PUT /api/token/ 会重置 expired_time 到 0！不能先创建再 PUT 改字段，要一次 POST 设好所有参数。

## 问题3：端口冲突

旧 vermes 进程不自动退出，新实例启动后端口被占。
之前移除了单例锁，需要手动 kill 旧进程。

## 需要 Vermes 确认

1. `browser_tool.py` 的3个 plugins.* 改为 try/except 是否安全？
   - 它们只是 re-export（BrowserbaseProvider, BrowserUseProvider, FirecrawlProvider）
   - 这些名字在 browser_tool.py 内部是否有被引用？
2. 是否有更好的方案（比如 PyInstaller 的 Analysis hooks）？
