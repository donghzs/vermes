"""Process-level gateway watchdog (L3/L4 · 2026-09-25).

Hermes 2026-09-25 定位：gateway 可 100% CPU / 事件循环被占 / :9120 可达无响应 /
SIGTERM 被忽略，只能 SIGKILL；Electron 崩溃自愈（``code !== 0``）才会拉起。

本模块跑一条 daemon 线程，两路判据：

1. **CPU 自旋**：进程 CPU > 90% 连续 N 次采样（默认 30s×3 ≈ 90s）
2. **控制面失联**：``127.0.0.1:9120`` 连续 3 次探测失败/超时

命中即 ``os._exit(70)`` —— 非零退出码喂给 Electron 的崩溃自愈；命令行跑的
gateway 由 systemd/launchd 策略接管。**故意不走 SIGTERM**（被占死的事件循环
可能吞掉信号）。

注意：Python GIL 被纯计算占满时，本线程也可能抢不到 GIL。那是
「必须外部看门狗」的边界——Electron 侧健康探测仍兜底。本进程内看门狗
主要抓 **IO 阻塞 / 死锁 / 事件循环被 async 阻塞** 这类（昨晚的
``fut.result(timeout=30)`` 形状）。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 判据阈值（默认 30s × 3）
DEFAULT_INTERVAL = 30.0
DEFAULT_CPU_LIMIT = 0.90
DEFAULT_STRIKES = 3
DEFAULT_CONTROL_PORT = 9120
WATCHDOG_EXIT_CODE = 70


def _self_cpu_seconds() -> float:
    """Process CPU seconds (user+sys). Works on macOS/Linux without psutil."""
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF)
        return float(r.ru_utime + r.ru_stime)
    except Exception:
        # Fallback: os.times() is process-wide on some platforms
        t = os.times()
        return float(t.user + t.system)


def _probe_control_server(port: int, timeout: float = 2.0) -> bool:
    """True if 127.0.0.1:port accepts a TCP connection within *timeout*."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            # 有响应字节即可（不要求 HTTP 语义完整）——「可达但永不应答」会卡在 recv
            s.settimeout(timeout)
            data = s.recv(16)
            return bool(data) or True  # 连上并收到 EOF/数据都算活
    except Exception:
        return False


class GatewayWatchdog:
    """Daemon-thread watchdog. See module docstring."""

    def __init__(
        self,
        *,
        interval: float = DEFAULT_INTERVAL,
        cpu_limit: float = DEFAULT_CPU_LIMIT,
        strikes: int = DEFAULT_STRIKES,
        control_port: int | None = None,
        probe: Callable[[], bool] | None = None,
        cpu_fn: Callable[[], float] | None = None,
        on_trigger: Callable[[str], None] | None = None,
        enabled: bool = True,
    ) -> None:
        self.interval = interval
        self.cpu_limit = cpu_limit
        self.strikes = strikes
        if control_port is None:
            control_port = int(os.environ.get("VERMES_GATEWAY_CONTROL_PORT", str(DEFAULT_CONTROL_PORT)))
        self.control_port = control_port
        self._probe = probe or (lambda: _probe_control_server(self.control_port))
        self._cpu_fn = cpu_fn or _self_cpu_seconds
        self._on_trigger = on_trigger
        self.enabled = enabled
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cpu_strikes = 0
        self._control_strikes = 0
        self._last_cpu: Optional[float] = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="vermes-gateway-watchdog", daemon=True
        )
        self._thread.start()
        logger.info(
            "Gateway watchdog started (interval=%.0fs cpu>=%.0f%% x%d, control :%d)",
            self.interval, self.cpu_limit * 100, self.strikes, self.control_port,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1)
            self._thread = None

    def _trigger(self, reason: str) -> None:
        logger.critical("Gateway watchdog FIRING: %s — exiting %d for supervisor respawn",
                        reason, WATCHDOG_EXIT_CODE)
        try:
            from gateway.status import write_runtime_status
            write_runtime_status(
                exit_reason=f"watchdog: {reason}",
                restart_requested=True,
                error_code="watchdog",
                error_message=reason,
            )
        except Exception as exc:
            logger.warning("watchdog: could not write status: %r", exc)
        if self._on_trigger is not None:
            try:
                self._on_trigger(reason)
            except Exception:
                pass
        os._exit(WATCHDOG_EXIT_CODE)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            # CPU fraction over the last interval
            try:
                now = self._cpu_fn()
                if self._last_cpu is not None:
                    frac = max(0.0, (now - self._last_cpu) / self.interval)
                    if frac >= self.cpu_limit:
                        self._cpu_strikes += 1
                        logger.warning(
                            "watchdog: process CPU %.0f%% (strike %d/%d)",
                            frac * 100, self._cpu_strikes, self.strikes,
                        )
                    else:
                        self._cpu_strikes = 0
                self._last_cpu = now
            except Exception as exc:
                logger.debug("watchdog: cpu sample failed: %r", exc)

            try:
                alive = bool(self._probe())
            except Exception as exc:
                logger.debug("watchdog: control probe raised: %r", exc)
                alive = False
            if not alive:
                self._control_strikes += 1
                logger.warning(
                    "watchdog: control :%d unresponsive (strike %d/%d)",
                    self.control_port, self._control_strikes, self.strikes,
                )
            else:
                self._control_strikes = 0

            if self._cpu_strikes >= self.strikes:
                self._trigger(
                    f"CPU >{self.cpu_limit:.0%} for {self.strikes * self.interval:.0f}s"
                )
            if self._control_strikes >= self.strikes:
                self._trigger(
                    f"control :{self.control_port} unresponsive x{self.strikes}"
                )
