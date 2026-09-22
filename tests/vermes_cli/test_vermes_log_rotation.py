"""契约测试：vermes_log 日期日志轮转对齐 config（5MB/3份）+ 启动清理。"""

import gzip
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _make_tee(tmp_path, max_mb=5, backup_count=3):
    from vermes_cli.vermes_log import _TeeStream
    p = tmp_path / "vermes_20260922.log"
    tee = _TeeStream(None, p, max_mb=max_mb, backup_count=backup_count)
    return tee, p


def test_tee_rotation_produces_numbered_backups(tmp_path):
    """_rotate 应滚动 .1 -> .2 -> .3，最老删除，与 config backup_count 一致。"""
    tee, p = _make_tee(tmp_path, backup_count=3)
    try:
        # 触发 3 次旋转
        tee._f.write("x" * 100)
        tee._rotate()
        assert p.exists()  # 新文件重新打开
        assert Path(str(p) + ".1").exists()

        tee._f.write("y" * 100)
        tee._rotate()
        assert Path(str(p) + ".2").exists()

        tee._f.write("z" * 100)
        tee._rotate()
        assert Path(str(p) + ".3").exists()

        # 第 4 次旋转：.3 被 .2 顶掉，最多留 3 份
        tee._f.write("w" * 100)
        tee._rotate()
        assert Path(str(p) + ".3").exists()
        assert not Path(str(p) + ".4").exists()
    finally:
        tee._f.close()


def test_tee_uses_config_mb_not_hardcoded_50(tmp_path, monkeypatch):
    """_read_rotation_config 应读 config，而非硬编码 50MB。"""
    from vermes_cli.vermes_log import _read_rotation_config

    max_mb, backup = _read_rotation_config()
    assert max_mb == 5  # config 默认 5MB，非 50
    assert backup == 3


def test_cleanup_gzips_old_logs(tmp_path):
    """启动清理应 gzip >30 天旧日期日志，并删除原文件。"""
    from vermes_cli.vermes_log import _cleanup_old_logs

    old_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y%m%d")

    old_p = tmp_path / f"vermes_{old_date}.log"
    old_p.write_text("old log content")
    new_p = tmp_path / f"vermes_{today}.log"
    new_p.write_text("new log content")

    _cleanup_old_logs(tmp_path, keep_days=30)

    # 旧日志被 gzip 且原文件删除
    assert not old_p.exists()
    assert old_p.with_name(old_p.name + ".gz").exists()
    # 今日日志不动
    assert new_p.exists()


def test_cleanup_skips_already_gzipped(tmp_path):
    """已 gzip 的日志不应重复压缩。"""
    from vermes_cli.vermes_log import _cleanup_old_logs

    old_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    old_p = tmp_path / f"vermes_{old_date}.log"
    old_p.write_text("old log content")
    gz_path = old_p.with_name(old_p.name + ".gz")
    with gzip.open(gz_path, "wb") as f:
        f.write(b"already compressed")
    old_p.unlink()  # 模拟已压缩后的状态：只剩 .gz

    _cleanup_old_logs(tmp_path, keep_days=30)

    # 不报错，.gz 仍在
    assert gz_path.exists()
