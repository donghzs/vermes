# Vermes Windows 版优化建议报告

**审计日期**: 2025-06-02  
**审计范围**: ~/Projects/vermes/ 全部源码（86 个 agent/ 文件 + 89 个 vermes_cli/ 文件 + 47 个 tools/ 文件 + 构建/打包系统）  
**审计焦点**: Windows 原生兼容性（不依赖 WSL）

---

## 1. 已有基础设施（良好的基础）

| 模块 | 行数 | 说明 |
|------|------|------|
| `vermes_cli/win_adapter.py` | 411 | 完整的 Win32 API 适配层：WebView2 检测、DPI 感知、单例聚焦、系统托盘、窗口位置持久化 |
| `vermes_cli/_subprocess_compat.py` | 175 | `windows_detach_flags()` / `resolve_node_command()` / 跨平台 Popen 参数统一 |
| `hermes_bootstrap.py` | 154 | UTF-8 引导修复：`PYTHONUTF8=1` + `subprocess.Popen` 猴子补丁，解决中文 Windows GBK 解码问题 |
| `vermes_cli/gateway_windows.py` | 1043 | 完整的 Windows gateway 服务管理：schtasks + Startup 文件夹双重降级 |
| `vermes_cli/clipboard.py` | 494 | PowerShell WinForms 剪贴板图片提取，原生 Windows + WSL 双模式 |
| `vermes_cli/gui_app.py` | 516 | pywebview 原生窗口，Windows edgechromium GUI 后端，日志重定向 |
| `packaging/` | — | 完整的 NSIS、Inno Setup、build_windows.bat、build-windows-installer.py |
| `scripts/check-windows-footguns.py` | — | AST 级静态分析脚本，检查 `preexec_fn` 和 `os.killpg` |
| `pyproject.toml` | — | 平台条件依赖：`tzdata; win32`、`ptyprocess; !win32`、`pywinpty; win32` |

**现状**: Windows 适配架构设计良好，但实现覆盖不完全。框架撑住了核心路径（GUI 窗口、gateway 服务、剪贴板），但中层模块（hook 引擎、安全检查、信号处理等）缺少 Windows 分支。

---

## 2. P0 — 立即修复（运行时崩溃级）

### 2.1 `signal.SIGHUP` / `signal.SIGPIPE` 无防护引用

**问题**: Windows 上不存在 `SIGHUP` 和 `SIGPIPE` 信号常量，直接引用会抛出 `AttributeError`。

| 文件 | 行号 | 问题 |
|------|------|------|
| `tui_gateway/entry.py` | 152, 156 | `signal.SIGPIPE` / `signal.SIGHUP` |
| `cli.py` | 13989, 14417 | `signal.SIGHUP` |
| `vermes_cli/main.py` | 7821 | `signal.SIGHUP` |

**修复方案**: 替换为 `getattr(signal, 'SIGHUP', signal.SIGTERM)` 或加 `if hasattr(signal, 'SIGHUP'):` 守卫。

**风险**: Windows 启动时立即崩溃。

### 2.2 `os.getloadavg()` 无防护

**问题**: `os.getloadavg()` 在 Windows 上抛出 `OSError`。

| 文件 | 行号 |
|------|------|
| `gateway/shutdown_forensics.py` | 148 |

**修复方案**: 加 `try/except OSError` 包装，Windows 上返回 `(0, 0, 0)`。

**风险**: gateway 启动/诊断时崩溃。

### 2.3 `os.uname()` 无防护

**问题**: `os.uname()` 在 Windows 上抛出 `AttributeError`。

| 文件 | 行号 |
|------|------|
| `tools/mcp_oauth.py` | 144 |

**修复方案**: 用 `platform.system() == "Darwin"` 替换，或用 `hasattr(os, 'uname')` 守卫。

**风险**: MCP OAuth 流程中断。

### 2.4 `start_new_session=True` 无防护

**问题**: 两处 `subprocess.Popen` 使用了 `start_new_session=True`，在 Windows 上被静默忽略（子进程不真正分离）。

