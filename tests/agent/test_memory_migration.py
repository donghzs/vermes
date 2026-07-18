"""Slice 4: backfill existing memory into the unified index (memory_migration).

Builds a throwaway HERMES_HOME with the on-disk stores the agent actually uses
(MEMORY.md / USER.md, recall DBs, RAG documents.db) and asserts the migration
seeds the fabric index so every layer becomes searchable. Idempotent re-run
must not duplicate rows.
"""
import os
import sqlite3

import pytest


@pytest.fixture
def seeded_home(tmp_path, monkeypatch):
    h = tmp_path / "hermes"
    h.mkdir()

    # L1 — curated notes
    (h / "memories").mkdir()
    (h / "memories" / "MEMORY.md").write_text(
        "# Notes\nMIG_NOTE_XYZ user prefers postgres for analytics.\n", encoding="utf-8"
    )
    (h / "memories" / "USER.md").write_text(
        "MIG_USER_XYZ contact: alice@example.com\n", encoding="utf-8"
    )

    # L3 — recall DBs
    evo = h / "evolution"
    evo.mkdir()
    _c = sqlite3.connect(evo / "self-model.db")
    _c.execute(
        "CREATE TABLE raw_events(id INTEGER PRIMARY KEY, tool_name TEXT, "
        "args_preview TEXT, result_preview TEXT)"
    )
    _c.execute(
        "INSERT INTO raw_events VALUES(1,'search','MIG_RAW_XYZ query','found 3 docs')"
    )
    _c.commit()
    _c.close()
    _c = sqlite3.connect(evo / "fusion-state.db")
    _c.execute(
        "CREATE TABLE emotional_state(id INTEGER PRIMARY KEY, timestamp TEXT, "
        "emotion TEXT, intensity REAL, trigger TEXT, context TEXT)"
    )
    _c.execute(
        "INSERT INTO emotional_state VALUES(1,'t','calm',0.5,'MIG_EMO_XYZ','focus')"
    )
    _c.commit()
    _c.close()
    _c = sqlite3.connect(h / "session_handoffs.db")
    _c.execute(
        "CREATE TABLE session_handoffs(id INTEGER PRIMARY KEY, user_request TEXT, "
        "summary_text TEXT, decisions TEXT)"
    )
    _c.execute(
        "INSERT INTO session_handoffs VALUES(1,'MIG_HANDOFF_XYZ do X','did X','use Y')"
    )
    _c.commit()
    _c.close()

    # L4 — RAG documents
    rag = h / "rag"
    rag.mkdir()
    _c = sqlite3.connect(rag / "documents.db")
    _c.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY, filename TEXT)")
    _c.execute("CREATE TABLE chunks(doc_id INTEGER, chunk_index INTEGER, content TEXT)")
    _c.execute("INSERT INTO documents VALUES(1,'mig_doc.pdf')")
    _c.execute(
        "INSERT INTO chunks VALUES(1,0,'MIG_DOC_XYZ introduction to the system')"
    )
    _c.commit()
    _c.close()

    monkeypatch.setenv("HERMES_HOME", str(h))
    import agent.memory_fabric as mf

    db = mf._get_index_db()
    if db.exists():
        os.remove(db)
    return h


def test_migration_backfills_all_layers(seeded_home):
    from agent.memory_migration import migrate_memories_to_fabric
    from agent.memory_fabric import recall

    skills = [{"name": "mig_skill", "description": "MIG_SKILL_XYZ migration helper"}]
    summary = migrate_memories_to_fabric(hermes_home=str(seeded_home), skills=skills)

    assert summary["L1_note"] == 2, summary  # MEMORY.md + USER.md
    assert summary["L2_procedural"] == 1, summary  # injected skill
    assert summary["L3_episodic"] >= 3, summary  # raw_event + emotion + handoff
    assert summary["L4_reference"] == 1, summary  # one document

    # each layer is now searchable through the unified index
    assert recall("MIG_NOTE_XYZ", layer="note")
    assert recall("MIG_USER_XYZ", layer="note")
    assert recall("MIG_SKILL_XYZ", layer="procedural")
    assert recall("MIG_RAW_XYZ", layer="episodic")
    assert recall("MIG_EMO_XYZ", layer="episodic")
    assert recall("MIG_HANDOFF_XYZ", layer="episodic")
    assert recall("MIG_DOC_XYZ", layer="reference")


def test_migration_is_idempotent(seeded_home):
    from agent.memory_migration import migrate_memories_to_fabric
    from agent.memory_fabric import recall

    skills = [{"name": "mig_skill", "description": "MIG_SKILL_XYZ migration helper"}]
    migrate_memories_to_fabric(hermes_home=str(seeded_home), skills=skills)
    first = recall("MIG_NOTE_XYZ", layer="note")
    # re-run — same pointer → same row, not duplicated
    migrate_memories_to_fabric(hermes_home=str(seeded_home), skills=skills)
    second = recall("MIG_NOTE_XYZ", layer="note")
    assert len(first) == len(second) == 1


def test_migration_handles_missing_stores_gracefully(tmp_path, monkeypatch):
    # Empty HERMES_HOME (no recall DBs, no RAG) must not crash.
    h = tmp_path / "hermes"
    h.mkdir()
    (h / "memories").mkdir()
    (h / "memories" / "MEMORY.md").write_text(
        "MIG_ONLYNOTE_XYZ standalone note\n", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(h))
    import agent.memory_fabric as mf

    db = mf._get_index_db()
    if db.exists():
        os.remove(db)

    from agent.memory_migration import migrate_memories_to_fabric

    summary = migrate_memories_to_fabric(hermes_home=str(h))
    assert summary["L1_note"] == 1
    assert summary["L3_episodic"] == 0
    assert summary["L4_reference"] == 0
