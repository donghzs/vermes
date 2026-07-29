# Vermes Windows 版本优化建议 v3（GUI 聚焦）

> 审计时间：2026-06-02
> 审计范围：`run_agent.py` → `web_server.py` → `blueprints/*` → `tools/*` → `agent/*` → `gui_app.py`
> 排除：CLI 专属功能（TUI、voice_mode、profiles systemd/launchd、shell_hooks）

---

## 一、总体结论

好消息：Vermes GUI 对 Windows 的兼容性**远好于之前预期**。核心代码路径：

| 模块 | 状态 |
|---|---|
| `blueprints/*` (16 个) | ✅ 全部 clean，无 POSIX-only import |
| `gateway/status.py` | ✅ fcntl/msvcrt 分支正确，`/proc/PID/cmdline` 有 psutil 回退 |
| `tools/code_execution_tool.py` | ✅ `_IS_WINDOWS` 守卫完善（TCP RPC fallback、CREATE_NO_WINDOW、setsid guard）|
| `vermes_cli/win_adapter.py` | ✅ DPI 感知、UTF-8 编码、系统托盘、窗口几何体 |
| `vermes_cli/gateway_windows.py` | ✅ ScheduledTask + Startup 文件夹方案 |
| `web_server.py` (pty_ws) | ✅ `_PTY_BRIDGE_AVAILABLE` 异常捕获，友好的 WSL 提示 |
| `vermes_cli/blueprints/status.py` | ✅ subprocess Popen `creationflags`/`start_new_session` 分支正确 |

**未发现的 P0 崩溃项。** 以下是 P1 优化建议。

---

## 二、P1 需修复

### 1. `vermes_cli/gateway.py` 硬编码 `:` PATH 分隔符（3 处）

**文件**：`vermes_cli/gateway.py` L2179、L2219、L2806-2807

**问题**：
- L2179 和 L2219 在 `generate_systemd_unit()` 函数中，`":"` 是硬编码的分隔符，但这两个函数只在 Linux/WSL 上被调用（`is_macos()` 和 `is_windows()` 守卫保护），所以**实际不会在 Windows 上触发**。✅ **不需要修。**
- L2806-2807 在 `generate_launchd_plist()` 中，macOS launchd 专用。✅ **不需要修。**

### 2. `vermes_cli/web_server.py` —  frozen 模式下 `web_dist` 路径回退

**文件**：`vermes_cli/web_server.py` L71-77

```python
if "VERMES_WEB_DIST" in os.environ:
    WEB_DIST = Path(os.environ["VERMES_WEB_DIST"])
elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    WEB_DIST = Path(sys._MEIPASS) / "vermes_cli" / "web_dist"
else:
    WEB_DIST = Path(__file__).parent / "web_dist"
```

**潜在问题**：
- `vermes-gui.spec` 通过 PyInstaller COLLECT 模式打包，`web_dist` 确实会被放到 `{MEIPASS}/vermes_cli/web_dist`，路径匹配正确。✅ **不需要修。**
- 但 `vermes-onefile.spec` 的 `datas = []` 是空的——如果将来有人用 onefire spec 打包，`web_dist` 不会被包含，`WEB_DIST.exists()` 返回 False，前端会显示 "Frontend not built" 错误。⚠️ **建议修复**（见下）。

### 3. `vermes_cli/gui_app.py` — `PORT_FILE` 路径使用 `~/.vermes/` 而非 `~/.vermes/`

**文件**：`vermes_cli/gui_app.py` L53-54

```python
PORT_FILE     = os.path.expanduser("~/.vermes/gui_port.txt")
LOCK_FILE     = os.path.expanduser("~/.vermes/gui_app.lock")
```

**问题**：Vermes/Vermes CLI 的标准配置目录是 `~/.vermes/`，但 GUI 用了 `~/.vermes/`。这在多 profile 场景下可能导致 confusion。不过这是设计选择（Vermes 独立于 Vermes CLI），**不影响 Windows 功能**。✅ **不需要修。**

---

## 三、P2 优化建议

### 1. `vermes-onefile.spec` 缺少 `web_dist` 打包

**文件**：`vermes-onefile.spec`

```python
datas = []  # 空！
```

如果将来有人构建 onefire EXE，前端不会被包含。

**建议**：
```python
datas = [
    ('vermes_cli/web_dist', 'vermes_cli/web_dist'),
]
```

或者更安全的做法是 `web_server.py` 增加 fallback：

