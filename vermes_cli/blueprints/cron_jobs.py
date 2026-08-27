"""Blueprint: Cron Jobs（定时任务管理）

Vermes cron job endpoints — manage cron jobs across profiles.
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import BaseModel

from vermes_cli.blueprints.helpers import _fallback_profile_dicts, _profile_to_dict

_log = logging.getLogger(__name__)


# ── models ─────────────────────────────────────────────────────

class CronJobCreate(BaseModel):
    prompt: str
    schedule: str
    name: str = ""
    deliver: str = "local"
    workflow: str = ""  # A2/G6: 关联工作流模板名 → 定时跑该工作流


class CronJobUpdate(BaseModel):
    updates: dict


# ── module-level state ─────────────────────────────────────────

_CRON_PROFILE_LOCK = threading.RLock()


# ── helpers ────────────────────────────────────────────────────

def _cron_profile_dicts() -> List[Dict[str, Any]]:
    """Return dashboard profile records, falling back to a directory scan."""
    from vermes_cli import profiles as profiles_mod
    try:
        return [_profile_to_dict(p) for p in profiles_mod.list_profiles()]
    except Exception:
        _log.exception("Failed to list profiles for cron dashboard; falling back to directory scan")
        return _fallback_profile_dicts(profiles_mod)


def _cron_profile_home(profile: Optional[str]) -> Tuple[str, Path]:
    """Resolve a profile query value to (profile_name, VERMES_HOME)."""
    from vermes_cli import profiles as profiles_mod

    raw = (profile or "default").strip() or "default"
    try:
        canon = profiles_mod.normalize_profile_name(raw)
        profiles_mod.validate_profile_name(canon)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(canon):
        raise HTTPException(status_code=404, detail=f"Profile '{canon}' does not exist.")
    return canon, profiles_mod.get_profile_dir(canon)


def _annotate_cron_job(job: Dict[str, Any], profile: str, home: Path) -> Dict[str, Any]:
    annotated = dict(job)
    annotated["profile"] = profile
    annotated["profile_name"] = profile
    annotated["VERMES_home"] = str(home)
    annotated["is_default_profile"] = profile == "default"
    return annotated


def _call_cron_for_profile(profile: Optional[str], func_name: str, *args, **kwargs):
    """Run cron.jobs helpers against the selected profile's cron directory.

    cron.jobs keeps CRON_DIR/JOBS_FILE/OUTPUT_DIR as module globals resolved
    from the process VERMES_HOME at import time. The dashboard is a single
    process that can inspect many profiles, so temporarily retarget those
    globals while holding a lock and restore them immediately after the call.
    """
    profile_name, home = _cron_profile_home(profile)
    with _CRON_PROFILE_LOCK:
        from cron import jobs as cron_jobs

        old_cron_dir = cron_jobs.CRON_DIR
        old_jobs_file = cron_jobs.JOBS_FILE
        old_output_dir = cron_jobs.OUTPUT_DIR
        cron_jobs.CRON_DIR = home / "cron"
        cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
        cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
        try:
            result = getattr(cron_jobs, func_name)(*args, **kwargs)
        finally:
            cron_jobs.CRON_DIR = old_cron_dir
            cron_jobs.JOBS_FILE = old_jobs_file
            cron_jobs.OUTPUT_DIR = old_output_dir

    if isinstance(result, list):
        return [_annotate_cron_job(j, profile_name, home) for j in result]
    if isinstance(result, dict):
        return _annotate_cron_job(result, profile_name, home)
    return result


def _find_cron_job_profile(job_id: str) -> Optional[str]:
    for profile in _cron_profile_dicts():
        name = str(profile.get("name") or "")
        if not name:
            continue
        jobs = _call_cron_for_profile(name, "list_jobs", True)
        if any(j.get("id") == job_id or j.get("name") == job_id for j in jobs):
            return name
    return None


# ── route handlers ─────────────────────────────────────────────

async def list_cron_jobs(profile: str = "all"):
    requested = (profile or "all").strip()
    if requested.lower() != "all":
        return _call_cron_for_profile(requested, "list_jobs", True)

    jobs: List[Dict[str, Any]] = []
    for item in _cron_profile_dicts():
        name = str(item.get("name") or "")
        if not name:
            continue
        try:
            jobs.extend(_call_cron_for_profile(name, "list_jobs", True))
        except Exception:
            _log.exception("Failed to list cron jobs for profile %s", name)
    return jobs


async def get_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "get_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def create_cron_job(body: CronJobCreate, profile: str = "default"):
    try:
        return _call_cron_for_profile(
            profile,
            "create_job",
            prompt=body.prompt,
            schedule=body.schedule,
            name=body.name,
            deliver=body.deliver,
            workflow=body.workflow or None,
        )
    except Exception as e:
        _log.exception("POST /api/cron/jobs failed")
        raise HTTPException(status_code=400, detail=str(e))


async def update_cron_job(job_id: str, body: CronJobUpdate, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "update_job", job_id, body.updates)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def pause_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "pause_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def resume_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "resume_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def trigger_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _call_cron_for_profile(selected, "trigger_job", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def delete_cron_job(job_id: str, profile: Optional[str] = None):
    selected = profile or _find_cron_job_profile(job_id)
    if not selected:
        raise HTTPException(status_code=404, detail="Job not found")
    if not _call_cron_for_profile(selected, "remove_job", job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register cron job routes on the FastAPI app."""
    app.add_api_route(
        "/api/cron/jobs", list_cron_jobs, methods=["GET"], name="list_cron_jobs"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}", get_cron_job, methods=["GET"], name="get_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs", create_cron_job, methods=["POST"], name="create_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}", update_cron_job, methods=["PUT"], name="update_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}/pause", pause_cron_job, methods=["POST"], name="pause_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}/resume", resume_cron_job, methods=["POST"], name="resume_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}/trigger", trigger_cron_job, methods=["POST"], name="trigger_cron_job"
    )
    app.add_api_route(
        "/api/cron/jobs/{job_id}", delete_cron_job, methods=["DELETE"], name="delete_cron_job"
    )


blueprint = None  # no APIRouter; uses register_to(app) pattern
