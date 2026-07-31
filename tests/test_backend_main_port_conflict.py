"""A.4.2 — 端口冲突不应触发自动回滚看门狗。

核心回归点：当后端因端口被占用（EADDRINUSE）启动失败时，backend_main
必须把它识别为"运行期环境问题"而非"构建损坏"，从而：
  (a) 不写 crash marker → 下次启动不会静默回滚到旧版本代码；
  (b) 仍置位 shutdown_event → 交由 Electron 外壳重启。
本测试只覆盖可单测的判定函数 `_is_port_conflict`。
"""

import backend_main as bm


def test_linux_eaddrinuse_errno():
    # Linux EADDRINUSE = 98
    exc = OSError(98, "Address already in use")
    assert exc.errno == 98
    assert bm._is_port_conflict(exc) is True


def test_macos_eaddrinuse_errno():
    # macOS EADDRINUSE = 48
    exc = OSError(48, "Address already in use")
    assert exc.errno == 48
    assert bm._is_port_conflict(exc) is True


def test_eaddrinuse_by_message_only():
    exc = OSError("[Errno 98] address already in use")
    assert bm._is_port_conflict(exc) is True


def test_windows_socket_in_use_message():
    exc = OSError("Only one usage of each socket address (protocol/network address/port) is normally permitted")
    assert bm._is_port_conflict(exc) is True


def test_permission_error_not_port_conflict():
    # EACCES = 13 → 真实构建/权限问题，应触发回滚，不视为端口冲突
    exc = OSError(13, "Permission denied")
    assert bm._is_port_conflict(exc) is False


def test_arbitrary_exception_not_port_conflict():
    exc = ValueError("unexpected config shape")
    assert bm._is_port_conflict(exc) is False


def test_none_is_not_port_conflict():
    assert bm._is_port_conflict(None) is False


def test_eaddrinuse_no_message_still_detected():
    # 仅 errno，无消息文本
    exc = OSError(98, "")
    assert bm._is_port_conflict(exc) is True
