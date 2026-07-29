# Vermes Windows 版本全面审计报告 (2026-06-02)

> 审计范围: `/Users/dongzusheng/Projects/vermes/` 源码（排除 `dist/`, `tests/`, `site-packages/`, `.venv/`）

## 总体评估

Vermes 对 Windows 的移植工作已经非常深入，大量关键路径已有完善的 `_IS_WINDOWS` 或 `sys.platform == "win32"` 守卫。已识别的基础设施包括：
- `vermes_cli/win_adapter.py` — DPI 感知、系统托盘、单例聚焦
- `vermes_cli/_subprocess_compat.py` — `windows_hide_flags()`、UTF-8 Popen 补丁
- `vermes_cli/gateway_windows.py` — schtasks + Startup 文件夹
- `vermes_cli/stdio.py` — ConsoleCP/ConsoleOutputCP 设为 65001
- `hermes_bootstrap.py` — `PYTHONUTF8=1` + subprocess Popen 猴子补丁
- `tools/environments/local.py` — Git Bash 多源查找

**结论**: P0 级崩溃问题已基本修复。剩余问题集中在 P1（功能降级）和 P2（ polish）。

---

## P0 — 运行时崩溃（已修复 ✅ / 新增发现 ⚠️）

### ✅ 1. signal.SIGHUP / signal.SIGPIPE — 已防护
所有关键文件中的 SIGHUP/SIGPIPE 设置都使用 `hasattr(signal, 'SIGHUP')` 守卫：
- `tui_gateway/entry.py:151-156` — `if hasattr(signal, "SIGPIPE")` / `if hasattr(signal, "SIGHUP")`
- `vermes_cli/main.py:7819` — `if hasattr(_signal, "SIGHUP")`
- `cli.py:13988,14416` — `if hasattr(_signal, 'SIGHUP')`

### ✅ 2. os.killpg / os.setsid — 已防护
所有 `os.killpg` 调用均在 `if not _IS_WINDOWS:` 分支内：
- `tools/process_registry.py:593-595` — guarded
- `tools/environments/local.py:555,593,605` — guarded
- `tools/process_registry.py:565` — `preexec_fn=None if _IS_WINDOWS else os.setsid` ✅
- `tools/code_execution_tool.py:1240` — 同上 ✅
- `tools/environments/local.py:534` — 同上 ✅

### ✅ 3. os.getloadavg() — 已防护
`gateway/shutdown_forensics.py:147-150` — `try/except (OSError, AttributeError)`

### ✅ 4. os.uname() — 已防护
`tools/mcp_oauth.py:143-147` — `try: os.uname() except AttributeError: pass`

### ⚠️ 5. os.O_NONBLOCK / fcntl — 局部问题
**文件**: `tools/process_registry.py:900-914`
- 已用 `if stdout is not None and not _IS_WINDOWS:` 守卫 ✅
- 但 `os.O_NONBLOCK` 在 Windows 上**存在**（值为 0x8000），不触发 AttributeError，所以不会崩溃

### ✅ 6. fcntl — 全部防护
- `tools/memory_tool.py` — `try: import fcntl except ImportError: fcntl = None` ✅
- `tools/skill_usage.py` — 同上 ✅
- `tools/environments/file_sync.py` — 同上 ✅
- `vermes_cli/auth.py` — 同上 ✅

### ✅ 7. import pty / termios — 已隔离
- `vermes_cli/pty_bridge.py:10-11` — 文档明确标注 POSIX-only
- `tools/terminal_tool.py:428-433` — `import termios` 在 `os.open("/dev/tty")` 之后，仅在 Linux 可用路径

### ✅ 8. pwd / grp — 局部问题
**文件**: `vermes_cli/gateway.py:1788-1808` (`_system_service_identity`)
- 直接 `import pwd` / `import grp` 无防护
- **但** 此函数只被 systemd 服务安装路径调用（`supports_systemd_services()` 先检查 `is_linux()`）
- `vermes_cli/gateway.py:1941` 同样 — 仅在 Linux 路径下调用

