"""Bug 1 fix: `memory` writes must be searchable via `memory_search`.

The `memory` tool persists to MEMORY.md / USER.md. Previously writing via
`memory` and then searching returned nothing because the curated memory lived
in a separate store from the RAG index (the fragile ``_sync_rag_index`` bridge
drifted). The unified memory fabric (``agent.memory_fabric``) now indexes
curated notes into a single typed index on every write, and ``memory_search``
queries that index — so notes are reachable through the canonical path
(logical-unify Slice 1).
"""
import os

import pytest


@pytest.fixture
def VERMES_home(tmp_path, monkeypatch):
    h = tmp_path / "Vermes"
    h.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(h))
    import agent.memory_fabric as mf

    # start from a clean unified index for the test
    db = mf._get_index_db()
    if db.exists():
        os.remove(db)
    return h


def test_memory_write_is_searchable_via_fabric(VERMES_home):
    from tools.memory_tool import MemoryStore
    from agent.memory_fabric import recall

    store = MemoryStore()
    store.add("memory", "VERMES_BRIDGE_TOKEN_XYZ my favorite database is postgres")

    hits = recall("VERMES_BRIDGE_TOKEN_XYZ", layer="note")
    assert any("VERMES_BRIDGE_TOKEN_XYZ" in h["content"] for h in hits), (
        "memory write was not searchable via the unified index"
    )


def test_memory_replace_updates_index(VERMES_home):
    from tools.memory_tool import MemoryStore
    from agent.memory_fabric import recall

    store = MemoryStore()
    store.add("memory", "VERMES_OLD_TOKEN_ABC first draft")
    store.replace(
        "memory",
        "VERMES_OLD_TOKEN_ABC first draft",
        "VERMES_NEW_TOKEN_DEF revised draft",
    )

    assert recall("VERMES_NEW_TOKEN_DEF", layer="note"), "replaced content not indexed"
    assert not recall(
        "VERMES_OLD_TOKEN_ABC", layer="note"
    ), "stale content still indexed after replace"
