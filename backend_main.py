#!/usr/bin/env python3
"""
Vermes Backend (Headless) — Electron 后端专用入口。
无 pywebview / 无 GUI 依赖，仅启动 FastAPI + uvicorn。
用法: backend_main [--port 9119]
"""
import logging

logger = logging.getLogger(__name__)

# ── Windows UTF-8 bootstrap ──────────────────────────────────────────
# 必须在任何 stdout/print/subprocess 之前。修复两个 Windows 中文 locale 问题：
#   1) StreamHandler 写 emoji/中文时 UnicodeEncodeError: 'gbk' codec
#   2) subprocess text=True 默认 GBK 解码 → 工具输出丢失 → "无响应"
# import 即生效（模块底部自动调用 apply_windows_utf8_bootstrap）。POSIX 无副作用。
try:
    import hermes_bootstrap  # noqa: F401 — side-effect: reconfigure stdio to UTF-8
except Exception:
    pass

import sys
import os
import time
import threading

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 阻止 PyInstaller 打包的 Python 自动运行 ensurepip
os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
import importlib
try:
    importlib.import_module('ensurepip')._main = lambda *a, **kw: None
except Exception:
    pass

import uvicorn
from hermes_cli.web_server import app
import signal as _signal

# Gateway server 实例（用于 restart 时优雅关闭）
server_instance = None


def _handle_sigterm(signum, frame):
    """SIGTERM 处理器：设置 shutdown_event 让主循环退出"""
    logger.info("[Vermes] 收到 SIGTERM，准备关闭...")
    try:
        from hermes_cli.shutdown_signal import shutdown_event
        shutdown_event.set()
    except Exception:
        os._exit(0)


def _deploy_builtin_modules():
    """首次启动时将内置生态模块（ScholarForge）部署到 ~/.vermes/modules/。

    打包里 hermes_cli/scholarforge/ 有源码但模块加载器只扫 ~/.vermes/modules/。
    检测 module.yaml 不存在时复制后端代码 + 写入清单文件。
    前端 dist 不在打包里，跳过（用户可通过对话方式使用文献工具）。
    """
    import shutil

    # 定位打包内的 scholarforge 源码目录
    _root = os.path.dirname(os.path.abspath(__file__))
    _pkg_sf = os.path.join(_root, "hermes_cli", "scholarforge")
    if not os.path.isdir(_pkg_sf):
        logger.debug("[Vermes] 内置 scholarforge 源码目录不存在，跳过部署")
        return

    # 定位模块安装目录
    try:
        from hermes_constants import get_hermes_home
        _modules_dir = get_hermes_home() / "modules"
    except Exception:
        # hermes_constants 可能未初始化，回退到 ~/.vermes
        from pathlib import Path
        _modules_dir = Path(os.path.expanduser("~")) / ".vermes" / "modules"

    _sf_dir = _modules_dir / "scholarforge"
    _manifest = _sf_dir / "module.yaml"

    if _manifest.exists():
        logger.debug("[Vermes] ScholarForge 模块已部署，跳过")
        return

    # 创建模块目录
    _sf_dir.mkdir(parents=True, exist_ok=True)

    # 复制后端代码到 backend/
    _backend_dir = _sf_dir / "backend"
    _backend_dir.mkdir(exist_ok=True)
    for _fn in os.listdir(_pkg_sf):
        if _fn.startswith("__pycache__") or _fn.startswith("tests"):
            continue
        _src = os.path.join(_pkg_sf, _fn)
        _dst = _backend_dir / _fn
        if os.path.isfile(_src) and _fn.endswith(".py"):
            shutil.copy2(_src, _dst)
        elif os.path.isdir(_src):
            shutil.copytree(_src, _dst, dirs_exist_ok=True)

    # 复制根级 tools.py（module.yaml 指向 backend/tools.py，但旧版在根目录）
    _root_tools = os.path.join(_pkg_sf, "tools.py")
    if os.path.exists(_root_tools):
        shutil.copy2(_root_tools, _sf_dir / "tools.py")

    # 写入 module.yaml
    _yaml_content = """\
name: scholarforge
display_name: ScholarForge 论文写作
version: 1.0.0
description: AI 全链路学术写作——文献搜索、STORM 写作、查重检测、论文评分、Word 导出
author: Vermes Team
license: MIT
homepage: https://vbit.top/vermes

backend:
  entry: backend/blueprint.py
  tools_entry: backend/tools.py
  dependencies:
    - pyyaml>=6.0

frontend:
  entry: frontend/dist/entry.js
  route: /scholarforge
  icon: 📝
  menu_title: 论文

permissions:
  - llm_call
  - file_read
  - file_write
  - web_search

compatibility:
  vermes_min: "2.1.0"
  platforms: [macos, windows, linux]
"""
    _manifest.write_text(_yaml_content, encoding="utf-8")

    logger.info("[Vermes Backend] ✅ ScholarForge 模块已部署到 %s", _sf_dir)


def main():
    global server_instance

    port = 9119
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