### ⚠️ 9. resource.getrusage — 潜在崩溃
**文件**: `gateway/memory_monitor.py:63-65`
```python
import resource
...
maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
```
`resource` 模块在 CPython for Windows 上**存在**（有 `getrusage`），但行为有限。需验证是否返回有效值。

---

## P1 — 高优先级（核心功能降级）

### 1. shell_hooks.py — Windows 引号/脚本扩展问题

**文件**: `agent/shell_hooks.py`

#### 1a. shlex.split() POSIX 引号规则
**行 383, 735, 822**: `shlex.split(command)` 默认使用 POSIX 解析规则。
- Windows 路径 `"C:\Users\My Name\hook.ps1"` 中的空格和反斜杠会被错误解析
- Python 3.12+ 支持 `shlex.split(command, posix=False)` 使用 Windows 规则
- 对于 Python 3.9-3.11，建议用 `subprocess.list2cmdline()` 验证或 `shlex.split(posix=False)`

#### 1b. _SCRIPT_EXTENSIONS 缺少 Windows 脚本扩展
**行 718-723**:
```python
_SCRIPT_EXTENSIONS = (
    ".sh", ".bash", ".zsh", ".fish",
    ".py", ".pyw",
    ".rb", ".pl", ".lua",
    ".js", ".mjs", ".cjs", ".ts",
)
```
**缺少**: `.bat`, `.cmd`, `.ps1` — Windows 用户常用脚本类型无法被正确识别和解析。

#### 1c. shell=False 对脚本文件不适用
**行 393-400**: 当 command 是 `C:\path\hook.bat` 时，`shell=False` 需要注册表关联或直接执行 `.bat` 可执行文件。
- Windows 上 `.bat/.cmd` 需要通过 `shell=True` 或 `cmd.exe /c` 执行
- `.ps1` 默认被 PowerShell 阻止（ExecutionPolicy），需要特殊处理

#### 1d. os.X_OK 在 Windows 上始终返回 True
**行 826**:
```python
required = os.X_OK if is_bare_invocation else os.R_OK
# Windows: os.access(path, os.X_OK) always returns True!
```
**建议**: Windows 上用 `os.access(path, os.R_OK)` 替代。

#### 建议修复：
```python
# 在文件顶部
_SCRIPT_EXTENSIONS_WINDOWS = (".bat", ".cmd", ".ps1")
def _is_windows() -> bool:
    return sys.platform == "win32"

# 对 shlex.split 使用 Python 3.12+ 的 posix 参数或兼容方案
if sys.version_info >= (3, 12):
    _shlex_split = lambda cmd: shlex.split(cmd, posix=not _is_windows())
else:
    _shlex_split = shlex.split  # fallback — Windows 上仍有风险

# _SCRIPT_EXTENSIONS 动态扩展
_ALL_SCRIPT_EXTENSIONS = _SCRIPT_EXTENSIONS + (
    _SCRIPT_EXTENSIONS_WINDOWS if _is_windows() else ()
)

# os.X_OK 替换
if _is_windows():
    required = os.R_OK  # X_OK meaningless on Windows
else:
    required = os.X_OK if is_bare_invocation else os.R_OK
```

### 2. voice_mode.py — Windows 音频播放缺失
**文件**: `tools/voice_mode.py:887-894`
```python
if system == "Darwin":
    players.append(["afplay", file_path])
players.append(["ffplay", ...])  # 需要 ffmpeg
if system == "Linux":
    players.append(["aplay", ...])
```
**Windows 没有 fallback** — 在 Windows 上只有 ffplay（需要 ffmpeg 安装）。
**建议**: 添加 Windows fallback：
```python
elif system == "Windows":
    # 方案1: winsound.Beep（简单但音质差）
    # 方案2: PowerShell Invoke-Expression 调用 System.Media.SoundPlayer
    # 方案3: pygame.mixer 已跨平台
    players.append(["powershell", "-c", 
        f"(New-Object Media.SoundPlayer '{file_path}').PlaySync()"])
```

