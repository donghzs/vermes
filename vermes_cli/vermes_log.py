"""
Vermes — 输出双写模块

在 Vermes 层捕获所有 logger.info() 输出和 logging 日志，同时写入：
  1. 原始 stdout/stderr（用户体验不变）
  2. ~/.vermes/logs/vermes_YYYYMMDD.log（完整日志链）

策略 A 实现：不修改 vermes_cli 上游代码，在 Vermes 入口层做 stdout/stderr 封装。
"""
import gzip
import logging
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger(__name__)


_DATE_LOG_RE = re.compile(r"^vermes_(\d{8})\.log(?:\.\d+)?$")


def _read_rotation_config() -> tuple[int, int]:
    """从 config.yaml 读轮转参数（max_size_mb / backup_count），失败回落 5MB/3 份。

    延迟 import 避免与 vermes_cli.config 循环依赖。
    """
    try:
        from vermes_cli.config import cfg_get, load_config
        cfg = load_config() or {}
        max_mb = cfg_get(cfg, "logging", "max_size_mb", default=5) or 5
        backup = cfg_get(cfg, "logging", "backup_count", default=3) or 3
        return int(max_mb), int(backup)
    except Exception:
        return 5, 3


class _TeeStream:
    """Duplicate writes to stdout + log file, preserving user visibility."""

    def __init__(self, original, log_path: Path, max_mb: int = 5, backup_count: int = 3):
        self.original = original
        self.log_path = log_path
        self.max_bytes = max_mb * 1024 * 1024
        self.backup_count = max(1, backup_count)
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
        """标准滚动轮转：.1 -> .2 -> ... -> .backup_count，最老的删除。"""
        try:
            self._f.close()
            for i in range(self.backup_count - 1, 0, -1):
                src = Path(str(self.log_path) + f".{i}")
                dst = Path(str(self.log_path) + f".{i + 1}")
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
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


def _cleanup_old_logs(log_dir: Path, keep_days: int = 30) -> None:
    """启动时清理过期日志：超过 keep_days 天的日期日志 gzip 压缩。

    只 gzip（压缩后原文件删除），不直接删文件——压缩后体积通常只剩 5~10%，
    且可恢复，比硬删安全。历史 2.2GB 存量会在此处一次性收敛。
    """
    try:
        cutoff = datetime.now() - timedelta(days=keep_days)
        for p in sorted(log_dir.glob("vermes_*.log*")):
            m = _DATE_LOG_RE.match(p.name)
            if not m:
                continue
            try:
                fdate = datetime.strptime(m.group(1), "%Y%m%d")
            except ValueError:
                continue
            if fdate < cutoff and not p.name.endswith(".gz"):
                gz_path = p.with_name(p.name + ".gz")
                if gz_path.exists():
                    continue  # 已压缩
                try:
                    with open(p, "rb") as fin, gzip.open(gz_path, "wb", 9) as fout:
                        fout.write(fin.read())
                    p.unlink()
                except Exception:
                    pass  # 单文件清理失败不影响启动
    except Exception:
        pass


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

    max_mb, backup_count = _read_rotation_config()

    today = datetime.now().strftime("%Y%m%d")
    log_path = log_dir / f"vermes_{today}.log"

    # 启动清理：>30 天旧日志 gzip（防再堆到 GB）
    _cleanup_old_logs(log_dir)

    # 1. 双写 stdout
    if sys.stdout is not None and not getattr(sys.stdout, "_vermes_tee", False):
        sys.stdout = _TeeStream(sys.stdout, log_path, max_mb=max_mb, backup_count=backup_count)
        sys.stdout._vermes_tee = True  # type: ignore

    # 2. 双写 stderr（走同一个日志文件）
    if sys.stderr is not None and not getattr(sys.stderr, "_vermes_tee", False):
        sys.stderr = _TeeStream(sys.stderr, log_path, max_mb=max_mb, backup_count=backup_count)
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

        # 文件 handler — 完整日志链（轮转，对齐 config max_size_mb/backup_count）
        fh = RotatingFileHandler(
            log_path, maxBytes=max_mb * 1024 * 1024, backupCount=backup_count,
            encoding="utf-8",
        )
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
