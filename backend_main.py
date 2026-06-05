#!/usr/bin/env python3
"""
Vermes Backend (Headless) — Electron 后端专用入口。
无 pywebview / 无 GUI 依赖，仅启动 FastAPI + uvicorn。
用法: backend_main [--port 9119]
"""
import sys
import os
import time
import threading

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Agent 框架热加载 ──────────────────────────────────────────────────
# 如果 ~/.vermes/agent/ 存在，将其插入 sys.path 最前面，优先加载
# 这样 Agent 框架更新不需要修改 app bundle，只需替换 ~/.vermes/agent/
_vermes_home = os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes"))
_agent_dir = os.path.join(_vermes_home, "agent")
if os.path.isdir(_agent_dir):
    sys.path.insert(0, _agent_dir)
    # 读取版本号
    _ver_file = os.path.join(_agent_dir, ".version")
    if os.path.exists(_ver_file):
        try:
            _ver = open(_ver_file, encoding="utf-8").read().strip()
            print(f"[Vermes] Agent 框架 v{_ver} 已加载 ({_agent_dir})")
        except Exception:
            pass
    else:
        print(f"[Vermes] Agent 框架已加载 ({_agent_dir})")
# ── 热加载结束 ────────────────────────────────────────────────────────

import uvicorn
from hermes_cli.web_server import app
import signal as _signal

# Gateway server 实例（用于 restart 时优雅关闭）
server_instance = None


def _handle_sigterm(signum, frame):
    """SIGTERM 处理器：设置 shutdown_event 让主循环退出"""
    print("[Vermes] 收到 SIGTERM，准备关闭...")
    try:
        from hermes_cli.shutdown_signal import shutdown_event
        shutdown_event.set()
    except Exception:
        os._exit(0)


def main():
    global server_instance

    port = 9119
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

    # 强制启用 agent 模式（WebSocket 聊天端点）
    from hermes_cli import web_server
    web_server._DASHBOARD_EMBEDDED_CHAT_ENABLED = True

    print(f"[Vermes Backend] 启动 FastAPI, port={port}, agent模式=已启用")

    # 注册 SIGTERM 处理器（Electron 关闭后端时发送 SIGTERM）
    _signal.signal(_signal.SIGTERM, _handle_sigterm)

    # ── 启动 uvicorn 并进入 restart 循环 ──
    from hermes_cli.shutdown_signal import shutdown_event, restart_event

    # 初始启动：在子线程中运行 uvicorn，主线程监听信号
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", lifespan="off")
    server_instance = uvicorn.Server(config)
    threading.Thread(target=server_instance.run, daemon=True).start()
    print(f"[Vermes Backend] 后端已启动，监听 :{port}")

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
            print("[Vermes] 退出。")
            os._exit(0)

        if restart_event.is_set():
            print("[Vermes] 收到重启信号，重启 Gateway...")
            restart_event.clear()
            # 停止当前 uvicorn
            if server_instance:
                server_instance.should_exit = True
                time.sleep(2)  # 等待 uvicorn 优雅关闭
            # 重新启动 uvicorn（重新导入模块以加载更新的 Agent 框架）
            print("[Vermes] Gateway 重启中...")
            import importlib
            from hermes_cli import web_server as _ws
            importlib.reload(_ws)
            _ws._DASHBOARD_EMBEDDED_CHAT_ENABLED = True
            config = uvicorn.Config(_ws.app, host="127.0.0.1", port=port, log_level="info", lifespan="off")
            server_instance = uvicorn.Server(config)
            threading.Thread(target=server_instance.run, daemon=True).start()
            print("[Vermes] ✅ Gateway 已重启")
            continue  # 回到等待循环

        # shutdown_event
        print("[Vermes] 收到退出信号，关闭。")
        break


if __name__ == "__main__":
    main()
