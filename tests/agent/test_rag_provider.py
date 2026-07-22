"""Regression tests for RAGProvider (Route A-1).

Covers:
- FTS5 default path: init, ingest, search, prefetch, delete, list, stats
- Vector backend (sqlite-vec): init with vec table, fail-open when unavailable
- Edge cases: empty query, missing file, re-ingest overwrite

These tests use a temp HERMES_HOME to avoid polluting the real RAG DB.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def rag_provider(tmp_path, monkeypatch):
    """Create a RAGProvider with a clean temp HERMES_HOME, default FTS5 backend."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("VERMES_RAG_BACKEND", raising=False)
    # Force reimport so module-level config picks up the new env
    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    import agent.rag_provider
    importlib.reload(agent.rag_provider)
    from agent.rag_provider import RAGProvider
    provider = RAGProvider()
    provider.initialize("test-session")
    return provider


@pytest.fixture
def rag_provider_vec(tmp_path, monkeypatch):
    """Create a RAGProvider with sqlite-vec backend enabled."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("VERMES_RAG_BACKEND", "sqlite-vec")
    import importlib
    import hermes_constants
    importlib.reload(hermes_constants)
    import agent.rag_provider
    importlib.reload(agent.rag_provider)
    from agent.rag_provider import RAGProvider
    provider = RAGProvider()
    provider.initialize("test-session")
    return provider


# ── FTS5 Default Path Tests ──────────────────────────────────────────


class TestFTS5DefaultPath:
    """Verify FTS5 path works unchanged (zero regression from A-1 changes)."""

    def test_is_available(self, rag_provider):
        assert rag_provider.is_available() is True

    def test_initialization(self, rag_provider):
        assert rag_provider._initialized is True
        assert rag_provider._db_path is not None
        assert rag_provider._db_path.exists()

    def test_system_prompt_empty_when_no_docs(self, rag_provider):
        block = rag_provider.system_prompt_block()
        assert block == ""

    def test_system_prompt_shows_count_after_ingest(self, rag_provider):
        rag_provider.ingest_content("doc.txt", "Some content here.", "txt")
        block = rag_provider.system_prompt_block()
        assert "知识库" in block
        assert "1" in block

    def test_ingest_content_basic(self, rag_provider):
        result = rag_provider.ingest_content(
            "test.md", "# Title\n\nSome markdown content about RAG.", "md"
        )
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["filename"] == "test.md"
        assert data["chunks"] >= 1

    def test_ingest_content_empty(self, rag_provider):
        result = rag_provider.ingest_content("empty.txt", "", "txt")
        data = json.loads(result)
        assert "error" in data

    def test_search_finds_matching_content(self, rag_provider):
        rag_provider.ingest_content(
            "python.md",
            "Python is a high-level programming language known for readability.",
            "md",
        )
        hits = rag_provider.search("Python programming", limit=3)
        assert len(hits) >= 1
        assert "Python" in hits[0]["content"]
        assert hits[0]["filename"] == "python.md"
        assert "doc_id" in hits[0]
        assert "preview" in hits[0]

    def test_search_empty_query_returns_empty(self, rag_provider):
        rag_provider.ingest_content("doc.txt", "Some content.", "txt")
        assert rag_provider.search("", limit=5) == []
        assert rag_provider.search("   ", limit=5) == []

    def test_search_no_match_returns_empty(self, rag_provider):
        rag_provider.ingest_content("doc.txt", "Hello world.", "txt")
        hits = rag_provider.search("completelyunrelatedterm", limit=5)
        assert hits == []

    def test_prefetch_returns_context_string(self, rag_provider):
        rag_provider.ingest_content(
            "guide.md",
            "This guide covers machine learning fundamentals and neural networks.",
            "md",
        )
        result = rag_provider.prefetch("machine learning")
        assert len(result) > 0
        assert "知识库" in result

    def test_prefetch_empty_when_no_docs(self, rag_provider):
        assert rag_provider.prefetch("anything") == ""

    def test_delete_document(self, rag_provider):
        result = rag_provider.ingest_content("temp.txt", "Temporary content.", "txt")
        doc_id = json.loads(result)["doc_id"]
        assert rag_provider.delete_document(doc_id) is True
        # Verify gone
        docs = rag_provider.list_documents()
        assert all(d["id"] != doc_id for d in docs)

    def test_reingest_overwrites(self, rag_provider):
        """Re-ingesting the same file should replace, not duplicate."""
        rag_provider.ingest_content("doc.txt", "Original content.", "txt")
        result2 = rag_provider.ingest_content("doc.txt", "Updated content here.", "txt")
        docs = rag_provider.list_documents()
        # Should have exactly 1 document (overwrite)
        assert len(docs) == 1
        assert docs[0]["filename"] == "doc.txt"

    def test_list_documents(self, rag_provider):
        rag_provider.ingest_content("a.txt", "Content A.", "txt")
        rag_provider.ingest_content("b.md", "Content B.", "md")
        docs = rag_provider.list_documents()
        assert len(docs) == 2
        filenames = {d["filename"] for d in docs}
        assert filenames == {"a.txt", "b.md"}

    def test_get_document_chunks(self, rag_provider):
        result = rag_provider.ingest_content(
            "long.txt",
            "Chunk one content. " * 100 + "Chunk two content. " * 100,
            "txt",
        )
        doc_id = json.loads(result)["doc_id"]
        chunks = rag_provider.get_document_chunks(doc_id)
        assert len(chunks) >= 1
        assert all("content" in c and "chunk_index" in c for c in chunks)

    def test_handle_tool_call_search(self, rag_provider):
        rag_provider.ingest_content("api.md", "REST API design patterns.", "md")
        result = rag_provider.handle_tool_call(
            "memory_search", {"query": "API design"}
        )
        data = json.loads(result)
        assert "results" in data
        assert data["count"] >= 1

    def test_handle_tool_call_unknown(self, rag_provider):
        result = rag_provider.handle_tool_call("nonexistent", {})
        data = json.loads(result)
        assert "error" in data


# ── Vector Backend Tests (A-1) ───────────────────────────────────────


class TestVectorBackend:
    """Verify sqlite-vec backend integration (A-1)."""

    def test_vec_backend_enabled(self, rag_provider_vec):
        """When VERMES_RAG_BACKEND=sqlite-vec, vector backend should be active."""
        from agent.rag_provider import _VEC_BACKEND, _vec_available

        assert _VEC_BACKEND == "sqlite-vec"
        assert _vec_available is True

    def test_vec_table_created(self, rag_provider_vec):
        """chunks_vec table should exist when vector backend is enabled."""
        import sqlite3
        import sqlite_vec

        conn = sqlite3.connect(str(rag_provider_vec._db_path))
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "chunks_vec" in tables

    def test_fts5_still_works_with_vec_enabled(self, rag_provider_vec):
        """FTS5 search should still work when vector backend is enabled."""
        rag_provider_vec.ingest_content(
            "test.md", "Python programming language basics.", "md"
        )
        hits = rag_provider_vec.search("Python", limit=3)
        assert len(hits) >= 1
        assert "Python" in hits[0]["content"]

    def test_vector_search_returns_empty_without_embeddings(self, rag_provider_vec):
        """_vector_search should return [] when no embeddings stored (placeholder)."""
        rag_provider_vec.ingest_content("test.md", "Some content.", "md")
        vec_hits = rag_provider_vec._vector_search("test", limit=3)
        assert vec_hits == []

    def test_vec_backend_fail_open_on_missing_dylib(self, tmp_path, monkeypatch):
        """When dylib is missing, should fail-open to FTS5."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("VERMES_RAG_BACKEND", "sqlite-vec")

        # Mock sqlite_vec import to fail (simulating missing dylib)
        import sys
        original = sys.modules.get("sqlite_vec")
        sys.modules["sqlite_vec"] = None  # type: ignore
        try:
            import importlib
            import agent.rag_provider
            importlib.reload(agent.rag_provider)
            from agent.rag_provider import _vec_available

            # Provider should still work with FTS5
            assert _vec_available is False
        finally:
            if original is not None:
                sys.modules["sqlite_vec"] = original
            else:
                sys.modules.pop("sqlite_vec", None)
        # Reload to restore real state
        importlib.reload(agent.rag_provider)


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge case and error handling tests."""

    def test_ingest_nonexistent_file(self, rag_provider):
        result = rag_provider.handle_tool_call(
            "memory_ingest", {"file_path": "/nonexistent/path.txt"}
        )
        data = json.loads(result)
        assert "error" in data

    def test_search_with_special_chars(self, rag_provider):
        """Search with special chars should not crash."""
        rag_provider.ingest_content("doc.txt", "Normal content here.", "txt")
        # Should not raise
        hits = rag_provider.search("test!!!@#$%", limit=3)
        # May return 0 or more, just shouldn't crash
        assert isinstance(hits, list)

    def test_get_document_stats_empty(self, rag_provider):
        stats = rag_provider.get_document_stats()
        assert stats == []

    def test_get_document_stats_with_docs(self, rag_provider):
        rag_provider.ingest_content("doc.txt", "Some content.", "txt")
        # get_document_stats queries Evolution DB which may not exist in test;
        # it fail-opens to [] — verify it returns a list without crashing
        stats = rag_provider.get_document_stats()
        assert isinstance(stats, list)

    def test_shutdown_no_error(self, rag_provider):
        rag_provider.shutdown()  # Should not raise

    def test_queue_prefetch_no_error(self, rag_provider):
        rag_provider.queue_prefetch("test query")  # Should not raise

    def test_sync_turn_no_error(self, rag_provider):
        rag_provider.sync_turn("user msg", "assistant msg")  # Should not raise
