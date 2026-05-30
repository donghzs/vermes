#!/usr/bin/env python3
"""
Vermes GUI App — 双击即开原生窗口，无需浏览器。
pywebview 6.x: create_window → start(func=...)
单例锁：防止多开，第二次打开自动聚焦已有窗口。
自动端口分配：避免与本机测试实例冲突。
"""

import sys
import os
import time
import threading
import platform
import webbrowser
import socket
import subprocess

APP_TITLE    = "Vermes - AI Agent"
DEFAULT_PORT = 9119
PORT_FILE     = os.path.expanduser("~/.vermes/gui_port.txt")
LOCK_FILE     = os.path.expanduser("~/.vermes/gui_app.lock")
WINDOW_W     = 1200
WINDOW_H     = 800


def acquire_lock():
    """获取单例锁。成功返回 True，失败返回 False（已有实例在运行）。"""
    try:
        # 检查锁文件中的PID是否还在运行
        if os.path.exists(LOCK_FILE):
            old_pid = open(LOCK_FILE).read().strip()
            if old_pid.isdigit():
                try:
                    os.kill(int(old_pid), 0)  # 检查进程是否存在
                    return False  # 旧进程还在运行
                except OSError:
                    pass  # 旧进程已退出，可以继续
        # 写入当前PID
        with open(LOCK_FILE, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True  # 锁文件异常，允许运行


def release_lock():
    """释放单例锁。"""
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def find_existing_port():
    """检查是否有已运行的实例，返回其端口。"""
    if os.path.exists(PORT_FILE):
        try:
            port = int(open(PORT_FILE).read().strip())
            # 检查端口是否在监听
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                s.close()
                return port
            s.close()
        except Exception:
            pass
    return None


# 确保 hermes_cli 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_cli.shutdown_signal import shutdown_event


class VermesAPI:
    """暴露给前端 JavaScript 的 Python API。"""

    _oauth_result = None  # 存储 OAuth 结果

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
        """打开微信 OAuth 原生窗口，监控 URL 获取 code（同步阻塞直到完成）。"""
        import webview
        import threading
        import time

        print(f"[Vermes API] 打开 OAuth 原生窗口")
        VermesAPI._oauth_result = None
        result_ready = threading.Event()

        win = webview.create_window(
            '微信登录', url,
            width=380, height=540, resizable=False,
            confirm_close=False,
            js_api=VermesAPI(),
        )

        def on_loaded():
            """页面加载完成后检查 URL"""
            try:
                current_url = win.evaluate_js('window.location.href')
                print(f"[Vermes API] 页面加载: {current_url[:80]}...")
                if 'code=' in current_url and 'vbit.top' in current_url:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(current_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    code = params.get('code', [''])[0]
                    state = params.get('state', [''])[0]
                    if code:
                        print(f"[Vermes API] ✅ 获取到 code: {code[:10]}... state: {state}")
                        VermesAPI._oauth_result = {"success": True, "code": code, "state": state}
                        result_ready.set()
                        try:
                            win.destroy()
                        except Exception:
                            pass
            except Exception as e:
                if 'destroyed' not in str(e).lower():
                    print(f"[Vermes API] URL 检查失败: {e}")

        def poll_url():
            """备用轮询：每 1.5 秒检查一次 URL"""
            while not result_ready.is_set():
                time.sleep(1.5)
                try:
                    if not hasattr(win, 'evaluate_js'):
                        break
                    current_url = win.evaluate_js('window.location.href')
                    if current_url and 'code=' in current_url and 'vbit.top' in current_url:
                        import urllib.parse
                        parsed = urllib.parse.urlparse(current_url)
                        params = urllib.parse.parse_qs(parsed.query)
                        code = params.get('code', [''])[0]
                        if code and not VermesAPI._oauth_result:
                            print(f"[Vermes API] ✅ 轮询获取到 code: {code[:10]}...")
                            VermesAPI._oauth_result = {"success": True, "code": code}
                            result_ready.set()
                            try:
                                win.destroy()
                            except Exception:
                                pass
                            break
                except Exception:
                    break

        def on_closed():
            """用户手动关闭窗口"""
            if not result_ready.is_set():
                print("[Vermes API] OAuth 窗口被用户关闭")
                VermesAPI._oauth_result = {"success": False, "error": "cancelled"}
                result_ready.set()

        win.events.loaded += on_loaded
        win.events.closed += on_closed
        poller = threading.Thread(target=poll_url, daemon=True)
        poller.start()

        # 等待结果（最多 5 分钟）
        result_ready.wait(timeout=300)

        if VermesAPI._oauth_result:
            return VermesAPI._oauth_result
        return {"success": False, "error": "timeout or cancelled"}


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
    _startup_log = os.path.expanduser("~/.vermes/gui_startup.log")

    try:
        import uvicorn
        from hermes_cli.web_server import app as fastapi_app
    except Exception as e:
        with open(_startup_log, "a") as _f:
            _f.write(f"[{time.strftime('%H:%M:%S')}] start_server IMPORT ERROR: {type(e).__name__}: {e}\n")
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
                log_level="info",
                lifespan="off")


def on_dom_ready():
    """窗口 DOM 就绪后调用（pywebview 6.x）。"""
    print("[Vermes] DOM 就绪，窗口已打开。")


def main(port):
    """原生窗口优先，失败回退浏览器。"""
    url = f"http://127.0.0.1:{port}"
    print(f"[Vermes] 打开界面：{url}")
    try:
        import webview

        # 加载页：先显示一个加载动画，等后端就绪后跳转真实页面
        loading_html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Vermes</title>
<style>
  body{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:#0a0a0a;color:#e5e5e5;font-family:system-ui,sans-serif}
  .spin{width:40px;height:40px;border:3px solid #333;border-top-color:#22c55e;border-radius:50%;animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  p{margin-top:16px;color:#888;font-size:14px}
</style></head>
<body><div style="text-align:center"><div class="spin"></div><p>正在启动 Vermes...</p></div></body></html>"""

        win = webview.create_window(
            title=APP_TITLE,
            html=loading_html,
            width=WINDOW_W,
            height=WINDOW_H,
            resizable=True,
            text_select=True,
            js_api=VermesAPI(),
        )

        def _load_real_url():
            """后台等待服务器就绪后切换到真实页面。"""
            if wait_for_server(port, timeout=20):
                print(f"[Vermes] 后端就绪，加载 {url}")
            else:
                print(f"[Vermes] ⚠️ 后端超时，仍尝试加载 {url}")
            win.load_url(url)

        import threading
        threading.Thread(target=_load_real_url, daemon=True).start()

        # macOS 用 cocoa，Windows 用 edgechromium，其他平台自动选择
        gui = None
        if platform.system() == 'Windows':
            gui = 'edgechromium'
        # WebView2 数据目录固定到 ~/.vermes/webview_data/
        # 防止更新 ZIP 覆盖时丢失 localStorage（聊天记录、用户偏好）
        storage_path = os.path.expanduser('~/.vermes/webview_data')
        os.makedirs(storage_path, exist_ok=True)
        webview.start(gui=gui, private_mode=False, storage_path=storage_path)
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
        os._exit(0)


def _apply_pending_update_if_any():
    """启动时检查 ~/.vermes/update/pending，如果有待应用的更新就执行。

    更新流程：
    1. 后端下载新版到 ~/.vermes/update/staging/
    2. 写 pending.json（含 platform、version、staging_path）
    3. 后端 shutdown → 进程退出
    4. gui_app 重启时检测到 pending.json → 应用更新 → 清理
    """
    import json
    pending_file = os.path.expanduser('~/.vermes/update/pending.json')
    if not os.path.exists(pending_file):
        return
    try:
        with open(pending_file, 'r') as f:
            pending = json.load(f)
        version = pending.get('version', 'unknown')
        staging = pending.get('staging_path', '')
        print(f"[Vermes Update] 发现待应用更新 v{version}，正在应用...")

        if platform.system() == 'Darwin':
            # macOS: staging 里是 .app 目录
            app_path = os.path.join(staging, 'Vermes.app')
            target = '/Applications/Vermes.app'
            if os.path.exists(app_path):
                import shutil
                if os.path.exists(target):
                    shutil.rmtree(target)
                shutil.copytree(app_path, target)
                print(f"[Vermes Update] ✅ 已更新到 v{version}")
                # 重启到新版本
                subprocess.Popen(['open', target])
                sys.exit(0)
        elif platform.system() == 'Windows':
            # Windows: staging 里是解压后的目录
            exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            for item in os.listdir(staging):
                src = os.path.join(staging, item)
                dst = os.path.join(exe_dir, item)
                import shutil
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            print(f"[Vermes Update] ✅ 已更新到 v{version}")
            # 重启
            subprocess.Popen([os.path.join(exe_dir, 'Vermes.exe')])
            sys.exit(0)

        # 更新成功后清理
        os.remove(pending_file)
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
    except Exception as e:
        print(f"[Vermes Update] ❌ 应用更新失败: {e}")
        # 不阻塞启动，继续正常运行
        try:
            os.remove(pending_file)
        except Exception:
            pass


def run_gui():
    """Entry point for GUI mode (called from main.py when frozen + no args)."""
    # ── 启动时检查是否有待应用的更新 ──
    _apply_pending_update_if_any()

    # 单例锁：防止多开
    if not acquire_lock():
        # 已有实例在运行，聚焦已有窗口
        existing_port = find_existing_port()
        if existing_port:
            print(f"[Vermes] 已有实例在运行 (port={existing_port})，聚焦窗口...")
            # 尝试聚焦已有的.app窗口
            try:
                subprocess.run(['open', '-a', 'Vermes'], timeout=3, 
                             capture_output=True, check=False)
            except Exception:
                # 回退：打开浏览器
                webbrowser.open(f"http://127.0.0.1:{existing_port}")
        else:
            print("[Vermes] 已有实例在运行，但无法找到端口。")
        sys.exit(0)

    # 注册退出清理
    import atexit
    atexit.register(release_lock)

    # 服务器在后台线程启动（不阻塞）
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    # 读取端口文件（start_server 会写入）
    port = DEFAULT_PORT
    for _ in range(60):
        try:
            if os.path.exists(PORT_FILE):
                with open(PORT_FILE, "r") as f:
                    port = int(f.read().strip())
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
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

    main(port)


if __name__ == "__main__":
    run_gui()