# ── 启动预检：关键文件语法检查 ────────────────────────────────
    _KEY_FILES = [
        "hermes_cli/web_server.py",
        "backend_main.py",
        "hermes_cli/blueprints/chat.py",
        "hermes_cli/blueprints/update.py",
    ]
    for _f in _KEY_FILES:
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), _f)
        if os.path.exists(_p):
            import ast
            try:
                with open(_p, "r", encoding="utf-8") as _fh:
                    ast.parse(_fh.read(), filename=_f)
            except SyntaxError as _e:
                logger.info(f"[Vermes] ❌ 启动失败: {_f} 语法错误")
                logger.info(f"       {_e}")
                sys.exit(1)
    logger.info("[Vermes] ✅ 启动预检通过")

    # ── 启动崩溃看门狗 ─────────────────────────────────────────────────
    _CRASH_MARKER = "/tmp/vermes-startup.lock"
    if os.path.exists(_CRASH_MARKER):
        logger.info("[Vermes] ⚠️ 检测到上次启动异常退出")
        # 尝试回滚到上一个备份版本
        try:
            from hermes_cli.update_manager import list_backups, rollback_to_version
            _backups = list_backups()
            if _backups:
                _last = _backups[-1]
                _ver = _last.get("version", "")
                logger.info(f"[Vermes] 🔄 自动回滚到 v{_ver} ...")
                rollback_to_version(_ver)
                logger.info(f"[Vermes] ✅ 已回滚到 v{_ver}，重启后生效")
            else:
                logger.info("[Vermes] ❌ 无可用备份，请手动修复")
        except Exception as _e:
            logger.info(f"[Vermes] ❌ 自动回滚失败: {_e}")
        os.remove(_CRASH_MARKER)  # 防止循环回滚
    # 写入新标记
    with open(_CRASH_MARKER, "w") as _f:
        _f.write(str(time.time()))
    # 10 秒后如果还活着，清除标记
    def _clear_marker():
        import time as _t
        _t.sleep(10)
        if os.path.exists(_CRASH_MARKER):
            os.remove(_CRASH_MARKER)
            logger.info("[Vermes] ✅ 启动稳定，看门狗已解除")
    threading.Thread(target=_clear_marker, daemon=True).start()

    # 强制启用 agent 模式（WebSocket 聊天端点）
    from hermes_cli import web_server
    web_server._DASHBOARD_EMBEDDED_CHAT_ENABLED = True

    # 启动时即注册所有文献源 + 工具服务（Settings UI 依赖此数据）
    # 注意：lifespan="off" 禁用 startup 事件，必须在这里手动调用
    try:
        from agent.literature_registry import bootstrap_builtin_providers
        bootstrap_builtin_providers()
        logger.info("[Vermes Backend] ✅ 文献源 provider 已注册")
    except Exception as _e:
        logger.warning(f"[Vermes Backend] ⚠️ 文献源注册失败: {_e}")
    # 触发 tools 模块的 register_service（openrouter/daytona 等）
    try:
        import tools.openrouter_client  # noqa: F401 — side-effect: register_service
        import tools.terminal_tool      # noqa: F401 — side-effect: register_service
        logger.info("[Vermes Backend] ✅ 工具服务凭证已注册")
    except Exception as _e:
        logger.warning(f"[Vermes Backend] ⚠️ 工具服务注册失败: {_e}")

    # ── 部署内置生态模块（ScholarForge 等）到 ~/.vermes/modules/ ──
    # 首次安装时 modules/ 目录不存在，生态模块加载器找不到模块。
    # 从打包内的 hermes_cli/scholarforge/ 复制后端代码 + 写入 module.yaml。
    try:
        _deploy_builtin_modules()
    except Exception as _e:
        logger.warning(f"[Vermes Backend] ⚠️ 内置模块部署失败: {_e}")

    logger.info(f"[Vermes Backend] 启动 FastAPI, port={port}, agent模式=已启用")

    # 注册 SIGTERM 处理器（Electron 关闭后端时发送 SIGTERM）
    _signal.signal(_signal.SIGTERM, _handle_sigterm)

    # ── 启动 uvicorn 并进入 restart 循环 ──
    from hermes_cli.shutdown_signal import shutdown_event, restart_event

    # 初始启动：在子线程中运行 uvicorn，主线程监听信号
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", lifespan="off")
    server_instance = uvicorn.Server(config)
    threading.Thread(target=server_instance.run, daemon=True).start()
    logger.info(f"[Vermes Backend] 后端已启动，监听 :{port}")

    # ── restart 循环：Agent 框架更新后重启 gateway，不关壳 ──
    while True:
        try:
            # 等待任一信号
            while not shutdown_event.is_set() and not restart_event.is_set():
                shutdown_event.wait(timeout=1.0)
                restart_event.wait(timeout=0.1)
                if shutdown_event.is_set() or restart_event.is_set():
                    break
        except KeyboardInterrupt:
            logger.info("[Vermes] 退出。")
            os._exit(0)

        if restart_event.is_set():
            logger.info("[Vermes] 收到重启信号，重启进程...")
            restart_event.clear()
            # 优雅关闭当前 uvicorn
            if server_instance:
                server_instance.should_exit = True
                time.sleep(2)  # 等待 uvicorn 优雅关闭
            # 通过 subprocess 重启自身 (完全干净的进程)
            import subprocess
            env = os.environ.copy()
            env["VERMES_RESTARTED"] = "1"
            subprocess.Popen(
                [sys.executable, __file__, "--port", str(port)],
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            logger.info("[Vermes] ✅ 新进程已启动，当前进程退出")
            os._exit(0)

        # shutdown_event
        logger.info("[Vermes] 收到退出信号，关闭。")
        break


if __name__ == "__main__":
    main()
