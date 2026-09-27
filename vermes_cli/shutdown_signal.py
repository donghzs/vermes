"""共享退出/重启信号，避免 web_server.py 与 gui_app.py 循环导入。

- shutdown_event: 完全退出应用（关壳）
- restart_event: 重启 gateway 进程（不关壳，Agent 框架更新用）
- shutting_down: 已进入关闭流程、不再接受新业务请求（P0 僵尸态根治）

2026-09-27 补充 `shutting_down` 的动机（真实事故）：
    原先 SIGTERM 只置 shutdown_event，不停 uvicorn。进程被非守护线程
    （telegram 轮询 / agent 线程 / kanban notifier）吊住无法退出，端口继续
    accept，但 Python 解释器已进入 shutdown → 任何往线程池排任务的请求都抛
    `RuntimeError: cannot schedule new futures after interpreter shutdown`
    → 一个 delta 都发不出 → 前端误报「回复为空，请重试或更换模型」。
    用户换模型、重试都无效，因为后端根本没在正常工作。
    `shutting_down` 让请求在进入业务代码之前就被挡掉，返回可诊断的 503。
"""
import threading

shutdown_event = threading.Event()
restart_event = threading.Event()

# 已进入关闭流程：listener 可能还开着，但解释器随时进入 shutdown，
# 此刻接受新请求必然失败。置位后 web_server 的关闭守卫直接返回 503。
shutting_down = threading.Event()


def mark_shutting_down():
    """进入关闭流程时调用：置 shutting_down + shutdown_event。"""
    shutting_down.set()
    shutdown_event.set()


def is_shutting_down():
    """是否处于"正在关闭但进程还没死"的窗口期。"""
    return shutting_down.is_set()
