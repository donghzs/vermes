"""Unified service-credential accessor (Part 2 of the unified memory base).

Verifies every API-needing component can read its credentials from the
user's central config (``config["services"]``) or a conventional env var,
instead of scattering ``os.environ.get("XXX_API_KEY")`` reads. The framework
is vendor-agnostic: it contains no vendor names — plugins declare their own
service metadata via ``register_service``.
"""
import agent.service_credentials as sc
from agent.service_credentials import (
    get_api_key,
    get_service_credentials,
    register_service,
    get_registered_services,
)


def test_get_api_key_convention_env(monkeypatch):
    monkeypatch.delenv("MYPLUGIN_API_KEY", raising=False)
    monkeypatch.setenv("MYPLUGIN_API_KEY", "sekret")
    assert get_api_key("myplugin") == "sekret"


def test_get_api_key_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("NOPE_API_KEY", raising=False)
    assert get_api_key("nope", default="dflt") == "dflt"
    assert get_api_key("nope") is None


def test_get_api_key_nonconventional_env_override(monkeypatch):
    monkeypatch.delenv("BRV_API_KEY", raising=False)
    monkeypatch.setenv("BRV_API_KEY", "brvsekret")
    assert get_api_key("byterover", env_var="BRV_API_KEY") == "brvsekret"


def test_get_api_key_config_precedence_over_env(monkeypatch):
    monkeypatch.delenv("ZZ_API_KEY", raising=False)
    monkeypatch.setenv("ZZ_API_KEY", "envval")
    monkeypatch.setattr(sc, "_load_user_services", lambda: {"zz": {"api_key": "cfgval"}})
    assert get_api_key("zz") == "cfgval"


def test_get_api_key_ui_flow_reads_env_when_config_empty(monkeypatch):
    """C3 contract: the frontend writes to .env (env), and in the normal UI
    flow the central config['services'] is empty — so the env fallback is the
    path that actually delivers the user-set key at runtime."""
    monkeypatch.delenv("UI_API_KEY", raising=False)
    monkeypatch.setenv("UI_API_KEY", "uival")
    # empty central config (what the UI writes to, normally)
    monkeypatch.setattr(sc, "_load_user_services", lambda: {})
    register_service("ui", api_key_env_var="UI_API_KEY")
    assert get_api_key("ui") == "uival"


def test_get_api_key_env_var_name_from_registry(monkeypatch):
    """The env var name used at read time matches what the schema tells the
    frontend to write (registry metadata), closing the config-loop contract."""
    monkeypatch.delenv("WIDGET_KEY", raising=False)
    monkeypatch.setenv("WIDGET_KEY", "wval")
    monkeypatch.setattr(sc, "_load_user_services", lambda: {})
    register_service("widget", api_key_env_var="WIDGET_KEY")
    # no convention-based WIDGET_API_KEY set; must hit the registered env_var
    monkeypatch.delenv("WIDGET_API_KEY", raising=False)
    assert get_api_key("widget") == "wval"


def test_get_service_credentials_merges_base_url(monkeypatch):
    monkeypatch.setattr(
        sc, "_load_user_services",
        lambda: {"vv": {"api_key": "k", "base_url": "https://v.example"}},
    )
    creds = get_service_credentials("vv")
    assert creds["api_key"] == "k"
    assert creds["base_url"] == "https://v.example"


def test_register_service_populates_registry(monkeypatch):
    monkeypatch.setattr(sc, "_SERVICES", {})
    register_service(
        "foo", api_key_env_var="FOO_KEY", base_url_env_var="FOO_URL", label="Foo"
    )
    reg = get_registered_services()
    assert reg["foo"]["api_key_env_var"] == "FOO_KEY"
    assert reg["foo"]["base_url_env_var"] == "FOO_URL"
    assert reg["foo"]["label"] == "Foo"


def test_plugins_registered_their_services():
    # The memory plugins register themselves at import time (each plugin's
    # entrypoint calls register_service), so the desktop frontend can render
    # ONE "services" section instead of per-plugin forms. Plugins needing an
    # uninstalled SDK are skipped (they can't be imported in this env anyway).
    import importlib

    mods = {
        "supermemory": "plugins.memory.supermemory",
        "mem0": "plugins.memory.mem0",
        "openviking": "plugins.memory.openviking",
        "retaindb": "plugins.memory.retaindb",
        "byterover": "plugins.memory.byterover",
        "hindsight": "plugins.memory.hindsight",
        "honcho": "plugins.memory.honcho",
    }
    imported = []
    for sid, mod in mods.items():
        try:
            importlib.import_module(mod)
            imported.append(sid)
        except Exception:
            pass

    reg = get_registered_services()
    assert imported, "at least one memory plugin should be importable in this env"
    for sid in imported:
        assert sid in reg, f"{sid} should be registered via register_service"
