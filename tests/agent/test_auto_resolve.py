"""阶段A 行动环闭合测试：auto_resolve_eligible_flags 置信度分级。

核心断言：
  - ≥0.9 duplicate + source=skill → 自动 demote
  - ≥0.9 duplicate + source!=skill → 不自动处理
  - ≥0.85 outdated → 自动 demote
  - <0.9 duplicate → 不自动处理
  - contradiction/scope_creep → 不自动处理
  - 处理后 lifecycle_tag=ephemeral（安全护栏）
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.memory_reflection import auto_resolve_eligible_flags  # noqa: E402


_MEMORIES_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    source TEXT, layer TEXT, type TEXT, scope TEXT,
    pointer TEXT, fts_content TEXT, updated_at TEXT,
    access_count INTEGER DEFAULT 0, lifecycle_tag TEXT DEFAULT 'reference'
)
"""

_FLAGS_DDL = """
CREATE TABLE IF NOT EXISTS memory_flags (
    id INTEGER PRIMARY KEY,
    memory_id TEXT, flag_type TEXT, confidence REAL,
    evidence TEXT, status TEXT DEFAULT 'open',
    created_at TEXT, source TEXT, resolution TEXT, resolved_at TEXT
)
"""


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(_MEMORIES_DDL)
    conn.execute(_FLAGS_DDL)
    conn.commit()

    # 插入记忆
    conn.execute(
        "INSERT INTO memories (id, source, layer, lifecycle_tag, fts_content) "
        "VALUES (1, 'skill', 'procedural', 'reference', '技能描述拷贝')",
    )
    conn.execute(
        "INSERT INTO memories (id, source, layer, lifecycle_tag, fts_content) "
        "VALUES (2, 'note', 'note', 'reference', '真实用户偏好：战略定位')",
    )
    conn.execute(
        "INSERT INTO memories (id, source, layer, lifecycle_tag, fts_content) "
        "VALUES (3, 'l1_auto', 'note', 'reference', '自动抽取的密码信息')",
    )
    conn.commit()
    yield path, conn
    conn.close()
    Path(path).unlink()


def _monkey_db(monkeypatch, path):
    monkeypatch.setattr(
        "agent.memory_fabric._get_index_db",
        lambda: path,
    )


def test_auto_resolve_high_confidence_duplicate_skill(monkeypatch, temp_db):
    """≥0.9 duplicate + source=skill → 自动 demote"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (10, '1', 'duplicate', 0.95, '技能描述重复', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 1

    flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=10").fetchone()
    assert flag[0] == "resolved"
    assert flag[1] == "demote"

    tag = conn.execute("SELECT lifecycle_tag FROM memories WHERE id=1").fetchone()[0]
    assert tag == "ephemeral"


def test_auto_resolve_does_not_process_non_skill_duplicate(monkeypatch, temp_db):
    """≥0.9 duplicate + source!=skill → 不自动处理"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (11, '2', 'duplicate', 0.95, '真实偏好重复', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 0

    flag = conn.execute("SELECT status FROM memory_flags WHERE id=11").fetchone()
    assert flag[0] == "open"


def test_auto_resolve_outdated_high_confidence(monkeypatch, temp_db):
    """≥0.85 outdated → 自动 demote"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (12, '3', 'outdated', 0.90, '过时信息', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 1

    flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=12").fetchone()
    assert flag[0] == "resolved"
    assert flag[1] == "demote"


def test_auto_resolve_does_not_process_low_confidence(monkeypatch, temp_db):
    """<0.9 duplicate → 不自动处理"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (13, '1', 'duplicate', 0.80, '低置信度重复', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 0


def test_auto_resolve_does_not_process_contradiction_or_scope_creep(monkeypatch, temp_db):
    """contradiction / scope_creep → 不自动处理"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (14, '2', 'contradiction', 0.9, '矛盾', 'open', 't')",
    )
    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (15, '2', 'scope_creep', 0.9, '范围漂移', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 0


# ── 配置边界护栏：config 写入 0 必须回落默认，不可覆盖 ──────────────
# Phase 1 策略外置后用 `>= 0` 校验，若用户在 config.yaml 写 0 会：
#   - cluster_min_interval=0 → 撤掉死亡间隔下限 → 复活 Bug 1（簇在末次事件后毫秒级被判死）
#   - duplicate/outdated/merge_cleanup=0 → 全量 flag 被自动降级 / 删除
# 修复后改用 `> 0`，0 一律回落硬编码默认。

def test_auto_resolve_config_zero_values_fall_back_to_defaults(monkeypatch):
    """memory.autoResolve 全部写 0 → 一律回落默认（防 0 注入）。"""
    import vermes_cli.config as vc_cfg

    monkeypatch.setattr(
        vc_cfg, "load_config",
        lambda: {"memory": {"autoResolve": {
            "duplicate": 0, "outdated": 0,
            "cluster_min_interval": 0, "merge_cleanup": 0,
        }}},
    )
    from agent.memory_reflection import _load_auto_resolve_config

    cfg = _load_auto_resolve_config()
    assert cfg["duplicate"] == 0.9
    assert cfg["outdated"] == 0.85
    assert cfg["cluster_min_interval"] == 60
    assert cfg["merge_cleanup"] == 0.7


def test_auto_resolve_config_positive_values_accepted(monkeypatch):
    """memory.autoResolve 写合法正值 → 被采纳。"""
    import vermes_cli.config as vc_cfg

    monkeypatch.setattr(
        vc_cfg, "load_config",
        lambda: {"memory": {"autoResolve": {
            "duplicate": 0.95, "outdated": 0.80,
            "cluster_min_interval": 120, "merge_cleanup": 0.6,
        }}},
    )
    from agent.memory_reflection import _load_auto_resolve_config

    cfg = _load_auto_resolve_config()
    assert cfg["duplicate"] == 0.95
    assert cfg["outdated"] == 0.80
    assert cfg["cluster_min_interval"] == 120
    assert cfg["merge_cleanup"] == 0.6


def test_cluster_min_interval_zero_falls_back(monkeypatch):
    """cluster_min_interval_s=0 → 回落 60.0（防 Bug 1 复活）。"""
    import vermes_cli.config as vc_cfg

    monkeypatch.setattr(
        vc_cfg, "load_config",
        lambda: {"memory": {"autoResolve": {"cluster_min_interval_s": 0}}},
    )
    from agent.cluster_lifecycle import ClusterLifecycleManager

    assert ClusterLifecycleManager._read_min_interval() == 60.0


def test_cluster_min_interval_positive_accepted(monkeypatch):
    """cluster_min_interval_s=120 → 被采纳。"""
    import vermes_cli.config as vc_cfg

    monkeypatch.setattr(
        vc_cfg, "load_config",
        lambda: {"memory": {"autoResolve": {"cluster_min_interval_s": 120}}},
    )
    from agent.cluster_lifecycle import ClusterLifecycleManager

    assert ClusterLifecycleManager._read_min_interval() == 120.0
