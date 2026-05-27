#!/usr/bin/env python3
"""
Vermes GUI App — 双击即开原生窗口，无需浏览器。
pywebview 6.x: create_window → start(func=...)
单例锁：确保同一时间只有一个实例运行。
自动端口分配：避免与本机测试实例冲突。
"""

import sys
import os
import time
import threading
import platform
import webbrowser

APP_TITLE    = "Vermes - AI Agent"
DEFAULT_PORT = 9119
PORT_FILE     = os.path.expanduser("~/.vermes/gui_port.txt")
WINDOW_W     = 1200
WINDOW_H     = 800

LOCK_FILE = os.path.expanduser("~/.vermes/gui_app.lock")

# 确保 hermes_cli 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_cli.shutdown_signal import shutdown_event


class VermesAPI:
    """暴露给前端 JavaScript 的 Python API。"""

    _oauth_window = None  # 微信 OAuth 弹窗引用

    def open_external_browser(self, url):
        """用系统默认浏览器打开 URL。"""
        print(f"[Vermes API] 打开系统浏览器: {url}")
        try:
            webbrowser.open(url)
            return {"success": True}
        except Exception as e:
            print(f"[Vermes API] ❌ 打开浏览器失败: {e}")
            return {"success": False, "error": str(e)}

    def open_oauth_window(self, url):
        """打开微信 OAuth 原生窗口（不弹浏览器）。"""
        try:
            import webview
            print(f"[Vermes API] 打开 OAuth 原生窗口: {url[:80]}...")
            VermesAPI._oauth_window = webview.create_window(
                '微信登录', url,
                width=420, height=620, resizable=False,
                easy_drag=True, text_select=False,
            )
            def on_closed():
                print("[Vermes API] OAuth 窗口已关闭")
                VermesAPI._oauth_window = None
            VermesAPI._oauth_window.events.closed += on_closed
            return {"success": True}
        except Exception as e:
            print(f"[Vermes API] ❌ 打开 OAuth 窗口失败: {e}")
            return {"success": False, "error": str(e)}

    def close_oauth_window(self):
        """关闭微信 OAuth 原生窗口。"""
        try:
            if VermesAPI._oauth_window:
                print("[Vermes API] 关闭 OAuth 窗口")
                VermesAPI._oauth_window.destroy()
                VermesAPI._oauth_window = None
                return {"success": True}
            return {"success": False, "error": "no window"}
        except Exception as e:
            print(f"[Vermes API] ❌ 关闭 OAuth 窗口失败: {e}")
            return {"success": False, "error": str(e)}


def acquire_lock():
    """获取单例锁。成功返回 lock file object，失败返回 None（已有实例在运行）。"""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    if platform.system() == "Windows":
        try:
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


def find_available_port(start_port=9119, max_tries=20):
    """从 start_port 开始找第一个可用的端口。"""
    import socket
    for port in range(start_port, start_port + max_tries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            result = s.connect_ex(('127.0.0.1', port))
            s.close()
            if result != 0:  # 连接失败 = 端口可用
                return port
        except Exception:
            pass
    return None  # 都不可用


def wait_for_server(port, timeout=15):
    """等待后端服务器就绪，最多等 timeout 秒。"""
    import urllib.request
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def start_server():
    """后台启动 uvicorn + FastAPI，自动找可用端口。
    把实际端口写入 PORT_FILE，供 main() 读取。
    """
    os.environ["VERMES_WEB_SKIP_BUILD"] = "1"

    try:
        import uvicorn
        from hermes_cli.web_server import app as fastapi_app
    except ImportError as e:
        print(f"[Vermes] 依赖缺失: {e}")
        return

    # 找可用端口
    port = find_available_port(DEFAULT_PORT)
    if port is None:
        print(f"[Vermes] ❌ 错误：9119-9138 端口全部被占用，无法启动后端！")
        return

    # 写入端口文件，让 main() 知道正确的 URL
    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except Exception as e:
        print(f"[Vermes] ⚠️ 无法写入端口文件: {e}")

    print(f"[Vermes] 后端启动在端口 {port}")
    uvicorn.run(fastapi_app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                lifespan="off")


def on_dom_ready():
    """窗口 DOM 就绪后调用（pywebview 6.x）。"""
    print("[Vermes] DOM 就绪，窗口已打开。")


def main(lock_fd, port):
    """原生窗口优先，失败回退浏览器。"""
    url = f"http://127.0.0.1:{port}"
    print(f"[Vermes] 打开界面：{url}")
    try:
        import webview
        webview.create_window(
            title=APP_TITLE,
            url=url,
            width=WINDOW_W,
            height=WINDOW_H,
            resizable=True,
        )
        webview.start(gui='edgechromium', private_mode=False)
        print("[Vermes] 原生窗口已关闭")
        return
    except Exception as e:
        print(f"[Vermes] ❌ 原生窗口失败: {e}")
        print("[Vermes] 请检查 pywebview 是否正确安装: pip install pywebview")

    # 保持进程运行，等待退出信号
    try:
        shutdown_event.wait()
        print("[Vermes] 收到退出信号，关闭后端...")
    except KeyboardInterrupt:
        print("[Vermes] 退出。")
    finally:
        if platform.system() != "Windows":
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        os._exit(0)


if __name__ == "__main__":
    lock_fd = acquire_lock()
    if lock_fd is None:
        # 已有实例在运行，静默退出
        print("[Vermes] 已有实例在运行，请切换到已打开的窗口。")
        sys.exit(0)

    # 服务器在后台线程启动（不阻塞）
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    # 读取端口文件（start_server 会写入）
    port = DEFAULT_PORT
    for _ in range(60):  # 最多等 30 秒（find_available_port 可能扫 20 个端口）
        try:
            if os.path.exists(PORT_FILE):
                with open(PORT_FILE, "r") as f:
                    port = int(f.read().strip())
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        # 端口文件超时未写入，自动扫描实际运行端口
        print("[Vermes] 端口文件未就绪，自动扫描后端端口...")
        for scan_port in range(DEFAULT_PORT, DEFAULT_PORT + 20):
            if wait_for_server(scan_port, timeout=8):
                port = scan_port
                print(f"[Vermes] 发现后端在端口 {port}")
                break

    print(f"[Vermes] 等待后端服务器就绪 (port={port})...")
    if wait_for_server(port, timeout=15):
        print("[Vermes] 后端就绪，打开窗口。")
    else:
        print("[Vermes] 警告：后端未就绪，仍尝试打开窗口。")

    # GUI 在主线程启动（macOS 要求）
    main(lock_fd, port)
