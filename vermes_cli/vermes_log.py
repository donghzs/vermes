"""
Vermes — 输出双写模块

在 Vermes 层捕获所有 logger.info() 输出和 logging 日志，同时写入：
  1. 原始 stdout/stderr（用户体验不变）
  2. ~/.vermes/logs/vermes_YYYYMMDD.log（完整日志链）

策略 A 实现：不修改 vermes_cli 上游代码，在 Vermes 入口层做 stdout/stderr 封装。
"""
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)



class _TeeStream:
    """Duplicate writes to stdout + log file, preserving user visibility."""

    def __init__(self, original, log_path: Path, max_mb: int = 50):
        self.original = original
        self.log_path = log_path
        self.max_bytes = max_mb * 1024 * 1024
        self._lock = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(log_path, "a", buffering=1)  # line-buffered
        if original is not None:
            original.flush()

    def write(self, data):
        with self._lock:
            if self.original is not None:
                self.original.write(data)
            try:
                self._f.write(data)
                if self._f.tell() > self.max_bytes:
                    self._rotate()
            except Exception:
                pass  # 日志写失败不影响主流程

    def flush(self):
        if self.original is not None:
            self.original.flush()
        with self._lock:
            try:
                self._f.flush()
            except Exception:
                pass

    def _rotate(self):
        try:
            self._f.close()
            bak = Path(str(self.log_path) + ".1")
            if bak.exists():
                bak.unlink()
            self.log_path.rename(bak)
            self._f = open(self.log_path, "a", buffering=1)
        except Exception:
            self._f = open(self.log_path, "a", buffering=1)

    # 透传所有其他属性到原始流
    def __getattr__(self, name):
        return getattr(self.original, name)

    def __del__(self):
        try:
            self._f.close()
        except Exception:
            pass


_installed = False


def install(log_dir: str | Path | None = None) -> Path:
    """安装 stdout/stderr 双写 + logging 文件输出。幂等，多次调用无副作用。

    Returns:
        日志文件路径 (Path)
    """
    global _installed
    if _installed and getattr(sys.stdout, "_vermes_tee", False):
        return _installed  # 已安装

    if log_dir is None:
        log_dir = Path.home() / ".vermes" / "logs"
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    log_path = log_dir / f"vermes_{today}.log"

    # 1. 双写 stdout
    if sys.stdout is not None and not getattr(sys.stdout, "_vermes_tee", False):
        sys.stdout = _TeeStream(sys.stdout, log_path)
        sys.stdout._vermes_tee = True  # type: ignore

    # 2. 双写 stderr（走同一个日志文件）
    if sys.stderr is not None and not getattr(sys.stderr, "_vermes_tee", False):
        sys.stderr = _TeeStream(sys.stderr, log_path)
        sys.stderr._vermes_tee = True  # type: ignore

    # 3. 配置 Python logging 模块：同时输出到 stdout + 文件
    root = logging.getLogger()
    if not any(isinstance(h, _TeeLogHandler) for h in root.handlers):
        root.setLevel(logging.DEBUG)

        # stdout handler — 用户可见
        sh = logging.StreamHandler(sys.__stdout__)
        sh.setLevel(logging.INFO)
        sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(sh)

        # 文件 handler — 完整日志链
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d: %(message)s"
        ))
        root.addHandler(fh)

    _installed = log_path
    return log_path


class _TeeLogHandler(logging.Handler):
    """标记 handler（占位），用于 install() 幂等性检测。"""
    pass


# ── 清理函数 ──
def get_log_path() -> Path | None:
    """返回当前活跃的日志文件路径，未安装时返回 None。"""
    if isinstance(_installed, Path):
        return _installed
    return None


# ── 自动安装：import 时即生效 ──
install()
