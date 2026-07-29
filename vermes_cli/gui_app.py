#!/usr/bin/env python3
"""
Vermes GUI App — 双击即开原生窗口，无需浏览器。
pywebview 6.x: create_window → start(func=...)
单例锁：防止多开，第二次打开自动聚焦已有窗口。
自动端口分配：避免与本机测试实例冲突。
"""

import logging

logger = logging.getLogger(__name__)
import sys
import os
import time
import threading
import platform
import webbrowser
import socket

# Gateway server 实例（用于 restart 时优雅关闭）
server_instance = None
import subprocess

# ── 日志重定向：console=True 时输出到 CMD 窗口 + 日志文件 ──
# 同时写入文件，方便事后排查。
if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    import io
    _log_dir = os.path.expanduser("~/.vermes")
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "gui_app.log")
    class _Tee(io.TextIOBase):
        """同时写入原始流和日志文件。"""
        def __init__(self, orig, path):
            self._orig = orig
            self._file = open(path, "a", encoding="utf-8", buffering=1)
        def write(self, s):
            r = self._orig.write(s) if self._orig else len(s)
            self._file.write(s)
            return r
        def flush(self):
            if self._orig:
                self._orig.flush()
            self._file.flush()
        def fileno(self):
            return self._orig.fileno() if self._orig else -1
    try:
        sys.stdout = _Tee(sys.stdout, _log_path)
        sys.stderr = _Tee(sys.stderr, _log_path)
    except Exception:
        pass
    logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Vermes GUI started (pid={os.getpid()}, frozen={getattr(sys, 'frozen', False)})")

# ── Windows 适配层初始化（DPI 感知 + UTF-8 编码） ──
from vermes_cli import win_adapter
win_adapter.init()

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


# 确保 vermes_cli 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Agent 框架热加载 ──────────────────────────────────────────────────
_vermes_home = os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes"))
_agent_dir = os.path.join(_vermes_home, "agent")
if os.path.isdir(_agent_dir):
    sys.path.insert(0, _agent_dir)
    _ver_file = os.path.join(_agent_dir, ".version")
    if os.path.exists(_ver_file):
        try:
            _ver = open(_ver_file, encoding="utf-8").read().strip()
            logger.info(f"[Vermes] Agent 框架 v{_ver} 已加载 ({_agent_dir})")
        except Exception:
            pass
    else:
        logger.info(f"[Vermes] Agent 框架已加载 ({_agent_dir})")
# ── 热加载结束 ────────────────────────────────────────────────────────

from vermes_cli.shutdown_signal import shutdown_event


