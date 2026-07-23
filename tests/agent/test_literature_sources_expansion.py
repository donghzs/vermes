"""文献源扩展（前端"文献源设置"表单 + 批量付费/免费源）测试。

覆盖三层：
1. 凭证层 — ``register_service`` 的 category / extra_fields(dict) / fields 计算；
2. 后端聚合 — ``get_schema`` / ``_allowed_env_keys`` / ``/api/registered-services``
   对 extra_fields（账号密码/网关）与 literature 分类的支持；
3. Provider 层 — 新增源的注册 bootstrap、可用性判定与 search 映射（离线 mock）。
"""

import asyncio

import pytest

import agent.service_credentials as sc
from agent.service_credentials import (
    get_registered_services,
    get_service_fields,
    register_service,
)


# ── 1. 凭证层 ───────────────────────────────────────────────────────


def test_register_service_category_and_dict_extra_fields(monkeypatch):
    monkeypatch.setattr(sc, "_SERVICES", {})
    register_service(
        "libx",
        api_key_env_var="LIBX_KEY",
        base_url_env_var="LIBX_GATEWAY",
        label="LibX",
        category="literature",
        description="demo",
        url="https://libx.example",
        extra_fields=[
            {"key": "LIBX_USERNAME", "label": "账号", "secret": False},
            {"key": "LIBX_PASSWORD", "label": "密码", "secret": True},
        ],
    )
    reg = get_registered_services()
    meta = reg["libx"]
    assert meta["category"] == "literature"
    assert meta["description"] == "demo"
    assert meta["url"] == "https://libx.example"
    keys = [f["key"] for f in meta["fields"]]
    assert keys == ["LIBX_KEY", "LIBX_GATEWAY", "LIBX_USERNAME", "LIBX_PASSWORD"]
    by_key = {f["key"]: f for f in meta["fields"]}
    assert by_key["LIBX_KEY"]["secret"] is True
    assert by_key["LIBX_GATEWAY"]["secret"] is False
    assert by_key["LIBX_USERNAME"]["secret"] is False
    assert by_key["LIBX_PASSWORD"]["secret"] is True
    assert by_key["LIBX_PASSWORD"]["label"] == "密码"


def test_register_service_bare_extra_field_secret_inference(monkeypatch):
    monkeypatch.setattr(sc, "_SERVICES", {})
    register_service("liby", extra_fields=["LIBY_USER", "LIBY_TOKEN"])
    by_key = {f["key"]: f for f in get_service_fields("liby")}
    assert by_key["LIBY_USER"]["secret"] is False   # 无敏感词
    assert by_key["LIBY_TOKEN"]["secret"] is True   # TOKEN → secret


def test_register_service_dedupes_base_url_in_extra_fields(monkeypatch):
    """网关 env var 同时出现在 base_url_env_var 与 extra_fields 时只渲染一次。"""
    monkeypatch.setattr(sc, "_SERVICES", {})
    register_service(
        "libz",
        base_url_env_var="LIBZ_GATEWAY",
        extra_fields=["LIBZ_GATEWAY", "LIBZ_PASSWORD"],
    )
    keys = [f["key"] for f in get_service_fields("libz")]
    assert keys.count("LIBZ_GATEWAY") == 1


def test_get_registered_services_defaults_category_services(monkeypatch):
    monkeypatch.setattr(sc, "_SERVICES", {})
    register_service("plain", api_key_env_var="PLAIN_KEY")
    assert get_registered_services()["plain"]["category"] == "services"


# ── 2. 后端聚合（config blueprint） ────────────────────────────────


def _fake_registry():
    return {
        "libx": {
            "label": "LibX",
            "category": "literature",
            "url": "https://libx.example",
            "fields": [
                {"key": "LIBX_KEY", "kind": "api_key", "label": "LibX API Key", "secret": True},
                {"key": "LIBX_GATEWAY", "kind": "base_url", "label": "LibX Base URL", "secret": False},
                {"key": "LIBX_USERNAME", "kind": "extra", "label": "账号", "secret": False},
                {"key": "LIBX_PASSWORD", "kind": "extra", "label": "密码", "secret": True},
            ],
        },
    }


def test_get_schema_includes_extra_fields_and_literature_category(monkeypatch):
    import hermes_cli.blueprints.config as cfg
    import agent.service_credentials as sc2

    monkeypatch.setattr(sc2, "get_registered_services", _fake_registry)
    schema = asyncio.run(cfg.get_schema())
    fields = schema["fields"]
    assert fields["services.libx.api_key"]["env_var"] == "LIBX_KEY"
    assert fields["services.libx.api_key"]["category"] == "literature"
    assert fields["services.libx.api_key"]["secret"] is True
    assert fields["services.libx.base_url"]["env_var"] == "LIBX_GATEWAY"
    # extra_fields（账号/密码）此前完全缺席 schema — 本轮修复的核心断言
    assert fields["services.libx.libx_username"]["env_var"] == "LIBX_USERNAME"
    assert "secret" not in fields["services.libx.libx_username"]
    assert fields["services.libx.libx_password"]["secret"] is True
    assert "literature" in schema["category_order"]


