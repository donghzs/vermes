"""
Windows 适配层 — 让 Vermes 在 Windows 上达到 Mac 级体验。

功能：
1. WebView2 运行时检测 + 友好下载提示
2. 单例聚焦（Win32 API，替代 Mac 的 open -a）
3. DPI 感知（避免高分屏模糊）
4. 窗口位置/大小持久化
5. 系统托盘（最小化到托盘）
6. 进程管理（CREATE_NO_WINDOW、优雅关闭）
7. 编码修复（强制 UTF-8）

设计原则：
- 零硬依赖：所有 Win32 API 通过 ctypes 调用，pystray 可选
- 非 Windows 平台所有函数 no-op，不报错
- 单文件、自包含，gui_app.py 只需 import 这一个模块
"""

import sys
import os
import json
import platform

IS_WINDOWS = sys.platform == "win32"

# ── Win32 API 通过 ctypes 调用（零依赖） ──
if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # Win32 常量
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SW_RESTORE = 9
    SW_SHOW = 5
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    SM_CXSCREEN = 0
    SM_CYSCREEN = 1
    PROCESS_DPI_UNAWARE = 0
    PROCESS_SYSTEM_DPI_AWARE = 1
    PROCESS_PER_MONITOR_DPI_AWARE = 2


# ═══════════════════════════════════════════
# 1. WebView2 运行时检测
# ═══════════════════════════════════════════

def check_webview2_runtime() -> dict:
    """
    检测 WebView2 Runtime 是否已安装。
    返回: {"available": bool, "version": str|None, "error": str|None}
    """
    if not IS_WINDOWS:
        return {"available": True, "version": "N/A (not Windows)", "error": None}

    try:
        import winreg

        # WebView2 Evergreen 注册表路径
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BEE-13A6279B0900}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BEE-13A6279B0900}"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BEE-13A6279B0900}"),
        ]

        for hkey, path in reg_paths:
            try:
                key = winreg.OpenKey(hkey, path)
                version, _ = winreg.QueryValueEx(key, "pv")
                winreg.CloseKey(key)
                if version and version != "0.0.0.0":
                    return {"available": True, "version": version, "error": None}
            except FileNotFoundError:
                continue
            except Exception:
                continue

        return {
            "available": False,
            "version": None,
            "error": "WebView2 Runtime 未安装"
        }

    except ImportError:
        # winreg 不可用（极少见）
        return {"available": True, "version": "unknown", "error": None}
    except Exception as e:
        return {"available": True, "version": "unknown", "error": str(e)}


def show_webview2_missing_dialog():
    """WebView2 缺失时弹出 MessageBox 提示用户下载。"""
    if not IS_WINDOWS:
        return

    download_url = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
    message = (
        "Vermes 需要 Microsoft Edge WebView2 运行时才能运行。\n\n"
        "您的电脑上尚未安装此组件。\n\n"
        "点击「确定」将打开下载页面，安装后重新启动 Vermes 即可。"
    )

    # MB_OKCANCEL=0x01 | MB_ICONWARNING=0x30 | MB_TOPMOST=0x40000 | MB_SYSTEMMODAL=0x1000
    result = user32.MessageBoxW(
        0, message, "Vermes - 需要安装组件",
        0x01 | 0x30 | 0x40000 | 0x1000
    )
    if result == 1:  # IDOK
        import webbrowser
        webbrowser.open(download_url)


# ═══════════════════════════════════════════
# 2. 单例聚焦（Win32 API）
# ═══════════════════════════════════════════

def _find_window_by_title_partial(title_partial: str):
    """通过标题模糊匹配查找窗口句柄。"""
    if not IS_WINDOWS:
        return None

    found_hwnd = None

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd, lparam):
        nonlocal found_hwnd
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_partial.lower() in buf.value.lower():
                    found_hwnd = hwnd
                    return False  # 停止枚举
        return True

    user32.EnumWindows(enum_callback, 0)
    return found_hwnd


def focus_existing_window(title_partial: str = "Vermes") -> bool:
    """
    查找并聚焦已有的 Vermes 窗口。
    返回 True 表示成功聚焦。
    """
    if not IS_WINDOWS:
        return False

    hwnd = _find_window_by_title_partial(title_partial)
    if not hwnd:
        return False

    try:
        # 如果窗口最小化，先恢复
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # 强制前台
        # 先短暂置顶再取消，绕过 Windows 前台锁定
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(hwnd)

        return True
    except Exception:
        return False


# ═══════════════════════════════════════════
# 3. DPI 感知
# ═══════════════════════════════════════════

def set_dpi_awareness():
    """
    设置进程 DPI 感知，避免高分屏模糊。
    优先 Per-Monitor V2，回退 System DPI Aware。
    """
    if not IS_WINDOWS:
        return

    try:
        # Windows 10 1703+ 支持 Per-Monitor V2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass

    try:
        # Windows 8.1+ 支持 Per-Monitor V1
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass

    try:
        # Windows Vista+ 支持 System DPI Aware
        user32.SetProcessDPIAware()
    except Exception:
        pass


