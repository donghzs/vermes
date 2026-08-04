"""Phase 3 regression: variant endpoint handlers must not ship broken imports.

Context: the variant_rollback endpoint handler originally imported a
non-existent class name ``EmergentChangeManager`` from ``agent.emergent_change``
(the real class is ``EmergentChangePipeline``, exposed via ``get_pipeline()``).
The store-level no-mock tests (test_variant_store.py) exercised
``variant_store.rollback_variant`` but never the chat.py handler's import
line, so the bug shipped green — caught only at runtime via E2E.

These tests call the endpoint handlers directly with a fake Request so the
import lines inside the handler bodies execute for real. No server, no mock
of the import path. If a future rename breaks the import, the handler's broad
``except`` would return an ImportError string instead of the expected
domain error, and these tests fail.
"""

import pytest

from vermes_cli.blueprints.chat import variant_rollback, variant_list


class _FakeRequest:
    """Minimal stand-in for starlette.Request: only ``await req.json()``."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


# ── variant_rollback: import path must resolve ────────────────────────

@pytest.mark.asyncio
async def test_rollback_handler_import_path_resolves():
    """If the handler's `from agent.emergent_change import ...` line is broken
    (e.g. wrong class name), the broad except returns an ImportError string.
    With a valid import + a nonexistent variant, it must return the domain
    'variant ... not found' error instead — proving the import succeeded."""
    # nonexistent processor + bogus hash → get_variant_content returns None
    req = _FakeRequest({"hash": "deadbeef"})
    resp = await variant_rollback("nonexistent_proc_xyz", req)
    assert resp["ok"] is False
    # The error must be the domain "not found" — NOT an ImportError leak.
    assert resp["error"] == "variant deadbeef not found", (
        f"handler import path is broken (got: {resp['error']!r})"
    )


@pytest.mark.asyncio
async def test_rollback_handler_rejects_missing_hash():
    """hash is required — early return before any import-dependent work."""
    req = _FakeRequest({})
    resp = await variant_rollback("any_proc", req)
    assert resp == {"ok": False, "error": "hash is required"}


# ── variant_list: import path must resolve ────────────────────────────

@pytest.mark.asyncio
async def test_list_handler_import_path_resolves():
    """variant_list imports `list_variants` from agent.variant_store; a
    nonexistent processor yields an empty list, not an import error."""
    resp = await variant_list("nonexistent_proc_xyz")
    assert resp["ok"] is True
    assert resp["variants"] == []
    assert resp["count"] == 0