class VermesAPI:
    """暴露给前端 JavaScript 的 Python API。"""

    _oauth_result = None  # 存储 OAuth 结果

    def open_external_browser(self, url):
        """用系统默认浏览器打开 URL。"""
        logger.info(f"[Vermes API] 打开系统浏览器: {url}")
        try:
            webbrowser.open(url)
            return {"success": True}
        except Exception as e:
            logger.info(f"[Vermes API] ❌ 打开浏览器失败: {e}")
            return {"success": False, "error": str(e)}

    def open_oauth_window(self, url):
        """打开微信 OAuth 原生窗口，居中在主窗口中央，监控 URL 获取 code。"""
        import webview
        import threading
        import time

        logger.info(f"[Vermes API] 打开 OAuth 原生窗口")
        VermesAPI._oauth_result = None
        result_ready = threading.Event()

        # ── 计算居中位置（相对于主窗口） ──
        ow, oh = 420, 580
        ox, oy = None, None
        try:
            main_win = webview.windows[0]
            mx, my = main_win.x, main_win.y
            mw, mh = main_win.width, main_win.height
            ox = int(mx + (mw - ow) / 2)
            oy = int(my + (mh - oh) / 2)
            logger.info(f"[Vermes API] 主窗口=({mx},{my}) {mw}x{mh} → OAuth窗口居中=({ox},{oy})")
        except Exception as e:
            logger.info(f"[Vermes API] 获取主窗口位置失败({e})，使用屏幕居中")

        create_kwargs = dict(
            width=ow, height=oh,
            resizable=False, confirm_close=False,
            js_api=VermesAPI(),
        )
        if ox is not None and oy is not None:
            create_kwargs['x'] = ox
            create_kwargs['y'] = oy

        win = webview.create_window('微信登录', url, **create_kwargs)

        def on_loaded():
            """页面加载完成后检查 URL"""
            try:
                current_url = win.evaluate_js('window.location.href')
                logger.info(f"[Vermes API] 页面加载: {current_url[:80]}...")
                if 'code=' in current_url and 'vbit.top' in current_url:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(current_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    code = params.get('code', [''])[0]
                    state = params.get('state', [''])[0]
                    if code:
                        logger.info(f"[Vermes API] ✅ 获取到 code: {code[:10]}... state: {state}")
                        VermesAPI._oauth_result = {"success": True, "code": code, "state": state}
                        result_ready.set()
                        try:
                            win.destroy()
                        except Exception:
                            pass
            except Exception as e:
                if 'destroyed' not in str(e).lower():
                    logger.info(f"[Vermes API] URL 检查失败: {e}")

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
                            logger.info(f"[Vermes API] ✅ 轮询获取到 code: {code[:10]}...")
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
                logger.info("[Vermes API] OAuth 窗口被用户关闭")
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
        from vermes_cli.web_server import app as fastapi_app
    except Exception as e:
        with open(_startup_log, "a") as _f:
            _f.write(f"[{time.strftime('%H:%M:%S')}] start_server IMPORT ERROR: {type(e).__name__}: {e}\n")
        logger.info(f"[Vermes] 依赖缺失: {e}")
        return

    # 找可用端口
    port = find_available_port(DEFAULT_PORT)
    if port is None:
        logger.info(f"[Vermes] ❌ 错误：9119-9138 端口全部被占用，无法启动后端！")
        return

    # 写入端口文件，让 main() 知道正确的 URL
    try:
        with open(PORT_FILE, "w") as f:
            f.write(str(port))
    except Exception as e:
        logger.info(f"[Vermes] ⚠️ 无法写入端口文件: {e}")

    logger.info(f"[Vermes] 后端启动在端口 {port}")
    global server_instance
    try:
        # 强制启用 agent 模式
        from vermes_cli import web_server as _ws
        _ws._DASHBOARD_EMBEDDED_CHAT_ENABLED = True

        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="info", lifespan="off")
        server_instance = uvicorn.Server(config)
        server_instance.run()
    except Exception as e:
        logger.info(f"[Vermes] ❌ 后端崩溃: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        # Write crash to startup log for post-mortem
        try:
            with open(_startup_log, "a") as f:
                f.write(f"\n[{time.strftime('%H:%M:%S')}] BACKEND CRASH: {type(e).__name__}: {e}\n")
                traceback.print_exc(file=f)
        except Exception:
            pass


def on_dom_ready():
    """窗口 DOM 就绪后调用（pywebview 6.x）。"""
    logger.info("[Vermes] DOM 就绪，窗口已打开。")


def main(port):
    """原生窗口优先，失败回退浏览器。"""
    url = f"http://127.0.0.1:{port}"
    logger.info(f"[Vermes] 打开界面：{url}")
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

        # 读取上次窗口位置
        geo = win_adapter.load_window_geometry(WINDOW_W, WINDOW_H)

        win = webview.create_window(
            title=APP_TITLE,
            html=loading_html,
            x=geo["x"],
            y=geo["y"],
            width=geo["w"],
            height=geo["h"],
            resizable=True,
            text_select=True,
            js_api=VermesAPI(),
        )

        # 窗口关闭时保存位置
        def _on_closing():
            try:
                win_adapter.save_window_geometry(win.x, win.y, win.width, win.height)
            except Exception:
                pass
            win_adapter.remove_tray_icon()

        win.events.closing += lambda: _on_closing()

        # Windows 系统托盘：状态图标 + 退出入口（pywebview 不支持关闭拦截，暂不做最小化到托盘）
        if win_adapter.IS_WINDOWS:
            win_adapter.create_tray_icon(on_show=None, on_quit=lambda: win.destroy())

        def _load_real_url():
            """后台等待服务器就绪后切换到真实页面。"""
            if wait_for_server(port, timeout=20):
                logger.info(f"[Vermes] 后端就绪，加载 {url}")
            else:
                logger.info(f"[Vermes] ⚠️ 后端超时，仍尝试加载 {url}")
            win.load_url(url)

        import threading
        threading.Thread(target=_load_real_url, daemon=True).start()

        # macOS 用 cocoa，Windows 用 edgechromium，其他平台自动选择
        gui = None
        if win_adapter.IS_WINDOWS:
            gui = 'edgechromium'
        # WebView2 数据目录固定到 ~/.vermes/webview_data/
        # 防止更新 ZIP 覆盖时丢失 localStorage（聊天记录、用户偏好）
        storage_path = os.path.expanduser('~/.vermes/webview_data')
        os.makedirs(storage_path, exist_ok=True)
        webview.start(gui=gui, private_mode=False, storage_path=storage_path, debug=False)
        logger.info("[Vermes] 原生窗口已关闭")
        win_adapter.remove_tray_icon()  # 安全兜底（_on_closing 已调过，这里防异常路径漏清理）
        return
    except Exception as e:
        logger.info(f"[Vermes] ❌ 原生窗口失败: {e}")
        logger.info("[Vermes] 请检查 pywebview 是否正确安装: pip install pywebview")

    # 保持进程运行，等待退出/重启信号
    from vermes_cli.shutdown_signal import restart_event

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
            logger.info("[Vermes] 收到重启信号，重启 Gateway...")
            restart_event.clear()
            # 停止当前 uvicorn
            if server_instance:
                server_instance.should_exit = True
                time.sleep(2)  # 等待 uvicorn 优雅关闭
            # 重新启动 uvicorn
            logger.info("[Vermes] Gateway 重启中...")
            import uvicorn
            from vermes_cli.web_server import app as fastapi_app_restart
            from vermes_cli import web_server as _ws_restart
            _ws_restart._DASHBOARD_EMBEDDED_CHAT_ENABLED = True
            config = uvicorn.Config(fastapi_app_restart, host="127.0.0.1", port=port, log_level="info", lifespan="off")
            server_instance = uvicorn.Server(config)
            import threading
            threading.Thread(target=server_instance.run, daemon=True).start()
            logger.info("[Vermes] ✅ Gateway 已重启")
            continue  # 回到等待循环

        # shutdown_event
        logger.info("[Vermes] 收到退出信号，关闭后端...")
        break

    os._exit(0)


def _apply_pending_update_if_any():
    """启动时检查 ~/.vermes/update/pending，如果有待应用的更新就执行。

    使用 update_manager 的原子替换机制：
    1. 复制新版到临时位置
    2. 验证关键文件存在
    3. rename 原子切换
    4. 清理旧版本
    """
    try:
        from vermes_cli.update_manager import apply_pending_update
        apply_pending_update()
    except ImportError:
        # fallback: 如果 update_manager 不可用，使用旧逻辑
        import json
        pending_file = os.path.expanduser('~/.vermes/update/pending.json')
        if not os.path.exists(pending_file):
            return
        try:
            with open(pending_file, 'r') as f:
                pending = json.load(f)
            version = pending.get('version', 'unknown')
            staging = pending.get('staging_path', '')
            logger.info(f"[Vermes Update] 发现待应用更新 v{version}，正在应用...")

            import shutil
            if platform.system() == 'Darwin':
                app_path = os.path.join(staging, 'Vermes.app')
                target = '/Applications/Vermes.app'
                if os.path.exists(app_path):
                    if os.path.exists(target):
                        shutil.rmtree(target)
                    shutil.copytree(app_path, target)
                    logger.info(f"[Vermes Update] ✅ 已更新到 v{version}")
                    subprocess.Popen(['open', target])
                    sys.exit(0)
            elif win_adapter.IS_WINDOWS:
                exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
                for item in os.listdir(staging):
                    src = os.path.join(staging, item)
                    dst = os.path.join(exe_dir, item)
                    if os.path.isdir(src):
                        if os.path.exists(dst):
                            shutil.rmtree(dst)
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                logger.info(f"[Vermes Update] ✅ 已更新到 v{version}")
                subprocess.Popen([os.path.join(exe_dir, 'Vermes.exe')])
                sys.exit(0)

            os.remove(pending_file)
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as e:
            logger.info(f"[Vermes Update] ❌ 应用更新失败: {e}")
            try:
                os.remove(pending_file)
            except Exception:
                pass


def run_gui():
    """Entry point for GUI mode (called from main.py when frozen + no args)."""
    # Parse --no-server flag (connect to existing Gateway instead of starting new one)
    no_server = "--no-server" in sys.argv
    # Parse --port flag
    port = DEFAULT_PORT
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

    if no_server:
        # Skip server start, just open native window
        logger.info(f"[Vermes] --no-server mode, connecting to port {port}")
        if not wait_for_server(port, timeout=10):
            logger.info(f"[Vermes] ERROR: Gateway not running on port {port}")
            sys.exit(1)
        main(port)
        return
    # ── 启动时检查是否有待应用的更新 ──
    _apply_pending_update_if_any()

    # 单例锁：防止多开
    if not acquire_lock():
        # 已有实例在运行，聚焦已有窗口
        existing_port = find_existing_port()
        if existing_port:
            logger.info(f"[Vermes] 已有实例在运行 (port={existing_port})，聚焦窗口...")
            # 跨平台聚焦：Windows 用 Win32 API，Mac 用 open -a
            focused = False
            if win_adapter.IS_WINDOWS:
                focused = win_adapter.focus_existing_window("Vermes")
            else:
                try:
                    subprocess.run(['open', '-a', 'Vermes'], timeout=3,
                                 capture_output=True, check=False)
                    focused = True
                except Exception:
                    pass
            if not focused:
                webbrowser.open(f"http://127.0.0.1:{existing_port}")
        else:
            logger.info("[Vermes] 已有实例在运行，但无法找到端口。")
        sys.exit(0)

    # 启动前检查：WebView2 运行时等
    if not win_adapter.pre_launch_check():
        sys.exit(1)

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
        logger.info("[Vermes] 端口文件未就绪，自动扫描后端端口...")
        for scan_port in range(DEFAULT_PORT, DEFAULT_PORT + 20):
            if wait_for_server(scan_port, timeout=8):
                port = scan_port
                logger.info(f"[Vermes] 发现后端在端口 {port}")
                break

    logger.info(f"[Vermes] 等待后端服务器就绪 (port={port})...")
    if wait_for_server(port, timeout=15):
        logger.info("[Vermes] 后端就绪，打开窗口。")
    else:
        logger.info("[Vermes] 警告：后端未就绪，仍尝试打开窗口。")

    main(port)


if __name__ == "__main__":
    run_gui()