| 文件 | 行号 |
|------|------|
| `tools/tts_tool.py` | 610 |
| `vermes_cli/blueprints/status.py` | 135 |

**修复方案**: 用 `**vermes_cli._subprocess_compat.windows_detach_popen_kwargs()` 替代。

**风险**: TTS 后台进程/状态监控守护进程无法在后台存活。

---

## 3. P1 — 高优先级（核心功能异常）

### 3.1 `agent/shell_hooks.py` — Hook 执行引擎在 Windows 上根本性错误

**这是 agent 运行 shell hooks 的核心模块**，多个关键假设全是 POSIX-only：

- **`shlex.split()`（行 383）**: 使用 POSIX 引号规则。Windows 路径 `C:\Program Files\foo` 中的 `\` 被当转义字符，`%VAR%` 不会被展开。
- **`_SCRIPT_EXTENSIONS`（行 718-723）**: 只认 `.sh/.bash/.zsh/.fish`，不认 `.bat/.cmd/.ps1`。Windows 原生钩子全部被忽略。
- **`os.X_OK`（行 826-827）**: Windows 上对所有文件返回 True，`is_bare_invocation` 逻辑完全失效。
- **`shell=False`（行 393-399）**: 对 `.bat/.cmd` 文件，Windows 需要 `shell=True` 或通过 `shutil.which()` 解析全路径。

**修复建议**: 
1. 用 `shlex.split(posix=False)`（Python 3.12+）或 `subprocess.list2cmdline()` 替代
2. 将 `.bat`, `.cmd`, `.ps1` 加入 `_SCRIPT_EXTENSIONS`
3. 对已知脚本扩展使用 `shell=True` 或 `shutil.which()` 解析路径

### 3.2 `agent/file_safety.py` — Windows 敏感路径覆盖完全缺失

写入安全名单全是 POSIX 路径（行 49-76）：`/etc/sudoers, /etc/passwd, /etc/shadow, /etc/systemd, ~/.bashrc` 等。

**Windows 敏感路径应加入**：

| 路径 | 风险 |
|------|------|
| `C:\Windows\System32\drivers\etc\hosts` | 系统 hosts 被改写 |
| `C:\Windows\System32\config\*` | 注册表配置单元 |
| `C:\ProgramData\*` | 全局应用数据 |
| `C:\Program Files\*` | 程序文件被篡改 |
| `C:\Boot\BCD` | 引导配置 |
| `C:\Windows\System32\Tasks\*` | 计划任务 |
| `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\*` | 自动启动 |
| `C:\Windows\System32\*` (关键文件子集) | 系统核心文件 |

### 3.3 `agent/display.py` — 旧版 cmd.exe ANSI 支持缺失

大量 24 位 ANSI 颜色码和 表情符号在旧版 Windows 控制台上显示为乱码。

**修复方案**: 在 Windows 上调用 `kernel32.SetConsoleMode(handle, ENABLE_VIRTUAL_TERMINAL_PROCESSING)` 启用 VT 处理，或集成 `colorama`。

### 3.4 `cron/scheduler.py` — Windows 调度器依赖 bash

`shutil.which("bash")` + 硬编码 `/bin/bash`（行 866-867），Windows 上找不到。

**修复方案**: 在 Windows 上用 `os.environ.get("COMSPEC", "cmd.exe")` 或 `powershell.exe` 替代。

### 3.5 `tools/environments/local.py` — 硬编码 POSIX shell 路径

行 224-227：`/usr/bin/bash`, `/bin/bash`, `/bin/sh`。

**修复方案**: 添加 Windows 分支，使用 `os.environ.get("COMSPEC", "cmd.exe")` 找 shell。

### 3.6 `vermes_cli/profiles.py` — 缺少 win32 分支

行 934-953 只有 Linux/Darwin 分支，无 Windows 路径。

**修复方案**: 添加 `elif sys.platform == "win32":` 分支。

---

## 4. P2 — 中等优先级（功能性降级/安全隐患）

### 4.1 `os.chmod()` 在 Windows 上语义不同

42+ 处使用了 `os.chmod(path, 0o700)`，Windows 上通过 ACL 控制权限，`chmod` 功能有限。不会崩溃但权限保护效果不足。

**关键文件**: `vermes_constants.py:277`, `hermes_logging.py:336`, `utils.py:56`, `vermes_cli/config.py:404/447/4833/4889`, `tools/tirith_security.py:426`, `tools/mcp_oauth.py:179`

**修复**: 对凭据/敏感文件使用 `icacls` 命令或 `win32security` API 设置 DACL。

### 4.2 `os.X_OK` 语义在 Windows 上失效

`os.X_OK` 在 Windows 上对所有文件返回 True。可执行性检查逻辑失效。

**受影响文件**: `tools/tirith_security.py:477/501/565/597/615/635`, `tools/code_execution_tool.py:1613`, `tools/environments/docker.py:118/138`, `agent/shell_hooks.py:826`, `agent/lsp/install.py:126`

**修复**: 在 Windows 上检查 `PATHEXT` 环境变量值替代 `os.X_OK`。

### 4.3 `tools/file_tools.py` — 安全文件列表硬编码 POSIX 路径

安全审查列表（行 152）含 `/etc/`, `/boot/`, `/usr/lib/systemd/`，缺少 `C:\Windows\`, `C:\Program Files\`。

### 4.4 `tool_executor.py` — 路径分隔符不一致

`os.getcwd()` 在 Windows 上返回 `C:\Users\xxx` 反斜杠路径，可能破坏内部路径比较。`threading.current_thread().ident` 在 Python 3.12+ 上可能返回 `None`。

---

## 5. P3 — 低优先级

### 5.1 构建系统优化

- `vermes-gui.spec:180`: `console=True` 生产发布时建议 `console=False` 避免 CMD 窗口闪现
- `vermes-gui.spec:67-68`: pywebview 在 Windows 上可能需要 `pythonnet`/`cefpython3` hidden import
- `build-windows-installer.py:110-123`: VC++ DLL 双份拷贝冗余，可优化为只放根目录

### 5.2 PTY 功能在 Windows 上不可用

`pty_bridge.py` 明确 POSIX-only。`pywinpty` 依赖已声明但未集成。

**优化**: 实现 `pywinpty` 适配器，使 dashboard chat 在 Windows ConPTY 上可用。

### 5.3 `hermes_logging.py` — inode 日志轮转在 Windows 上不可靠

`_ManagedRotatingFileHandler` 的 `st_dev/st_ino` 检测在 Windows 上可能为 0。

### 5.4 `tools/tts_tool.py` — 硬编码 `/tmp/`

行中的 `/tmp/` 路径应使用 `tempfile.gettempdir()`。

---

## 6. P4 — 长期优化

### 6.1 分发增强

- Winget / Chocolatey 安装包
- 数字签名（SmartScreen 拦截规避）
- 便携版（Portable）选项不写注册表

### 6.2 Windows 原生功能

- `vermes://` 协议深度链接
- 右键文件→发送到→Vermes
- Windows 任务栏跳转列表
- Windows Hello/PIN 解锁（替代 sudo 授权）

### 6.3 Windows CI 测试覆盖

- 在真实 Windows CI runner 上跑核心流程
- 中文/日文路径编码测试
- `os.chmod`/`os.X_OK` 在 Windows 上的实际行为验证

---

## 7. 修复优先级总表

| 优先级 | 数量 | 本质 | 修复工作量 |
|--------|------|------|-----------|
| P0 | 4 | 崩溃级，getattr/try-except 即可 | 小（每处 < 10 行） |
| P1 | 6 | 核心功能异常，需重构逻辑 | 中（shell_hooks.py 需较大改动） |
| P2 | 4 | 功能性降级 | 中 |
| P3 | 4 | 削峰填谷 | 小 |
| P4 | 3 | 原生体验增强 | 大 |

**总计建议**: 21 项优化，其中 10 项（P0+P1）建议优先在日启动前修复。
