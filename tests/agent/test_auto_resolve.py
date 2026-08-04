"""阶段A 行动环闭合测试：auto_resolve_eligible_flags 置信度分级。

核心断言：
  - ≥0.7 duplicate + source=skill → 自动 demote
  - ≥0.7 duplicate + source!=skill → 不自动处理
  - ≥0.6 outdated → 自动 demote（阈值放宽以匹配 R2 实际置信度分布，避免 1600+ flag 堆积）
  - <0.7 duplicate → 不自动处理
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
    """≥0.7 duplicate + source=skill → 自动 demote"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (10, '1', 'duplicate', 0.75, '技能描述重复', 'open', 't')",
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
    """≥0.7 duplicate + source!=skill → 不自动处理"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (11, '2', 'duplicate', 0.75, '真实偏好重复', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 0

    flag = conn.execute("SELECT status FROM memory_flags WHERE id=11").fetchone()
    assert flag[0] == "open"


def test_auto_resolve_outdated_high_confidence(monkeypatch, temp_db):
    """≥0.6 outdated → 自动 demote（新阈值，匹配 R2 实际分布）"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (12, '3', 'outdated', 0.65, '过时信息', 'open', 't')",
    )
    conn.commit()

    n = auto_resolve_eligible_flags()
    assert n == 1

    flag = conn.execute("SELECT status, resolution FROM memory_flags WHERE id=12").fetchone()
    assert flag[0] == "resolved"
    assert flag[1] == "demote"


def test_auto_resolve_does_not_process_low_confidence(monkeypatch, temp_db):
    """<0.7 duplicate → 不自动处理"""
    path, conn = temp_db
    _monkey_db(monkeypatch, path)

    conn.execute(
        "INSERT INTO memory_flags (id, memory_id, flag_type, confidence, evidence, "
        "status, created_at) VALUES (13, '1', 'duplicate', 0.60, '低置信度重复', 'open', 't')",
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
    assert cfg["duplicate"] == 0.7
    assert cfg["outdated"] == 0.6
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


# ── 2026-08-04 爆炸回路回归测试 ──────────────────────────────────────
# 实测：min_idle=0.5h + outdated 阈值 0.85 + R2 只排除 false_positive
# → 每轮都扫最新50条，对已被 demote 的 ephemeral 记忆重新创 outdated flag，
# 而 auto_resolve 因阈值错配永远清不掉 → 全天累计 2189 open flag。
# 修复后：min_idle=6h、outdated 阈值=0.6、R2 排除所有已 resolved 记忆。


def test_reflection_min_idle_default_is_6h(monkeypatch):
    """get_reflection_min_idle_hours 默认 6h（防高频反思刷爆 flag）。"""
    # 不提供 curator_config.json
    monkeypatch.setattr(
        "vermes_constants.get_vermes_home",
        lambda: __import__("pathlib").Path("/nonexistent/path"),
    )
    from agent.memory_reflection import get_reflection_min_idle_hours

    assert get_reflection_min_idle_hours() == 6.0


def test_r2_excludes_demoted_memories_no_recreate_flag(monkeypatch, tmp_path):
    """R2 排除已 resolved（含 demote）记忆 —— 已 demote 的不应被重新标。

    回归核心：旧逻辑仅排除 false_positive，导致 demote 后的 ephemeral 记忆
    每轮被重新扫描、重新创建 outdated flag（标了又清、清了又标）。
    """
    import sqlite3

    db = tmp_path / "idx.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY, source TEXT, lifecycle_tag TEXT,
            pointer TEXT, scope TEXT, fts_content TEXT)"""
    )
    conn.execute(
        """CREATE TABLE memory_flags (
            id INTEGER PRIMARY KEY, memory_id TEXT, flag_type TEXT,
            confidence REAL, evidence TEXT, status TEXT,
            created_at TEXT, source TEXT, resolution TEXT, resolved_at TEXT)"""
    )
    # 一条记忆，已被 demote（resolution=demote，status=resolved）
    conn.execute(
        "INSERT INTO memories VALUES (1, 'note', 'ephemeral', NULL, NULL, '旧事实')"
    )
    conn.execute(
        "INSERT INTO memory_flags (memory_id, flag_type, confidence, evidence, "
        "status, created_at, source, resolution) VALUES "
        "('1', 'outdated', 0.9, '已降级', 'resolved', 't', 'reflection', 'demote')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: str(db))
    # 屏蔽 LLM 调用，确保若扫描到该记忆会尝试写 flag（但应被排除）
    monkeypatch.setattr(
        "agent.memory_reflection._reflection_llm_review",
        lambda prompt: {"final": "[]", "summary": "", "error": None},
    )
    from agent.memory_reflection import _scan_llm_flags

    created = _scan_llm_flags(limit=50)
    assert created == 0, f"已 demote 记忆不应被重新标，但创建了 {created} 个 flag"


def test_sweep_orphan_flags_resolves_dangling(monkeypatch, tmp_path):
    """指向不存在记忆的 open flag → 清为 orphan（2026-08-04 孤儿清扫回归）。

    实战：2189 open flag 中 2097 指向已删除记忆，auto_resolve 不处理，
    导致 flag 库永久堆积。新增 _sweep_orphan_flags 永久清零。
    """
    import sqlite3

    db = tmp_path / "idx.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY, source TEXT, lifecycle_tag TEXT,
            pointer TEXT, scope TEXT, fts_content TEXT)"""
    )
    conn.execute(
        """CREATE TABLE memory_flags (
            id INTEGER PRIMARY KEY, memory_id TEXT, flag_type TEXT,
            confidence REAL, evidence TEXT, status TEXT,
            created_at TEXT, source TEXT, resolution TEXT, resolved_at TEXT)"""
    )
    # memory 1 存在；flag 指向 1（有效）和 999（孤儿）
    conn.execute("INSERT INTO memories VALUES (1, 'note', NULL, NULL, NULL, '事实')")
    conn.execute(
        "INSERT INTO memory_flags (memory_id, flag_type, confidence, evidence, status, created_at) "
        "VALUES ('1', 'outdated', 0.9, '有效', 'open', 't')"
    )
    conn.execute(
        "INSERT INTO memory_flags (memory_id, flag_type, confidence, evidence, status, created_at) "
        "VALUES ('999', 'outdated', 0.9, '孤儿', 'open', 't')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("agent.memory_fabric._get_index_db", lambda: str(db))
    from agent.memory_reflection import _sweep_orphan_flags

    n = _sweep_orphan_flags()
    assert n == 1, f"应清 1 个孤儿 flag，实际 {n}"

    c = sqlite3.connect(str(db))
    orphan = c.execute("SELECT status, resolution FROM memory_flags WHERE memory_id='999'").fetchone()
    valid = c.execute("SELECT status, resolution FROM memory_flags WHERE memory_id='1'").fetchone()
    c.close()
    assert orphan == ("resolved", "orphan"), orphan
    assert valid == ("open", None), valid  # 有效 flag 不动
