"""Test the PluginContext.register_literature_provider entry point.

Verifies that a user-authored provider implementing LiteratureProvider is
registered into the literature registry via ``PluginContext``, while a
non-provider object is ignored (the framework's zero-vendor-name rule: only
the plugin self-declares its provider).
"""

import pytest

from agent.literature_provider import LiteratureProvider
from agent.literature_registry import _reset_for_tests, get_provider, register_provider
from hermes_cli.plugins import PluginContext


class _StubManifest:
    name = "demo-literature-plugin"
    key = "demo-literature-plugin"


@pytest.fixture(autouse=True)
def _reset_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()


def _make_ctx():
    # register_literature_provider is self-contained (only uses self.manifest.name
    # and the literature registry); manager is not touched by it.
    return PluginContext(_StubManifest(), None)


class MyLiteratureProvider(LiteratureProvider):
    @property
    def name(self):
        return "myprov"

    def is_available(self):
        return True

    def search(self, query, limit=10):
        return {"success": True, "data": {"papers": []}}


def test_register_literature_provider_registers():
    ctx = _make_ctx()
    ctx.register_literature_provider(MyLiteratureProvider())
    assert get_provider("myprov") is not None


def test_register_literature_provider_ignores_non_provider():
    ctx = _make_ctx()

    class NotAProvider:
        pass

    # Must not raise — just logs a warning and ignores.
    ctx.register_literature_provider(NotAProvider())
    assert get_provider("myprov") is None


def test_register_literature_provider_overrides_same_name():
    ctx = _make_ctx()
    ctx.register_literature_provider(MyLiteratureProvider())
    # Re-register (last-writer-wins) should not error.
    ctx.register_literature_provider(MyLiteratureProvider())
    assert get_provider("myprov") is not None
