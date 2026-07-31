import pytest
from pathlib import Path

from vermes_cli.config import (
    get_config_path,
    read_raw_config,
    save_config,
    is_managed,
)
from vermes_cli.backup import create_quick_snapshot, list_quick_snapshots


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _config_only_snapshots():
    return [
        s for s in list_quick_snapshots()
        if (s.get("files") or {}).get("config.yaml") is not None
    ]


def test_corrupt_config_restores_from_snapshot():
    """P0-1 读侧：config.yaml 损坏时应从 config-only 快照恢复，而非静默丢用户覆盖。"""
    cfg_path = get_config_path()
    # 1) 写一份有效配置（含用户覆盖）
    _write(cfg_path, "model:\n  default: gpt-4o\naux_providers:\n  - id: p1\n")
    # 2) 对 config.yaml 做轻量快照（不含 state.db）
    snap_id = create_quick_snapshot(label="pre-config-write", files=("config.yaml",))
    assert snap_id is not None
    assert _config_only_snapshots(), "应存在含 config.yaml 的快照"

    # 3) 损坏 config.yaml（非法 YAML / 编码乱码）
    _write(cfg_path, "model: : : : broken <<<<\n\t\xff\xfe\xff\n")

    # 4) 读取应自动从快照恢复有效配置
    data = read_raw_config()
    assert data.get("model", {}).get("default") == "gpt-4o", f"应从快照恢复用户配置，实际: {data}"
    assert "aux_providers" in data


def test_config_only_snapshot_excludes_state_db():
    """P0-1：config-only 快照不应把 state.db 一起拷（避免写前备份过重）。"""
    cfg_path = get_config_path()
    _write(cfg_path, "model:\n  default: claude-3\n")
    create_quick_snapshot(label="pre-config-write", files=("config.yaml",))
    for s in _config_only_snapshots():
        assert "state.db" not in (s.get("files") or {}), "config-only 快照不应含 state.db"


@pytest.mark.skipif(is_managed(), reason="managed 部署禁止写 config，跳过写侧验证")
def test_save_config_creates_config_only_snapshot():
    """P0-1 写侧：save_config 覆盖写盘前应快照现有 config.yaml。"""
    cfg_path = get_config_path()
    # 先写一个已存在的 config（模拟真实"覆盖写"场景，快照保留覆盖前的版本）
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("model:\n  default: old-model\n", encoding="utf-8")
    save_config({"model": {"default": "claude-3"}, "aux_providers": []})
    assert _config_only_snapshots(), "save_config 应写前创建 config-only 快照"
