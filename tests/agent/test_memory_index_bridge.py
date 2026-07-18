"""Bug 1 fix: `memory` writes must be searchable via `memory_search`.

The `memory` tool persists to MEMORY.md / USER.md — a separate store from the
RAG FTS5 knowledge base that `memory_search` queries. Previously writing via
`memory` and then searching via `memory_search` returned nothing. The bridge
(`index_memory_text`) re-indexes curated memory into the RAG store on every
write.
"""
import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    h = tmp_path / "hermes"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    import agent.rag_provider as rp
    rp._conn_cache.clear()
    return h


def test_memory_write_is_searchable_via_rag(hermes_home):
    from tools.memory_tool import MemoryStore
    from agent.rag_provider import _get_rag_db, _get_conn

    store = MemoryStore()
    store.add("memory", "VERMES_BRIDGE_TOKEN_XYZ my favorite database is postgres")

    db = _get_rag_db()
    conn = _get_conn(str(db))
    c = conn.cursor()
    c.execute(
        "SELECT content FROM chunks_fts WHERE chunks_fts MATCH ?",
        ("VERMES_BRIDGE_TOKEN_XYZ",),
    )
    rows = c.fetchall()
    assert any("VERMES_BRIDGE_TOKEN_XYZ" in r[0] for r in rows), (
        "memory write was not searchable via RAG FTS5"
    )


def test_memory_replace_updates_index(hermes_home):
    from tools.memory_tool import MemoryStore
    from agent.rag_provider import _get_rag_db, _get_conn

    store = MemoryStore()
    store.add("memory", "VERMES_OLD_TOKEN_ABC first draft")
    store.replace("memory", "VERMES_OLD_TOKEN_ABC first draft",
                  "VERMES_NEW_TOKEN_DEF revised draft")

    db = _get_rag_db()
    conn = _get_conn(str(db))
    c = conn.cursor()
    c.execute(
        "SELECT content FROM chunks_fts WHERE chunks_fts MATCH ?",
        ("VERMES_NEW_TOKEN_DEF",),
    )
    assert c.fetchall(), "replaced content not indexed"
    c.execute(
        "SELECT content FROM chunks_fts WHERE chunks_fts MATCH ?",
        ("VERMES_OLD_TOKEN_ABC",),
    )
    assert not c.fetchall(), "stale content still indexed after replace"
