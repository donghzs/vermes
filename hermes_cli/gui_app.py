#!/usr/bin/env python3
"""
Vermes GUI App — 双击即开原生窗口，无需浏览器。
pywebview 6.x: create_window → start(func=...)
单例锁：确保同一时间只有一个实例运行。
"""

import sys
import os
import time
import threading
import platform

APP_TITLE = "Vermes - AI Agent"
APP_URL   = "http://127.0.0.1:9119"
WINDOW_W  = 1200
WINDOW_H  = 800

LOCK_FILE = os.path.expanduser("~/.vermes/gui_app.lock")

# 确保 hermes_cli 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def acquire_lock():
    """获取单例锁。成功返回 lock file object，失败返回 None（已有实例在运行）。"""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if platform.system() == "Windows":
        try:
            # Windows: 独占写方式打开，已有实例则失败
            f = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            f = open(f, "w")
            f.write(str(os.getpid()))
            f.flush()
            return f
        except (OSError, IOError):
            return None
    else:
        import fcntl
        f = open(LOCK_FILE, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.write(str(os.getpid()))
            f.flush()
            return f
        except (IOError, OSError):
            f.close()
            return None


def wait_for_server(timeout=15):
    """等待后端服务器就绪，最多等 timeout 秒。"""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(APP_URL, method="HEAD")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_server():
    """后台启动 uvicorn + FastAPI。
    不设 HERMES_WEB_DIST，让 web_server.py 用 __file__.parent/web_dist 自动定位。
    PyInstaller 打包后 __file__ 指向临时目录中的 hermes_cli/，web_dist 就在旁边。
    """
    os.environ["VERMES_WEB_SKIP_BUILD"] = "1"

    try:
        import uvicorn
        from hermes_cli.web_server import app as fastapi_app
    except ImportError as e:
        print(f"[Vermes] 依赖缺失: {e}")
        return

    uvicorn.run(fastapi_app,
                host="127.0.0.1",
                port=9119,
                log_level="warning",
                lifespan="off")


def main(lock_fd):
    """pywebview.start 会在 GUI 初始化后调用此函数。
    所有窗口 API 必须在主线程调用，因此放在这里。"""
    import webview
    win = webview.create_window(
        title=APP_TITLE,
        url=APP_URL,
        width=WINDOW_W,
        height=WINDOW_H,
        min_size=(800, 600),
        resizable=True,
        focus=True,
    )
    # 保持 lock_fd 存活，防止 GC 关闭文件导致锁释放
    webview.start(debug=False, func=None)
    # 窗口关闭后释放锁
    if platform.system() != "Windows":
        import fcntl
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    lock_fd.close()


if __name__ == "__main__":
    lock_fd = acquire_lock()
    if lock_fd is None:
        # 已有实例在运行，尝试激活它的窗口（macOS）
        print("[Vermes] 已有实例在运行，退出。")
        # 用 AppleScript 激活已有窗口
        os.system('osascript -e "tell application \\"Python\\" to activate" 2>/dev/null')
        os.system('osascript -e "tell application \\"Vermes\\" to activate" 2>/dev/null')
        sys.exit(0)

    # 服务器在后台线程启动（不阻塞）
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    print("[Vermes] 等待后端服务器就绪...")
    if wait_for_server(timeout=15):
        print("[Vermes] 后端就绪，打开窗口。")
    else:
        print("[Vermes] 警告：后端未就绪，仍尝试打开窗口。")

    # GUI 在主线程启动（macOS 要求）
    main(lock_fd)
