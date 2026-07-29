"""Tests for the pluggable literature-provider architecture.

Covers: registry register/get/list/active-resolution, credential-driven
availability gating for CNKI/Wanfang, the always-free OpenAlex/Crossref
sources, and the CNKI fetcher result-shape conversion.
"""

import asyncio

import pytest

from agent.literature_provider import LiteratureProvider, PaperRecord
from agent.literature_registry import (
    _reset_for_tests,
    bootstrap_builtin_providers,
    get_active_literature_provider,
    get_provider,
    list_providers,
    register_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()


class _Dummy(LiteratureProvider):
    def __init__(self, name, avail=True):
        self._n = name
        self._a = avail

    @property
    def name(self):
        return self._n

    def is_available(self):
        return self._a

    def search(self, query, limit=10):
        return {"success": True, "data": {"papers": []}}


def test_register_and_get():
    register_provider(_Dummy("x"))
    assert get_provider("x") is not None
    assert get_provider("missing") is None


def test_register_rejects_wrong_type():
    with pytest.raises(TypeError):
        register_provider(object())


def test_list_sorted():
    register_provider(_Dummy("b"))
    register_provider(_Dummy("a"))
    assert [p.name for p in list_providers()] == ["a", "b"]


def test_bootstrap_registers_four_builtins():
    bootstrap_builtin_providers()
    names = {p.name for p in list_providers()}
    assert {"openalex", "crossref", "cnki", "wanfang"} <= names


def test_free_sources_always_available():
    bootstrap_builtin_providers()
    from agent.literature_providers.openalex import OpenAlexProvider
    from agent.literature_providers.crossref import CrossrefProvider

    assert OpenAlexProvider().is_available() is True
    assert CrossrefProvider().is_available() is True


def test_cnki_wanfang_gated_by_credentials(monkeypatch):
    for var in ("CNKI_GATEWAY_URL", "CNKI_API_KEY", "WANFANG_API_KEY", "WANFANG_USER"):
        monkeypatch.delenv(var, raising=False)
    bootstrap_builtin_providers()
    from agent.literature_providers.cnki import CnkiProvider
    from agent.literature_providers.wanfang import WanfangProvider

    assert CnkiProvider().is_available() is False
    assert WanfangProvider().is_available() is False

    monkeypatch.setenv("CNKI_API_KEY", "sekret")
    assert CnkiProvider().is_available() is True
    monkeypatch.setenv("WANFANG_API_KEY", "wf")
    assert WanfangProvider().is_available() is True


def test_active_prefers_paid_when_credentialed(monkeypatch):
    for var in ("CNKI_GATEWAY_URL", "CNKI_API_KEY", "WANFANG_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    bootstrap_builtin_providers()

    # No credentials -> free source; openalex precedes crossref in legacy order.
    assert get_active_literature_provider().name == "openalex"

    monkeypatch.setenv("CNKI_API_KEY", "sekret")
    assert get_active_literature_provider().name == "cnki"


def test_cnki_search_converts_cnkifetcher(monkeypatch):
    class FakePaper:
        title = "测试论文"
        authors = ["张三"]
        year = "2023"
        journal = "某学报"
        abstract = "摘要内容"
        cited_count = 5
        url = "http://example.com/x"
        doi = "10.1000/x"
        keywords = ["主题A"]

    import vermes_cli.scholarforge.cnki_fetcher as cf

    async def _fake_search(query, limit):
        return [FakePaper()]

    monkeypatch.setattr(cf, "search_cnki", _fake_search)
    bootstrap_builtin_providers()

    from agent.literature_providers.cnki import CnkiProvider

    res = CnkiProvider().search("测试", 5)
    assert res["success"] is True
    papers = res["data"]["papers"]
    assert len(papers) == 1
    assert papers[0]["title"] == "测试论文"
    assert papers[0]["source"] == "cnki"  # source tag overridden by provider
    assert papers[0]["doi"] == "10.1000/x"


def test_wanfang_search_converts_cnkifetcher(monkeypatch):
    class FakePaper:
        title = "万方论文"
        authors = ["李四"]
        year = "2022"
        journal = "万方期刊"
        abstract = "摘要"
        cited_count = 3
        url = "http://wf/x"
        doi = "10.2000/y"
        keywords = ["K"]

    import vermes_cli.scholarforge.cnki_fetcher as cf

    async def _fake_wf(query, limit):
        return [FakePaper()]

    monkeypatch.setattr(cf, "_fetch_via_wanfang", _fake_wf)
    bootstrap_builtin_providers()

    from agent.literature_providers.wanfang import WanfangProvider

    res = WanfangProvider().search("测试", 5)
    assert res["success"] is True
    assert res["data"]["papers"][0]["source"] == "wanfang"


def test_setup_schema_exposes_env_vars():
    bootstrap_builtin_providers()
    from agent.literature_providers.cnki import CnkiProvider
    from agent.literature_providers.wanfang import WanfangProvider

    cnki_vars = {e["key"] for e in CnkiProvider().get_setup_schema()["env_vars"]}
    wf_vars = {e["key"] for e in WanfangProvider().get_setup_schema()["env_vars"]}
    assert "CNKI_API_KEY" in cnki_vars
    assert "CNKI_USERNAME" in cnki_vars
    assert "WANFANG_API_KEY" in wf_vars


def test_paper_record_roundtrip():
    p = PaperRecord(title="T", authors=["a"], year="2024", doi="10/x")
    d = p.to_dict()
    assert d["title"] == "T"
    p2 = PaperRecord.from_dict(d)
    assert p2.title == "T" and p2.doi == "10/x"
