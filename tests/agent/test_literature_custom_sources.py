"""自定义文献源（用户自建机构内部文献库）测试。

覆盖：
1. 存储层 — 增删改、id/命名空间 env var 生成、更新保持 id、空标签校验；
2. 凭证层合并 — get_registered_services 自动纳入自定义源（category=literature）；
3. 通用 Provider — 端点/认证(bearer/basic/none)/不可用判定/容错解析/网关字段覆盖；
4. 注册表 — bootstrap_custom_providers 注册 + get_provider_by_ref 按名/标签解析；
5. 后端 CRUD 端点 — list/create/update/delete（token 保护打桩）。
"""

import importlib

import pytest

import agent.literature_custom_store as cs
from agent.literature_custom_store import (
    add_custom_source,
    delete_custom_source,
    get_custom_service_entries,
    list_custom_sources,
    update_custom_source,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """隔离的自定义源存储（指向临时 VERMES_HOME）。"""
    monkeypatch.setenv("VERMES_HOME", str(tmp_path))
    importlib.reload(cs)
    cs._store_path = None
    cs._cache = {"mtime": 0.0, "data": None}
    yield cs


def test_add_generates_namespaced_env_vars(store):
    d = add_custom_source({
        "label": "MyMedLibrary",
        "base_url": "https://api.hospital.example/search",
        "auth_scheme": "bearer",
        "field_types": ["api_key", "user", "password"],
    })
    keys = [f["key"] for f in d["fields"]]
    assert keys == [
        "LIT_MYMEDLIBRARY_API_KEY",
        "LIT_MYMEDLIBRARY_USER",
        "LIT_MYMEDLIBRARY_PASSWORD",
    ]
    # env var 命名空间以 LIT_ 前缀，避免与内置源冲突
    assert all(k.startswith("LIT_") for k in keys)
    assert d["fields"][0]["secret"] is True
    assert d["fields"][1]["secret"] is False


def test_update_keeps_id_and_env_var_names(store):
    d = add_custom_source({"label": "Portal A", "field_types": ["api_key", "user"]})
    sid = d["id"]
    env_before = {f["key"] for f in d["fields"]}
    d2 = update_custom_source(sid, {"label": "Portal A (改名)", "field_types": ["api_key"]})
    assert d2["id"] == sid  # id 稳定，凭证不被孤立
    assert {f["key"] for f in d2["fields"]} <= env_before


def test_empty_label_rejected(store):
    with pytest.raises(ValueError):
        add_custom_source({"label": "   ", "field_types": ["api_key"]})


def test_delete_removes_and_returns_false_when_missing(store):
    d = add_custom_source({"label": "Portal B", "field_types": ["api_key"]})
    assert delete_custom_source(d["id"]) is True
    assert delete_custom_source(d["id"]) is False
    assert list_custom_sources() == []


def test_custom_source_merged_into_registry(store):
    d = add_custom_source({
        "label": "Hospital Portal",
        "url": "https://lib.example",
        "field_types": ["api_key", "base_url", "user", "password"],
    })
    from agent.service_credentials import get_registered_services

    reg = get_registered_services()
    entry = reg.get(d["id"])
    assert entry is not None
    assert entry["category"] == "literature"
    assert entry.get("custom") is True
    assert entry["url"] == "https://lib.example"
    merged_keys = [f["key"] for f in entry["fields"]]
    assert merged_keys == [
        "LIT_HOSPITAL_PORTAL_API_KEY",
        "LIT_HOSPITAL_PORTAL_BASE_URL",
        "LIT_HOSPITAL_PORTAL_USER",
        "LIT_HOSPITAL_PORTAL_PASSWORD",
    ]


def test_get_custom_service_entries_shape(store):
    add_custom_source({"label": "X", "field_types": ["api_key"]})
    entries = get_custom_service_entries()
    assert len(entries) == 1
    e = next(iter(entries.values()))
    assert e["category"] == "literature"
    assert e["custom"] is True
    assert e["fields"][0]["kind"] == "api_key"


# ── 通用 Provider ────────────────────────────────────────────────────────


def test_custom_provider_bearer_attaches_header(store, monkeypatch):
    d = add_custom_source({
        "label": "P", "base_url": "https://api.example/search",
        "auth_scheme": "bearer", "field_types": ["api_key"],
    })
    from agent.literature_providers.custom import CustomHttpProvider
    import agent.literature_providers.custom as cmod

    env = {f["key"]: "KEY123" for f in d["fields"]}
    monkeypatch.setattr("os.environ", env)
    prov = CustomHttpProvider(d)
    assert prov.is_available() is True

    cap = {}
    cmod.http_get_json = lambda url, *, params=None, headers=None, timeout=20: (
        cap.update({"h": headers, "p": params}) or {"ok": True, "data": {"results": [{"title": "T"}]}}
    )
    res = prov.search("q", 5)
    assert res["success"] is True
    assert cap["h"]["Authorization"] == "Bearer KEY123"
    assert cap["p"]["q"] == "q"


def test_custom_provider_basic_auth(store, monkeypatch):
    d = add_custom_source({
        "label": "P", "base_url": "https://api.example/q",
        "auth_scheme": "basic", "field_types": ["user", "password"],
    })
    from agent.literature_providers.custom import CustomHttpProvider
    import agent.literature_providers.custom as cmod

    env = {f["key"]: v for f, v in zip(d["fields"], ["u", "p"])}
    monkeypatch.setattr("os.environ", env)
    prov = CustomHttpProvider(d)
    cap = {}
    cmod.http_get_json = lambda url, *, params=None, headers=None, timeout=20: (
        cap.update({"h": headers}) or {"ok": True, "data": {"records": [{"title": "X"}]}}
    )
    prov.search("q")
    assert cap["h"]["Authorization"].startswith("Basic ")


def test_custom_provider_unavailable_without_creds(store, monkeypatch):
    d = add_custom_source({
        "label": "P", "base_url": "https://api.example/search",
        "auth_scheme": "bearer", "field_types": ["api_key"],
    })
    from agent.literature_providers.custom import CustomHttpProvider
    import agent.literature_providers.custom as cmod

    monkeypatch.setattr("os.environ", {})
    # 桩掉网络，避免沙箱真实联网导致结果不确定
    cmod.http_get_json = lambda *a, **k: {"ok": False, "error": "no network"}
    prov = CustomHttpProvider(d)
    assert prov.is_available() is False
    res = prov.search("q")
    assert res["success"] is False
    assert "未配置" in res["error"] or "error" in res


def test_custom_provider_gateway_field_overrides_endpoint(store, monkeypatch):
    d = add_custom_source({
        "label": "P", "base_url": "https://stored.example/search",
        "auth_scheme": "none", "field_types": ["base_url"],
    })
    from agent.literature_providers.custom import CustomHttpProvider
    import agent.literature_providers.custom as cmod

    env = {d["fields"][0]["key"]: "https://gateway.internal/q"}
    monkeypatch.setattr("os.environ", env)
    prov = CustomHttpProvider(d)
    cap = {}
    cmod.http_get_json = lambda url, *, params=None, headers=None, timeout=20: (
        cap.update({"u": url}) or {"ok": True, "data": {"items": [{"title": "Y"}]}}
    )
    prov.search("q")
    assert cap["u"].startswith("https://gateway.internal/q")


def test_custom_provider_tolerant_parse(store, monkeypatch):
    d = add_custom_source({
        "label": "P", "base_url": "https://api.example/search",
        "auth_scheme": "none", "field_types": [],
    })
    from agent.literature_providers.custom import CustomHttpProvider
    import agent.literature_providers.custom as cmod

    monkeypatch.setattr("os.environ", {})
    prov = CustomHttpProvider(d)
    # dict with non-standard list key + author dict + year string + no-title skipped
    cmod.http_get_json = lambda *a, **k: {"ok": True, "data": {
        "hits": [
            {"name": "Paper", "authors": [{"full_name": "Dr. Lee"}, "Kim"],
             "date": "2022-03", "journal": "JX", "cited_count": 3, "no_title": 1},
            {"title": ""},  # skipped (no title)
        ]
    }}
    res = prov.search("q", 10)
    assert res["success"] is True
    p = res["data"]["papers"][0]
    assert p["title"] == "Paper"
    assert p["authors"] == ["Dr. Lee", "Kim"]
    assert p["year"] == "2022"
    assert p["journal"] == "JX"
    assert p["cited_count"] == 3
    assert p["source"] == d["id"]


# ── 注册表 ──────────────────────────────────────────────────────────────


def test_bootstrap_custom_and_resolve_by_label(store):
    d = add_custom_source({
        "label": "My Hospital Lib", "base_url": "https://api.example/search",
        "auth_scheme": "none", "field_types": [],
    })
    from agent.literature_registry import (
        bootstrap_custom_providers,
        get_provider_by_ref,
    )

    bootstrap_custom_providers()
    assert get_provider_by_ref("My Hospital Lib") is not None
    assert get_provider_by_ref(d["id"]) is not None


# ── 后端 CRUD 端点 ───────────────────────────────────────────────────────


def _auth_ok(monkeypatch):
    import hermes_cli.web_server as ws

    monkeypatch.setattr(ws, "_require_token", lambda request: None)


def test_endpoint_list_returns_sources(store):
    import hermes_cli.blueprints.config as cfg

    add_custom_source({"label": "Portal", "field_types": ["api_key"]})
    resp = _run_sync(cfg.list_literature_custom_sources())
    assert "Portal" in [s["label"] for s in resp["sources"]]


def test_endpoint_create_update_delete(store, monkeypatch):
    import hermes_cli.blueprints.config as cfg

    _auth_ok(monkeypatch)
    fake_req = object()

    # create
    resp = _run_sync(cfg.create_literature_custom_source(
        {"label": "New Lib", "field_types": ["api_key", "user"]}, fake_req))
    assert resp["ok"] is True
    sid = resp["source"]["id"]
    assert {f["key"] for f in resp["source"]["fields"]} == {
        f"LIT_{sid.upper()}_API_KEY", f"LIT_{sid.upper()}_USER"}

    # update
    resp2 = _run_sync(cfg.update_literature_custom_source(
        sid, {"label": "New Lib 2", "field_types": ["api_key"]}, fake_req))
    assert resp2["ok"] is True
    assert resp2["source"]["label"] == "New Lib 2"

    # delete
    resp3 = _run_sync(cfg.delete_literature_custom_source(sid, fake_req))
    assert resp3["ok"] is True
    assert list_custom_sources() == []


def test_endpoint_create_rejects_empty_label(store, monkeypatch):
    import hermes_cli.blueprints.config as cfg

    _auth_ok(monkeypatch)
    with pytest.raises(Exception):  # HTTPException 400
        _run_sync(cfg.create_literature_custom_source({"label": "  "}, object()))


def _run_sync(coro):
    import asyncio

    return asyncio.run(coro)