def get_screen_size() -> tuple:
    """获取主屏幕分辨率。"""
    if IS_WINDOWS:
        return (user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))
    return (1920, 1080)


# ═══════════════════════════════════════════
# 4. 窗口位置持久化
# ═══════════════════════════════════════════

_geometry_file = os.path.expanduser("~/.vermes/window_geometry.json")


def save_window_geometry(x: int, y: int, w: int, h: int, maximized: bool = False):
    """保存窗口位置和大小到文件。"""
    try:
        os.makedirs(os.path.dirname(_geometry_file), exist_ok=True)
        data = {"x": x, "y": y, "w": w, "h": h, "maximized": maximized}
        with open(_geometry_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def load_window_geometry(default_w=1200, default_h=800) -> dict:
    """
    加载上次的窗口位置。
    返回: {"x": int|None, "y": int, "w": int, "h": int, "maximized": bool}
    """
    try:
        if os.path.exists(_geometry_file):
            with open(_geometry_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 验证坐标在屏幕范围内
            sw, sh = get_screen_size()
            x = data.get("x")
            y = data.get("y", 0)
            w = data.get("w", default_w)
            h = data.get("h", default_h)
            if x is not None and (x < -100 or x > sw + 100):
                x = None
            if y < -100 or y > sh + 100:
                y = 0
            return {"x": x, "y": y, "w": w, "h": h, "maximized": data.get("maximized", False)}
    except Exception:
        pass
    # 默认：屏幕居中
    sw, sh = get_screen_size()
    return {
        "x": (sw - default_w) // 2,
        "y": (sh - default_h) // 2,
        "w": default_w,
        "h": default_h,
        "maximized": False,
    }


# ═══════════════════════════════════════════
# 5. 系统托盘（pystray 可选）
# ═══════════════════════════════════════════

_tray_icon = None
_tray_thread = None


def create_tray_icon(on_show=None, on_quit=None):
    """
    创建系统托盘图标（仅 Windows，需要 pystray）。
    on_show: 点击「显示窗口」的回调
    on_quit: 点击「退出」的回调
    """
    global _tray_icon

    if not IS_WINDOWS:
        return False

    try:
        import pystray
        from PIL import Image
    except ImportError:
        return False

    # 创建一个简单的绿色圆形图标
    def _create_icon_image():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=(34, 197, 94))
        # V 字母
        draw.text((18, 10), "V", fill="white")
        return img

    def _on_quit(icon, item):
        if on_quit:
            on_quit()
        icon.stop()

    menu_items = []
    if on_show:
        def _on_show(icon, item):
            on_show()
        menu_items.append(pystray.MenuItem("显示 Vermes", _on_show, default=True))
        menu_items.append(pystray.Menu.SEPARATOR)
    menu_items.append(pystray.MenuItem("退出", _on_quit))
    menu = pystray.Menu(*menu_items)

    _tray_icon = pystray.Icon(
        name="Vermes",
        icon=_create_icon_image(),
        title="Vermes - AI Agent",
        menu=menu,
    )

    import threading
    global _tray_thread
    _tray_thread = threading.Thread(target=_tray_icon.run, daemon=True)
    _tray_thread.start()
    return True


def remove_tray_icon():
    """移除系统托盘图标。"""
    global _tray_icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception:
            pass
        _tray_icon = None


# ═══════════════════════════════════════════
# 6. 进程管理
# ═══════════════════════════════════════════

def get_creation_flags():
    """返回 subprocess creationflags，隐藏子进程控制台窗口。"""
    if IS_WINDOWS:
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def kill_process_tree(pid: int):
    """Windows 上杀死进程树（包括子进程）。"""
    if not IS_WINDOWS:
        return

    try:
        # 用 taskkill /T 杀死进程树
        import subprocess
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            creationflags=get_creation_flags(),
            timeout=5,
        )
    except Exception:
        pass


# ═══════════════════════════════════════════
# 7. 编码修复
# ═══════════════════════════════════════════

def force_utf8_env():
    """强制 Windows 使用 UTF-8 编码。"""
    if not IS_WINDOWS:
        return

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")

    # 设置控制台代码页为 UTF-8
    try:
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass


# ═══════════════════════════════════════════
# 8. 一键初始化
# ═══════════════════════════════════════════

def init():
    """
    Windows 适配层一键初始化。
    在 gui_app.py 最开头调用一次即可。
    """
    if not IS_WINDOWS:
        return

    force_utf8_env()
    set_dpi_awareness()


def pre_launch_check() -> bool:
    """
    启动前检查。返回 True 表示一切就绪。
    WebView2 检查已禁用 — pywebview 自己会处理缺失情况。
    """
    return True
