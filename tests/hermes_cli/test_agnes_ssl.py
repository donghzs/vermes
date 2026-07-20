"""Agnes SSL/endpoint resolution tests.

Verifies the source-level fix for the Agnes SSL problem: the plugin now
defaults to a pinned certifi CA bundle (instead of relying on the system CA
store, which is frequently missing a chain on sandboxed/macOS/self-hosted
setups) and exposes two escape hatches:

  - AGNES_BASE_URL      override the endpoint (self-hosted / behind a proxy)
  - AGNES_SSL_VERIFY    set to "false" to disable verification (internal certs)

Both the image_gen and video_gen Agnes plugins must behave identically.
"""

import importlib

import pytest


@pytest.fixture(params=["image_gen", "video_gen"])
def agnes_module(request):
    pkg = request.param
    mod = importlib.import_module(f"plugins.{pkg}.agnes")
    yield mod
    # Clean up any env we set so tests stay isolated.
    for var in ("AGNES_BASE_URL", "AGNES_SSL_VERIFY"):
        import os
        os.environ.pop(var, None)


def test_resolve_base_url_default(agnes_module):
    import os
    os.environ.pop("AGNES_BASE_URL", None)
    assert agnes_module._resolve_base_url() == agnes_module.BASE_URL


def test_resolve_base_url_override(agnes_module, monkeypatch):
    monkeypatch.setenv("AGNES_BASE_URL", "https://agnes.internal/v1/")
    assert agnes_module._resolve_base_url() == "https://agnes.internal/v1"


def test_resolve_verify_false(agnes_module, monkeypatch):
    monkeypatch.setenv("AGNES_SSL_VERIFY", "false")
    assert agnes_module._resolve_verify() is False


def test_resolve_verify_disabled_variants(agnes_module, monkeypatch):
    for v in ("0", "no", "NO", "False"):
        monkeypatch.setenv("AGNES_SSL_VERIFY", v)
        assert agnes_module._resolve_verify() is False


def test_resolve_verify_default_pins_certifi(agnes_module, monkeypatch):
    # Default (unset or "true") must pin the certifi CA bundle, not rely on
    # the system store — this is the core of the SSL fix.
    monkeypatch.delenv("AGNES_SSL_VERIFY", raising=False)
    verify = agnes_module._resolve_verify()
    certifi = __import__("certifi")
    assert verify == certifi.where()
