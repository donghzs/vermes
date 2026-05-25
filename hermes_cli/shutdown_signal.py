"""共享退出信号，避免 web_server.py 与 gui_app.py 循环导入。"""
import threading

shutdown_event = threading.Event()
