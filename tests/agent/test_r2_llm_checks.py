"""R2 四类 LLM 校核测试（mock LLM，不真实调用）"""
import sqlite3
from pathlib import Path


def _seed_memories(db_path, rows):
    """创建 memories 表并插入样本（仅 R2 读取所需列）"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY,
                fts_content TEXT,
                pointer TEXT,
                scope TEXT,
                lifecycle_tag TEXT
            )"""
        )
        for r in rows:
            conn.execute(
                "INSERT INTO memories (id, fts_content, pointer, scope, lifecycle_tag) "
                "VALUES (?,?,?,?,?)",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def test_r2_detects_four_classes(tmp_path, monkeypatch):
    """R2 正确分类四类问题并写入对应 flag_type"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    _seed_memories(
        db,
        [
            (1, "The API version is v1 and uses endpoint /old.", "p1", "global", None),
            (2, "We use the legacy Postgres primary store.", "p2", "global", None),
        ],
    )

    def fake_llm(prompt):
        if "v1" in prompt:
            final = '[{"class":"stale","confidence":0.8,"evidence":"version likely outdated"}]'
        else:
            final = '[{"class":"redundant","confidence":0.6,"evidence":"duplicate of known fact"}]'
        return {"final": final, "error": None}

    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)
    monkeypatch.setattr(mr, "_reflection_llm_review", fake_llm)

    created = mr._scan_llm_flags()
    assert created == 2

    flags = mr.get_open_flags()
    types = sorted(f["flag_type"] for f in flags)
    assert types == ["duplicate", "outdated"]


def test_r2_fail_open_on_llm_error(tmp_path, monkeypatch):
    """LLM 失败时 fail-open：跳过，0 flag"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    _seed_memories(db, [(1, "Some long enough memory content here about config x.", "p", "global", None)])

    def fake_llm_err(prompt):
        return {"final": "", "error": "llm down"}

    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)
    monkeypatch.setattr(mr, "_reflection_llm_review", fake_llm_err)

    created = mr._scan_llm_flags()
    assert created == 0
    assert mr.get_open_flags() == []


def test_r2_skip_short_and_decision(tmp_path, monkeypatch):
    """过短记忆与 @decision 记忆不参与 LLM 校核"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    _seed_memories(
        db,
        [
            (1, "short", "p", "global", None),  # 过短 → 跳过
            (2, "long enough memory about preference y", "p", "global", "decision"),  # decision → 跳过
            (3, "long enough memory about normal fact z", "p", "global", None),
        ],
    )

    seen = {}

    def fake_llm(prompt):
        seen["prompt"] = prompt
        return {"final": "[]", "error": None}

    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)
    monkeypatch.setattr(mr, "_reflection_llm_review", fake_llm)

    created = mr._scan_llm_flags()
    assert created == 0
    # 仅 #3（普通长记忆）进入 LLM；#1 过短、#2 为 decision 均跳过
    assert "long enough memory about normal fact z" in seen["prompt"]


def test_r2_wired_into_run_reflection_review(tmp_path, monkeypatch):
    """run_reflection_review 调用 _scan_llm_flags（R2 已接线）"""
    from agent import memory_fabric
    from agent import memory_reflection as mr

    db = tmp_path / "test.db"
    mr.ensure_reflection_schema(db)
    _seed_memories(db, [(1, "The config v1 is old and deprecated.", "p", "global", None)])

    def fake_llm(prompt):
        return {"final": '[{"class":"stale","confidence":0.9,"evidence":"deprecated"}]', "error": None}

    monkeypatch.setattr(memory_fabric, "_get_index_db", lambda: db)
    monkeypatch.setattr(mr, "_reflection_llm_review", fake_llm)
    monkeypatch.setattr("agent.memory_reflection.get_vermes_home", lambda: tmp_path)

    # 模拟空闲（直接调用 review，跳过门控）
    mr.run_reflection_review()
    assert len(mr.get_open_flags()) == 1
    assert mr.get_open_flags()[0]["flag_type"] == "outdated"