def test_allowed_env_keys_include_extra_fields(monkeypatch):
    import hermes_cli.blueprints.config as cfg
    import agent.service_credentials as sc2

    monkeypatch.setattr(sc2, "get_registered_services", _fake_registry)
    keys = cfg._allowed_env_keys()
    for k in ("LIBX_KEY", "LIBX_GATEWAY", "LIBX_USERNAME", "LIBX_PASSWORD"):
        assert k in keys, f"{k} 应在 PUT /api/env 白名单中"


def test_registered_services_endpoint(monkeypatch):
    import hermes_cli.blueprints.config as cfg
    import agent.service_credentials as sc2

    monkeypatch.setattr(sc2, "get_registered_services", _fake_registry)
    resp = asyncio.run(cfg.get_registered_services_endpoint())
    assert "libx" in resp["services"]
    assert resp["services"]["libx"]["category"] == "literature"
    assert len(resp["services"]["libx"]["fields"]) == 4


def test_real_registry_exposes_literature_credential_fields():
    """真实注册表（import 各 provider 后）必须让账号密码字段可写、可渲染。"""
    from agent.literature_registry import bootstrap_builtin_providers

    bootstrap_builtin_providers()
    import hermes_cli.blueprints.config as cfg

    keys = cfg._allowed_env_keys()
    for k in (
        "CNKI_USERNAME", "CNKI_PASSWORD",
        "WANFANG_USER", "WANFANG_PASSWORD",
        "VIP_GATEWAY_URL", "VIP_USERNAME", "VIP_PASSWORD",
        "EBSCO_USER_ID", "EBSCO_PASSWORD", "EBSCO_PROFILE",
        "IEEE_API_KEY", "SCOPUS_API_KEY", "SPRINGER_API_KEY", "WOS_API_KEY",
        "SCIENCEDIRECT_API_KEY", "S2_API_KEY",
    ):
        assert k in keys, f"{k} 应在 PUT /api/env 白名单中"

    reg = get_registered_services()
    lit = {sid for sid, m in reg.items() if m.get("category") == "literature"}
    assert {"cnki", "wanfang", "vip", "wos", "scopus", "sciencedirect",
            "ieee", "springer", "ebsco", "semanticscholar"} <= lit


# ── 3. Provider 层 ─────────────────────────────────────────────────


def test_bootstrap_registers_all_seventeen_providers():
    from agent import literature_registry as lr

    lr._reset_for_tests()
    try:
        lr.bootstrap_builtin_providers()
        names = {p.name for p in lr.list_providers()}
        assert names == {
            "openalex", "crossref", "pubmed", "arxiv", "semanticscholar",
            "europepmc", "doaj", "core",
            "cnki", "wanfang", "vip", "wos", "scopus",
            "sciencedirect", "ieee", "springer", "ebsco",
        }
    finally:
        lr._reset_for_tests()
        lr.bootstrap_builtin_providers()


def test_free_providers_always_available():
    from agent.literature_providers import (
        ArxivProvider, EuropePmcProvider, PubMedProvider, SemanticScholarProvider,
    )

    for cls in (PubMedProvider, ArxivProvider, SemanticScholarProvider, EuropePmcProvider):
        assert cls().is_available() is True