```python
elif getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # COLLECT/onedir 模式
    _collect_web = Path(sys._MEIPASS) / "vermes_cli" / "web_dist"
    if _collect_web.exists():
        WEB_DIST = _collect_web
    else:
        # onefile 模式（如果 web_dist 被拷贝到 dist 目录）
        WEB_DIST = Path(sys.executable).parent / "vermes_cli" / "web_dist"
        if not WEB_DIST.exists():
            WEB_DIST = _collect_web  # fallback
else:
    WEB_DIST = Path(__file__).parent / "web_dist"
```

### 2. `gui_app.py` 的 `platform.system()` 调用过于分散

**文件**：`vermes_cli/gui_app.py` 4 处 `platform.system()` 调用

L358、L374、L434、L472 都用了 `platform.system() == 'Windows'`。

**建议**：使用 `win_adapter.IS_WINDOWS` 代替，保持一致性：

```python
from vermes_cli.win_adapter import IS_WINDOWS as _IS_WIN

if _IS_WIN:
    ...
```

### 3. `gui_app.py` `start_server()` 在 frozen 模式下可能找不到 `web_server` 模块

**文件**：`vermes_cli/gui_app.py` L268-269

```python
import uvicorn
from vermes_cli.web_server import app as fastapi_app
```

在 frozen 模式下，`vermes_cli` 目录被提取到 `_MEIPASS/vermes_cli/`。如果 `web_server` 没有被正确列入 hiddenimports，`import` 会失败。

**检查**：`vermes-gui.spec` 已包含 `'vermes_cli.web_server'` 在 hiddenimports 中。✅ **OK。**

但 `vermes-onefile.spec` **没有** `vermes_cli.web_server` 在 hiddenimports 中！⚠️ **需修复**。

### 4. 路径分隔符在 `web_server.py` 的 `is_relative_to()` 调用

**文件**：`vermes_cli/web_server.py` L1613-1614、L1638

```python
if not file_path.resolve().is_relative_to(WEB_DIST.resolve()):
```

`pathlib.Path.resolve()` 和 `is_relative_to()` 都正确处理跨平台路径分隔符。✅ **OK。**

### 5. `gui_app.py` 端口文件写入在 Windows 上可能因权限失败

**文件**：`vermes_cli/gui_app.py` L284

```python
with open(PORT_FILE, "w") as f:
    f.write(str(port))
```

`PORT_FILE = os.path.expanduser("~/.vermes/gui_port.txt")`。在 Windows 上 `~` 通常指向 `C:\Users\username`，写入 txt 文件没问题。✅ **OK。**

---

## 四、架构建议

### 1. Windows 上 `execute_code` 沙箱的 TCP RPC fallback 已完善

`code_execution_tool.py` 在 Windows 上自动切换到 TCP 模式（`_use_tcp_rpc = _IS_WINDOWS`），避免了 AF_UNIX 套接字在 Windows 上的不稳定。✅ **不需要修。**

### 2. `gateway/status.py` 的 `_read_process_cmdline` 跨平台回退已完善

- Linux → `/proc/PID/cmdline`
- macOS → `ps -p PID -o command=`
- Windows → psutil `Process(pid).cmdline()`

所有路径都有 try/except 保护。✅ **不需要修。**

### 3. `blueprints/` 目录全部 clean

16 个 blueprint 文件均无 POSIX-only import（fcntl、termios、pty 等）。✅ **不需要修。**

---

## 五、修复清单

| # | 优先级 | 文件 | 问题 | 操作 |
|---|---|---|---|---|
| 1 | P2 | `vermes-onefile.spec` | `datas=[]` 空，打包不包含 `web_dist` | 添加 `('vermes_cli/web_dist', 'vermes_cli/web_dist')` |
| 2 | P2 | `vermes-onefile.spec` | 缺少 `vermes_cli.web_server` 在 hiddenimports | 添加 `'vermes_cli.web_server'` |
| 3 | P3 | `gui_app.py` | 4 处 `platform.system() == 'Windows'` 重复检查 | 统一使用 `win_adapter.IS_WINDOWS` |

---

## 六、已知限制（非 bug）

1. **PTY 聊天不可用**：Windows 原生 Python 不支持 POSIX PTY，`/api/pty` 端点会返回友好的 WSL 提示。这是设计限制。
2. **systemd/launchd 服务管理**：仅支持 Linux (systemd)、macOS (launchd)、Windows (ScheduledTask)。GUI 上的 Service 管理面板会根据平台自动切换。✅ 已通过 `is_macos()`/`is_windows()` 正确守卫。