### 3. agent/display.py — ANSI 颜色在旧版 Windows 终端不渲染
**文件**: `agent/display.py` 全文件
- 硬编码 ANSI 转义码 `"\033[0m"`、`"\033[31m"` 等
- Windows 10 以上 + VT 模式可以渲染，但 Windows 7/旧版 PowerShell 不行
- 现有 `vermes_cli/stdio.py` 已设置 `SetConsoleCP(65001)`，但未启用 VT 模式

**建议**: 集成 colorama 或调用 `SetConsoleMode` 启用 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`。

### 4. code_execution_tool.py — /tmp 硬编码
**文件**: `tools/code_execution_tool.py:1102`
```python
_sock_tmpdir = "/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
```
macOS 上 `/tmp` 和 `tempfile.gettempdir()` 是同一个路径 → 此代码实际等价于：
```python
_sock_tmpdir = tempfile.gettempdir()  # C:\Users\<user>\AppData\Local\Temp on Windows
```
**结论**: 实际上**不会崩溃**，但 `/tmp` 字符串对 Windows 用户会造成困惑。可保留现状或统一用 `tempfile.gettempdir()`。

### 5. browser_tool.py — sys.platform == "darwin" 缺失 Windows 分支
**文件**: `tools/browser_tool.py:1154` 和 `tools/browser_tool.py:3517`
```python
if sys.platform == "darwin":
    # macOS 特定浏览器查找
if sys.platform == "win32":
    # Windows 查找
