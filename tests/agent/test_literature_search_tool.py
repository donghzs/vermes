"""Offline tests for the literature_search tool wrapper.

The handler binds ``get_active_literature_provider`` / ``get_provider`` as
module-level names, so we monkeypatch them on the module to inject fake
providers and avoid real network calls (the sandbox has no outbound network).
"""

import asyncio
import json

import pytest

from agent.literature_provider import LiteratureProvider
from agent.literature_registry import _reset_for_tests


@pytest.fixture(autouse=True)
def _reset_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def _mod():
    import tools.literature_search_tool as m

    return m


def test_tool_registered(_mod):
    from tools.registry import registry

    assert "literature_search" in registry._tools


def test_handler_routes_to_active(monkeypatch, _mod):
    class FakeProv:
        name = "fake"

        def is_available(self):
            return True

        def search(self, q, limit=10):
            return {"success": True, "data": {"papers": [{"title": q}]}}

    monkeypatch.setattr(_mod, "get_active_literature_provider", lambda: FakeProv())
    out = asyncio.run(_mod._handle_literature_search({"query": "深度学习", "limit": 3}))
    d = json.loads(out)
    assert d["success"] is True
    assert d["data"]["papers"][0]["title"] == "深度学习"


def test_handler_empty_query(_mod):
    out = asyncio.run(_mod._handle_literature_search({"query": ""}))
    assert json.loads(out)["success"] is False


def test_handler_unavailable_prompt(monkeypatch, _mod):
    class CnkiProv(LiteratureProvider):
        name = "cnki"

        def is_available(self):
            return False

        def search(self, q, limit=10):
            raise AssertionError("must not be called when unavailable")

        def get_setup_schema(self):
            return {"env_vars": [{"key": "CNKI_API_KEY"}]}

    monkeypatch.setattr(_mod, "get_active_literature_provider", lambda: CnkiProv())
    out = asyncio.run(_mod._handle_literature_search({"query": "x"}))
    d = json.loads(out)
    assert d["success"] is False
    assert "CNKI_API_KEY" in d["error"]


def test_handler_explicit_source(monkeypatch, _mod):
    class Wf:
        name = "wanfang"

        def is_available(self):
            return True

        def search(self, q, limit=10):
            return {"success": True, "data": {"papers": [{"title": q, "source": "wanfang"}]}}

    monkeypatch.setattr(_mod, "get_provider", lambda s: Wf() if s == "wanfang" else None)
    out = asyncio.run(_mod._handle_literature_search({"query": "x", "source": "wanfang"}))
    d = json.loads(out)
    assert d["success"] is True
    assert d["data"]["papers"][0]["source"] == "wanfang"
