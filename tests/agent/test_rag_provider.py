"""Regression tests for RAGProvider (Route A-1).

Covers:
- FTS5 default path: init, ingest, search, prefetch, delete, list, stats
- Vector backend (sqlite-vec): init with vec table, fail-open when unavailable
- Edge cases: empty query, missing file, re-ingest overwrite

These tests use a temp VERMES_HOME to avoid polluting the real RAG DB.
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
    """Create a RAGProvider with a clean temp VERMES_HOME, default FTS5 backend."""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    monkeypatch.delenv("VERMES_RAG_BACKEND", raising=False)
    # Force reimport so module-level config picks up the new env
    import importlib
    import vermes_constants
    importlib.reload(vermes_constants)
    import agent.rag_provider
    importlib.reload(agent.rag_provider)
    from agent.rag_provider import RAGProvider
    provider = RAGProvider()
    provider.initialize("test-session")
    return provider


@pytest.fixture
def rag_provider_vec(tmp_path, monkeypatch):
    """Create a RAGProvider with sqlite-vec backend enabled."""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    monkeypatch.setenv("VERMES_RAG_BACKEND", "sqlite-vec")
    import importlib
    import vermes_constants
    importlib.reload(vermes_constants)
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
        monkeypatch.setenv("VERMES_HOME", str(tmp_path))
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


# ── 2026-09-27：FTS5 索引损坏自愈 + 中文短词召回 ──────────────────────
# 事故背景：真实用户库 ~/.vermes/rag/documents.db 的 chunks_fts 倒排索引损坏，
# 日志刷了 64 条 `RAG search failed: database disk image is malformed` 却因
# debug 级静默吞掉而无人察觉；同时 trigram 分词对 <3 字符无能为力，
# 中文两字词（记忆/进化/模型…）永远搜不到。这里把两条都锁住。


class TestFTSIndexRecovery:
    """索引损坏必须能自愈，且不再静默（2026-09-27 P1）。"""

    def _corrupt_fts_index(self, db_path):
        """人为制造倒排索引损坏：把 chunks_fts_data 的 block 写成垃圾。

        手法经过实测筛选（6 选 1）：只有改 chunks_fts_data.block 才会同时
          (a) PRAGMA quick_check → "malformed inverted index for FTS5 table"
          (b) 查询真抛 `DatabaseError: database disk image is malformed`
        另外 5 种（改 idx.pgno / drop idx 表 / 删 data 行…）只让 quick_check
        变坏但查询照常工作 —— 用它们写测试会得到"恒真"的假绿。
        本手法与真实事故（~/.vermes/rag/documents.db）形态一致。
        """
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute(
                "UPDATE chunks_fts_data SET block = randomblob(max(length(block), 64))"
            )
            conn.execute("PRAGMA writable_schema=OFF")
            conn.commit()
        finally:
            conn.close()

    def test_rebuild_fts_index_repairs_corruption(self, rag_provider):
        """L1/L2 自愈：损坏 → _rebuild_fts_index → quick_check 回到 ok。"""
        import sqlite3

        from agent.rag_provider import _rebuild_fts_index

        rag_provider.ingest_content("doc.txt", "分布式系统的长期进化与记忆机制", "txt")
        db_path = rag_provider._db_path
        self._corrupt_fts_index(db_path)

        conn = sqlite3.connect(str(db_path))
        verdict = conn.execute("PRAGMA quick_check").fetchone()[0]
        conn.close()
        # 探针自证：损坏必须真的造成了，否则这条测试是恒真的
        assert verdict != "ok", f"损坏未生效，quick_check 仍为 {verdict}"

        assert _rebuild_fts_index(str(db_path)) is True

        conn = sqlite3.connect(str(db_path))
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        conn.close()

    def test_search_recovers_after_corruption(self, rag_provider):
        """端到端：索引损坏后 search 仍能召回（自愈 + 重试一次），不是静默空。"""
        rag_provider.ingest_content("doc.txt", "分布式系统的长期进化与记忆机制研究", "txt")
        self._corrupt_fts_index(rag_provider._db_path)
        # 用 ≥3 字的词验证（短词走 LIKE 本就不依赖索引，无法证明索引修复）
        results = rag_provider.search("分布式系统", limit=3)
        assert isinstance(results, list)
        assert len(results) >= 1, "索引自愈后仍召回不到，自愈链路没生效"


class TestCJKShortTermRecall:
    """中文两字词必须能召回（trigram 天生匹配不了 <3 字符）。"""

    def test_two_char_cjk_is_recalled(self, rag_provider):
        """正向：库里有「记忆」，search('记忆') 必须命中。"""
        rag_provider.ingest_content("doc.txt", "这里讨论长期记忆与进化机制的实现", "txt")
        results = rag_provider.search("记忆", limit=3)
        assert len(results) >= 1, "两字中文词召回不到 —— LIKE 兜底未生效"
        assert "记忆" in results[0]["content"]

    def test_short_term_not_in_library_returns_empty(self, rag_provider):
        """负向：库里没有的短词不得凭空命中（兜底不能变成乱返回）。"""
        rag_provider.ingest_content("doc.txt", "这里讨论长期记忆与进化机制的实现", "txt")
        assert rag_provider.search("量子纠缠", limit=3) == []
        assert rag_provider.search("阿巴阿巴", limit=3) == []

    def test_build_fts_terms_split(self):
        """分词边界：≥3 字符进 MATCH 组，<3 字符进短词组（双探针）。"""
        from agent.rag_provider import _build_fts_terms

        # 应分离
        fts, short = _build_fts_terms("记忆")
        assert short == ["记忆"] and fts == [], f"两字词应进短词组，实得 fts={fts} short={short}"

        fts, short = _build_fts_terms("分布式系统")
        assert fts and "分布" in fts[0][:3] or len(fts) >= 1, f"四字词应产生 trigram: {fts}"
        assert short == [], f"四字词不应进短词组: {short}"

        # ASCII 边界
        fts, short = _build_fts_terms("ab")
        assert short == ["ab"] and fts == []

        fts, short = _build_fts_terms("abc")
        assert fts == ["abc"] and short == []

    def test_prefetch_short_term_recall(self, rag_provider):
        """prefetch 同样要能召回两字词（原本是 skip 掉的）。"""
        rag_provider.ingest_content("doc.txt", "这里讨论长期记忆与进化机制的实现", "txt")
        out = rag_provider.prefetch("记忆")
        assert "知识库上下文" in out, f"prefetch 未召回两字词: {out!r}"