```
需确认 Windows 分支（行 3519）是否覆盖完整。

### 6. vermes_cli/gateway.py — _system_service_identity 直接 import pwd/grp
**文件**: `vermes_cli/gateway.py:1788-1808`
- `import grp` / `import pwd` 无防护
- 虽然被 `is_linux()` 守卫，但代码层面不够健壮
- 建议添加 `try/except ImportError`

### 7. vermes_cli/profiles.py — 仅 Linux/macOS 服务管理
**文件**: `vermes_cli/profiles.py:934-962`
- 仅处理 Linux (systemd) 和 macOS (launchd)
- **缺少 Windows (schtasks/Startup) 分支**
- 在 Windows 上运行 profile 切换时不执行 Windows 服务清理

### 8. cron/scheduler.py — .sh/.bash 脚本需要 Git Bash
**文件**: `cron/scheduler.py:866-874`
- `shutil.which("bash")` 在原生 Windows 无 Git Bash 时返回 None
- 已有清晰的错误提示，但**用户体验差**
- 建议支持 `.py` 脚本替代方案，或在错误信息中提供自动安装 Git Bash 的指引

### 9. tools/file_tools.py — 敏感路径仅包含 POSIX
**文件**: `tools/file_tools.py:152-155`
```python
"/etc/", "/boot/", "/usr/lib/systemd/",
```
缺少 Windows 敏感路径：`C:\Windows\System32\`, `C:\ProgramData\`, `C:\Program Files\` 等。

### 10. agent/file_safety.py — 敏感路径仅包含 POSIX
**文件**: `agent/file_safety.py:49-82`
- 缺少 `.ssh/authorized_keys` 在 Windows 上的等效路径 (`C:\Users\<user>\.ssh\authorized_keys`)
- 缺少 Windows 系统路径保护

---

## P2 —  polish 和低影响问题

### 1. PATH 分隔符使用 `:` 硬编码
**文件**: `vermes_cli/gateway.py:2807`
```python
[p for p in os.environ.get("PATH", "").split(":") if p]
```
Windows PATH 分隔符是 `;`，应用 `os.pathsep`。

**文件**: `tools/environments/local.py:278-279`
```python
"/opt/homebrew/bin:/opt/homebrew/sbin:"
"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
```
这是 POSIX 路径，Windows 不受影响（有 `_IS_WINDOWS` 守卫）。

### 2. shlex.quote 在 Windows 上
**文件**: `tools/process_registry.py:646-650`
- 用于构建 shell 命令字符串
- `shlex.quote()` 使用 POSIX 转义规则
- 但此代码在 `if not _IS_WINDOWS:` 分支内 → ✅ 已防护

### 3. terminal_tool.py — termios tcsetattr
**文件**: `tools/terminal_tool.py:428-433`
- 在 `os.open("/dev/tty")` 之后，Windows 上 `/dev/tty` 不存在 → 抛 FileNotFoundError
- 已在 `except` 分支恢复终端属性（行 448-449）
- **结论**: 不会崩溃，但也不会生效。在 Windows 上终端 echo 控制不可用。

### 4. docker.py — Docker 相关硬编码
**文件**: `tools/environments/docker.py`
- `"/tmp/.hermes_sync.{pid}.tar"` — Docker 容器内使用，不受 Windows 影响
- Docker 本身只在 WSL/容器上可用
- **结论**: 可以安全忽略

### 5. browser_connect.py — start_new_session
**文件**: `vermes_cli/browser_connect.py:115`
- 返回 `{"start_new_session": True}` 给调用者
- 调用者 `browser_tool.py` 是否处理了 Windows？需确认。

### 6. vermes_cli/kanban_db.py — /proc 路径
**文件**: `vermes_cli/kanban_db.py:3711`
```python
with open(f"/proc/{int(pid)}/status", "r", encoding="utf-8") as f:
```
- 仅 `sys.platform == "linux"` 分支内 → ✅ 已防护

### 7. vermes_cli/gateway.py — /proc 路径
**文件**: `vermes_cli/gateway.py:412`
```python
cmdline = open(f"/proc/{pid}/cmdline", "rb").read()
```
需确认是否在任何 Windows 路径下被调用。

### 8. gateway.py — /etc/systemd 路径
**文件**: `vermes_cli/gateway.py:1333, 1602`
- 仅 `is_linux()` 分支 → ✅ 已防护

### 9. 构建/打包
**文件**: `build-windows-installer.py`, `vermes-inno-setup.iss`
- Inno Setup 和 NSIS 脚本需确保所有 DLL 依赖（特别是 `libcrypto`, `libssl`, `python*.dll`）都被正确包含
- PyInstaller 的 `--hidden-import` 需包含 `msvcrt`（用于 Windows 文件锁）

---

## P3 — 建议优化

### 1. 统一平台检查
代码中混用 `platform.system() == "Windows"`, `sys.platform == "win32"`, `os.name == "nt"`, `_IS_WINDOWS`, `is_windows()`。
**建议**: 建立统一的 `_IS_WINDOWS` 常量在 `vermes_constants.py` 或 `vermes_cli/_subprocess_compat.py` 中，全项目引用。

### 2. PowerShell 脚本执行策略
`shell_hooks.py` 支持 `.ps1` 时，PowerShell 的 ExecutionPolicy 默认阻止脚本执行。
**建议**: 添加 `"Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass"` 逻辑。

### 3. Windows 浏览器发现
Chrome/Edge 在 Windows 上的安装路径：
- `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`
- `C:\Program Files\Google\Chrome\Application\chrome.exe`
- `%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe`

### 4. Windows Node.js 发现
`%ProgramFiles%\nodejs\node.exe` 或 `%LOCALAPPDATA%\fnm\...`

### 5. Windows Git 发现
`shutil.which("git")` 通常可以工作，但需确保 PATH 包含 `git.exe`。

---

## 修复优先级总结

| 优先级 | 问题 | 影响 | 工作量 |
|--------|------|------|--------|
| P1 | shell_hooks.py shlex.split + _SCRIPT_EXTENSIONS | 钩子脚本执行失败 | 中 |
| P1 | voice_mode.py Windows 音频播放 | 语音播报无声 | 小 |
| P1 | display.py ANSI 颜色 | 终端颜色丢失 | 小 |
| P1 | file_safety.py 缺 Windows 敏感路径 | 安全风险 | 小 |
| P1 | profiles.py 缺 Windows 服务管理分支 | profile 切换异常 | 小 |
| P2 | PATH 分隔符 `:` vs `;` | 工具发现失败 | 极小 |
| P2 | 统一平台检查常量 | 可维护性 | 中 |
| P2 | .ps1 ExecutionPolicy | 脚本执行被阻止 | 小 |
