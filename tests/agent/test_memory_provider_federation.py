"""Slice 3: federated search across memory providers (MemoryManager.search_all).

Verifies the unified memory base can fan one query out to every active
provider's ``search()`` (RAG + external KB), and that the "rag is treated as
builtin" fix lets RAG coexist with an external provider (the old bug rejected
RAG once any external provider was active).
"""
import json

from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryProvider


class _FakeProvider(MemoryProvider):
    def __init__(self, name, hits=None, raise_on_search=False):
        self._name = name
        self._hits = hits or []
        self._raise = raise_on_search

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        pass

    def get_tool_schemas(self):
        return []

    def search(self, query, limit=5):
        if self._raise:
            raise RuntimeError(f"{self._name} search exploded")
        return list(self._hits[:limit])


def test_search_all_aggregates_rag_and_external():
    mm = MemoryManager()
    mm.add_provider(
        _FakeProvider(
            "rag",
            hits=[{"content": "RAG passage", "filename": "a.pdf", "chunk_index": 0}],
        )
    )
    mm.add_provider(
        _FakeProvider(
            "honcho",
            hits=[{"content": "KB passage", "pointer": "kb:1", "score": 0.8}],
        )
    )
    results = mm.search_all("query", limit=5)
    sources = {r["source"] for r in results}
    assert sources == {"rag", "honcho"}, sources
    assert all(r["layer"] == "reference" for r in results)
    # normalized shape
    for r in results:
        assert "pointer" in r and "content" in r and "score" in r


def test_search_all_ranks_rag_chunks_with_pointer():
    mm = MemoryManager()
    mm.add_provider(
        _FakeProvider(
            "rag",
            hits=[{"content": "RAG passage", "filename": "a.pdf", "chunk_index": 3}],
        )
    )
    results = mm.search_all("query", limit=5)
    assert results[0]["pointer"] == "rag:a.pdf#3"


def test_search_all_isolates_provider_failure():
    mm = MemoryManager()
    mm.add_provider(_FakeProvider("rag", hits=[{"content": "RAG passage"}]))
    mm.add_provider(_FakeProvider("mem0", raise_on_search=True))
    # must not raise; only the healthy provider's hits return
    results = mm.search_all("query", limit=5)
    assert len(results) == 1
    assert results[0]["source"] == "rag"


def test_add_provider_allows_rag_plus_external():
    mm = MemoryManager()
    mm.add_provider(_FakeProvider("honcho"))  # external first
    mm.add_provider(_FakeProvider("rag"))     # must NOT be rejected as "external"
    assert [p.name for p in mm.providers] == ["honcho", "rag"]


def test_add_provider_rejects_second_external():
    mm = MemoryManager()
    mm.add_provider(_FakeProvider("honcho"))
    mm.add_provider(_FakeProvider("rag"))
    mm.add_provider(_FakeProvider("mem0"))  # second external → rejected
    assert [p.name for p in mm.providers] == ["honcho", "rag"]


def test_default_search_is_noop_for_context_only_providers():
    # A provider that doesn't override search() must safely return [] in federation.
    class _CtxOnly(MemoryProvider):
        @property
        def name(self):
            return "ctx"

        def is_available(self):
            return True

        def initialize(self, session_id, **kwargs):
            pass

        def get_tool_schemas(self):
            return []

    mm = MemoryManager()
    mm.add_provider(_CtxOnly())
    assert mm.search_all("q") == []


def test_default_search_discovers_own_tool_emergent():
    # A provider that does NOT override search() but declares its own *_search
    # tool must auto-federate via handle_tool_call — proving the federation is
    # emergent/data-driven, not per-vendor hardcoded. The other 7 external KBs
    # join the unified recall this way without any framework-side vendor code.
    class _EmergeProvider(MemoryProvider):
        @property
        def name(self):
            return "honcho"

        def is_available(self):
            return True

        def initialize(self, session_id, **kwargs):
            pass

        def get_tool_schemas(self):
            return [{"name": "honcho_search", "description": "semantic search"}]

        def handle_tool_call(self, tool_name, args, **kwargs):
            assert tool_name == "honcho_search"
            assert args.get("query") == "EMERGE_XYZ"
            return json.dumps(
                {
                    "results": [
                        {"memory": "EMERGE_XYZ from honcho", "id": "h:1", "score": 0.7}
                    ]
                }
            )

    mm = MemoryManager()
    mm.add_provider(_EmergeProvider())
    results = mm.search_all("EMERGE_XYZ", limit=5)
    assert len(results) == 1, results
    assert results[0]["content"] == "EMERGE_XYZ from honcho"
    assert results[0]["pointer"] == "h:1"
    assert results[0]["source"] == "honcho"
    assert results[0]["score"] == 0.7


def test_default_search_skips_unrelated_tools():
    # The default search must NOT pick up the unified "memory_search" router
    # or non-search tools — only the provider's own *_search/*_query tool.
    class _Weird(MemoryProvider):
        @property
        def name(self):
            return "weird"

        def is_available(self):
            return True

        def initialize(self, session_id, **kwargs):
            pass

        def get_tool_schemas(self):
            return [
                {"name": "memory_search", "description": "unified router"},
                {"name": "weird_frobnicate", "description": "not a search"},
            ]

        def handle_tool_call(self, tool_name, args, **kwargs):
            raise AssertionError("should not be called")

    mm = MemoryManager()
    mm.add_provider(_Weird())
    assert mm.search_all("q") == []
