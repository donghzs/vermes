"""共享退出/重启信号，避免 web_server.py 与 gui_app.py 循环导入。

- shutdown_event: 完全退出应用（关壳）
- restart_event: 重启 gateway 进程（不关壳，Agent 框架更新用）
"""
import threading

shutdown_event = threading.Event()
restart_event = threading.Event()