def test_paid_providers_unavailable_without_credentials(monkeypatch):
    for var in ("IEEE_API_KEY", "SCOPUS_API_KEY", "SCIENCEDIRECT_API_KEY",
                "SPRINGER_API_KEY", "WOS_API_KEY", "VIP_GATEWAY_URL",
                "EBSCO_USER_ID", "EBSCO_PASSWORD", "EBSCO_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sc, "_load_user_services", lambda: {})

    from agent.literature_providers import (
        EbscoProvider, IeeeProvider, ScienceDirectProvider,
        ScopusProvider, SpringerProvider, VipProvider, WosProvider,
    )

    for cls in (IeeeProvider, ScopusProvider, ScienceDirectProvider,
                SpringerProvider, WosProvider, VipProvider, EbscoProvider):
        p = cls()
        assert p.is_available() is False, f"{p.name} 无凭证时不应可用"
        res = p.search("query")
        assert res["success"] is False
        assert "未配置" in res["error"]


def test_pubmed_search_maps_records(monkeypatch):
    import agent.literature_providers.pubmed as pm

    calls = []

    def fake_get_json(url, *, params=None, headers=None, timeout=20):
        calls.append(url)
        if "esearch" in url:
            return {"ok": True, "data": {"esearchresult": {"idlist": ["11", "22"]}}}
        return {
            "ok": True,
            "data": {
                "result": {
                    "11": {
                        "title": "论文甲",
                        "authors": [{"name": "张三"}],
                        "pubdate": "2024 Jan",
                        "fulljournalname": "期刊A",
                        "articleids": [{"idtype": "doi", "value": "10.1/x"}],
                    },
                    "22": {"title": "", "authors": []},
                }
            },
        }

    monkeypatch.setattr(pm, "http_get_json", fake_get_json)
    res = pm.PubMedProvider().search("test", limit=5)
    assert res["success"] is True
    papers = res["data"]["papers"]
    assert len(papers) == 1  # 空标题被过滤
    assert papers[0]["title"] == "论文甲"
    assert papers[0]["doi"] == "10.1/x"
    assert papers[0]["year"] == "2024"
    assert papers[0]["source"] == "pubmed"
    assert len(calls) == 2  # esearch + esummary


def test_arxiv_search_parses_atom(monkeypatch):
    import agent.literature_providers.arxiv as ax

    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2401.00001v1</id>
        <title>Deep Widgets</title>
        <summary>An abstract.</summary>
        <published>2024-01-02T00:00:00Z</published>
        <author><name>Alice</name></author>
        <author><name>Bob</name></author>
        <link title="doi" href="https://doi.org/10.5/abc"/>
      </entry>
    </feed>"""

    monkeypatch.setattr(
        ax, "http_get_text",
        lambda url, *, params=None, headers=None, timeout=20: {"ok": True, "text": atom},
    )
    res = ax.ArxivProvider().search("widgets")
    assert res["success"] is True
    p = res["data"]["papers"][0]
    assert p["title"] == "Deep Widgets"
    assert p["authors"] == ["Alice", "Bob"]
    assert p["year"] == "2024"
    assert p["doi"] == "10.5/abc"
    assert p["source"] == "arxiv"


def test_ieee_search_maps_records(monkeypatch):
    import agent.literature_providers.ieee as ie

    monkeypatch.setattr(ie, "get_api_key", lambda sid: "k123")
    monkeypatch.setattr(
        ie, "http_get_json",
        lambda url, *, params=None, headers=None, timeout=20: {
            "ok": True,
            "data": {
                "articles": [
                    {
                        "title": "Chip Design",
                        "authors": {"authors": [{"full_name": "C. Engineer"}]},
                        "publication_year": 2023,
                        "publication_title": "IEEE Trans.",
                        "abstract": "abs",
                        "citing_paper_count": 7,
                        "html_url": "https://ieee.example/1",
                        "doi": "10.9/z",
                    }
                ]
            },
        },
    )
    res = ie.IeeeProvider().search("chip")
    assert res["success"] is True
    p = res["data"]["papers"][0]
    assert p["cited_count"] == 7
    assert p["journal"] == "IEEE Trans."
    assert p["source"] == "ieee"


def test_vip_gateway_search(monkeypatch):
    import agent.literature_providers.vip as vp

    monkeypatch.setenv("VIP_GATEWAY_URL", "https://gw.example/")
    monkeypatch.setenv("VIP_USERNAME", "u")
    monkeypatch.setenv("VIP_PASSWORD", "p")
    monkeypatch.setattr(sc, "_load_user_services", lambda: {})

    captured = {}

    def fake_post(url, *, json_body=None, headers=None, timeout=20):
        captured["url"] = url
        captured["body"] = json_body
        return {
            "ok": True,
            "data": {"papers": [{"title": "中文论文", "authors": ["李四"], "year": 2022}]},
        }

    monkeypatch.setattr(vp, "http_post_json", fake_post)
    p = vp.VipProvider()
    assert p.is_available() is True
    res = p.search("检索词", limit=3)
    assert res["success"] is True
    assert res["data"]["papers"][0]["title"] == "中文论文"
    assert res["data"]["papers"][0]["source"] == "vip"
    assert captured["url"] == "https://gw.example/search"
    assert captured["body"]["username"] == "u"
    assert captured["body"]["limit"] == 3


def test_legacy_preference_paid_before_free():
    from agent.literature_registry import _LEGACY_PREFERENCE

    order = list(_LEGACY_PREFERENCE)
    assert order.index("cnki") < order.index("openalex")
    assert order.index("vip") < order.index("openalex")
    assert order.index("wos") < order.index("openalex")
    assert set(order) >= {
        "cnki", "wanfang", "vip", "wos", "scopus", "sciencedirect",
        "ieee", "springer", "ebsco", "openalex", "crossref",
        "semanticscholar", "pubmed", "arxiv", "europepmc",
    }
