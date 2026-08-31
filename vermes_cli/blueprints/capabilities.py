"""FastAPI router exposing the curated capability catalog (P0 feature).

Read-only endpoint backed by ``vermes_cli.capabilities.manifest``. Adds a
single route: ``GET /api/v1/capabilities`` (with optional ``?refresh=true``
to force re-fetch from models.dev). Mounted by ``web_server.py`` via
``app.include_router``. No existing routes or logic are modified.

G13/G4: also exposes ``GET /api/v1/capabilities/self-check`` returning
structured capability self-assessment (same data as ``get_capability_report_prompt``
but as JSON for frontend consumption).
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Query, Request

from vermes_cli.capabilities.manifest import generate_capability_manifest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/v1/capabilities")
def get_capabilities(
    refresh: bool = Query(False, description="Force re-fetch from models.dev"),
):
    """Return the curated capability manifest (pinned / mainstream / longtail)."""
    return generate_capability_manifest(refresh=refresh)


@router.get("/api/v1/capabilities/self-check")
def get_capability_self_check():
    """G13/G4: Return structured capability self-assessment as JSON.

    Same data source as ``get_capability_report_prompt()`` (M5 system prompt),
    but structured for frontend rendering — two groups: 'skilled' (active/built_in)
    and 'learning' (installed with emergence signals / not_installed with signals).
    """
    try:
        from agent.capability_registry import check_all_capabilities, CapabilityStatus

        report = check_all_capabilities()
        skilled = []
        learning = []

        for cap in report.capabilities:
            entry = {
                "name": cap.name,
                "description": cap.description,
                "status": cap.status.value,
                "emergence_signals": cap.emergence_signals,
                "activated_at": cap.activated_at,
            }
            if cap.status in (CapabilityStatus.ACTIVE, CapabilityStatus.BUILT_IN):
                skilled.append(entry)
            elif cap.emergence_signals > 0 or cap.status == CapabilityStatus.INSTALLED:
                learning.append(entry)

        # J3→M5: include emphasized bricks
        emphasized = []
        try:
            from vermes_cli.capabilities.registry import get_brick_registry
            emphasized = get_brick_registry().emphasized_ids()
        except Exception:
            pass

        return {
            "skilled": skilled,
            "learning": learning,
            "emphasized": emphasized,
            "total": len(report.capabilities),
        }
    except Exception as e:
        logger.error("self-check failed: %s", e)
        return {"skilled": [], "learning": [], "emphasized": [], "total": 0, "error": str(e)}


def register_to(app):
    """Register the capability catalog route at module level (before SPA catch-all).

    Must be called alongside the other ``blueprints.*.register_to(app)`` calls
    (module import time), NOT inside ``start_server()`` — otherwise the SPA
    catch-all ``/{full_path:path}`` route (mounted at module level via
    ``mount_spa(app)``) shadows the route and returns 404.
    """
    app.include_router(router)
