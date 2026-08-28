"""FastAPI router exposing the curated capability catalog (P0 feature).

Read-only endpoint backed by ``vermes_cli.capabilities.manifest``. Adds a
single route: ``GET /api/v1/capabilities`` (with optional ``?refresh=true``
to force re-fetch from models.dev). Mounted by ``web_server.py`` via
``app.include_router``. No existing routes or logic are modified.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from vermes_cli.capabilities.manifest import generate_capability_manifest

router = APIRouter()


@router.get("/api/v1/capabilities")
def get_capabilities(
    refresh: bool = Query(False, description="Force re-fetch from models.dev"),
):
    """Return the curated capability manifest (pinned / mainstream / longtail)."""
    return generate_capability_manifest(refresh=refresh)
