"""
Cron subcommand for hermes CLI.

Handles standalone cron management commands like list, create, edit,
pause/resume/run/remove, status, and tick.
"""

import logging

logger = logging.getLogger(__name__)
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from vermes_cli.colors import Colors, color


def _normalize_skills(single_skill=None, skills: Optional[Iterable[str]] = None) -> Optional[List[str]]:
    if skills is None:
        if single_skill is None:
            return None
        raw_items = [single_skill]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _cron_api(**kwargs):
    from tools.cronjob_tools import cronjob as cronjob_tool

    return json.loads(cronjob_tool(**kwargs))


def cron_list(show_all: bool = False):
    """List all scheduled jobs."""
    from cron.jobs import list_jobs

    jobs = list_jobs(include_disabled=show_all)

    if not jobs:
        logger.info(color("No scheduled jobs.", Colors.DIM))
        logger.info(color("Create one with 'hermes cron create ...' or the /cron command in chat.", Colors.DIM))
        return

    logger.info()
    logger.info(color("┌─────────────────────────────────────────────────────────────────────────┐", Colors.CYAN))
    logger.info(color("│                         Scheduled Jobs                                  │", Colors.CYAN))
    logger.info(color("└─────────────────────────────────────────────────────────────────────────┘", Colors.CYAN))
    logger.info()

    for job in jobs:
        job_id = job.get("id", "?")
        name = job.get("name", "(unnamed)")
        schedule = job.get("schedule_display", job.get("schedule", {}).get("value", "?"))
        state = job.get("state", "scheduled" if job.get("enabled", True) else "paused")
        next_run = job.get("next_run_at", "?")

        repeat_info = job.get("repeat", {})
        repeat_times = repeat_info.get("times")
        repeat_completed = repeat_info.get("completed", 0)
        repeat_str = f"{repeat_completed}/{repeat_times}" if repeat_times else "∞"

        deliver = job.get("deliver", ["local"])
        if isinstance(deliver, str):
            deliver = [deliver]
        deliver_str = ", ".join(deliver)

        skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])
        if state == "paused":
            status = color("[paused]", Colors.YELLOW)
        elif state == "completed":
            status = color("[completed]", Colors.BLUE)
        elif job.get("enabled", True):
            status = color("[active]", Colors.GREEN)
        else:
            status = color("[disabled]", Colors.RED)

        logger.info(f"  {color(job_id, Colors.YELLOW)} {status}")
        logger.info(f"    Name:      {name}")
        logger.info(f"    Schedule:  {schedule}")
        logger.info(f"    Repeat:    {repeat_str}")
        logger.info(f"    Next run:  {next_run}")
        logger.info(f"    Deliver:   {deliver_str}")
        if skills:
            logger.info(f"    Skills:    {', '.join(skills)}")
        script = job.get("script")
        if script:
            logger.info(f"    Script:    {script}")
        if job.get("no_agent"):
            logger.info(f"    Mode:      {color('no-agent', Colors.DIM)} (script stdout delivered directly)")
        workdir = job.get("workdir")
        if workdir:
            logger.info(f"    Workdir:   {workdir}")
        profile = job.get("profile")
        if profile:
            logger.info(f"    Profile:   {profile}")

        # Execution history
        last_status = job.get("last_status")
        if last_status:
            last_run = job.get("last_run_at", "?")
            if last_status == "ok":
                status_display = color("ok", Colors.GREEN)
            else:
                status_display = color(f"{last_status}: {job.get('last_error', '?')}", Colors.RED)
            logger.info(f"    Last run:  {last_run}  {status_display}")

        delivery_err = job.get("last_delivery_error")
        if delivery_err:
            logger.info(f"    {color('⚠ Delivery failed:', Colors.YELLOW)} {delivery_err}")

        logger.info()

    from vermes_cli.gateway import find_gateway_pids
    if not find_gateway_pids():
        logger.info(color("  ⚠  Gateway is not running — jobs won't fire automatically.", Colors.YELLOW))
        logger.info(color("     Start it with: hermes gateway install", Colors.DIM))
        logger.info(color("                    sudo hermes gateway install --system  # Linux servers", Colors.DIM))
        logger.info()


def cron_tick():
    """Run due jobs once and exit."""
    from cron.scheduler import tick
    tick(verbose=True)


def cron_status():
    """Show cron execution status."""
    from cron.jobs import list_jobs
    from vermes_cli.gateway import find_gateway_pids

    logger.info()

    pids = find_gateway_pids()
    if pids:
        logger.info(color("✓ Gateway is running — cron jobs will fire automatically", Colors.GREEN))
        logger.info(f"  PID: {', '.join(map(str, pids))}")
    else:
        logger.info(color("✗ Gateway is not running — cron jobs will NOT fire", Colors.RED))
        logger.info()
        logger.info("  To enable automatic execution:")
        logger.info("    hermes gateway install    # Install as a user service")
        logger.info("    sudo hermes gateway install --system  # Linux servers: boot-time system service")
        logger.info("    hermes gateway            # Or run in foreground")

    logger.info()

    jobs = list_jobs(include_disabled=False)
    if jobs:
        next_runs = [j.get("next_run_at") for j in jobs if j.get("next_run_at")]
        logger.info(f"  {len(jobs)} active job(s)")
        if next_runs:
            logger.info(f"  Next run: {min(next_runs)}")
    else:
        logger.info("  No active jobs")

    logger.info()


def cron_create(args):
    result = _cron_api(
        action="create",
        schedule=args.schedule,
        prompt=args.prompt,
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skill=getattr(args, "skill", None),
        skills=_normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None)),
        script=getattr(args, "script", None),
        workdir=getattr(args, "workdir", None),
        profile=getattr(args, "profile", None),
        no_agent=getattr(args, "no_agent", False) or None,
    )
    if not result.get("success"):
        logger.info(color(f"Failed to create job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1
    logger.info(color(f"Created job: {result['job_id']}", Colors.GREEN))
    logger.info(f"  Name: {result['name']}")
    logger.info(f"  Schedule: {result['schedule']}")
    if result.get("skills"):
        logger.info(f"  Skills: {', '.join(result['skills'])}")
    job_data = result.get("job", {})
    if job_data.get("script"):
        logger.info(f"  Script: {job_data['script']}")
    if job_data.get("no_agent"):
        logger.info("  Mode: no-agent (script stdout delivered directly)")
    if job_data.get("workdir"):
        logger.info(f"  Workdir: {job_data['workdir']}")
    if job_data.get("profile"):
        logger.info(f"  Profile: {job_data['profile']}")
    logger.info(f"  Next run: {result['next_run_at']}")
    return 0


def cron_edit(args):
    from cron.jobs import AmbiguousJobReference, resolve_job_ref



    try:
        job = resolve_job_ref(args.job_id)
    except AmbiguousJobReference as exc:
        logger.info(color(str(exc), Colors.RED))
        for m in exc.matches:
            logger.info(f"  {m['id']}  (name: {m.get('name')!r})")
        return 1
    if not job:
        logger.info(color(f"Job not found: {args.job_id}", Colors.RED))
        return 1

    existing_skills = list(job.get("skills") or ([] if not job.get("skill") else [job.get("skill")]))
    replacement_skills = _normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None))
    add_skills = _normalize_skills(None, getattr(args, "add_skills", None)) or []
    remove_skills = set(_normalize_skills(None, getattr(args, "remove_skills", None)) or [])

    final_skills = None
    if getattr(args, "clear_skills", False):
        final_skills = []
    elif replacement_skills is not None:
        final_skills = replacement_skills
    elif add_skills or remove_skills:
        final_skills = [skill for skill in existing_skills if skill not in remove_skills]
        for skill in add_skills:
            if skill not in final_skills:
                final_skills.append(skill)

    result = _cron_api(
        action="update",
        job_id=args.job_id,
        schedule=getattr(args, "schedule", None),
        prompt=getattr(args, "prompt", None),
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skills=final_skills,
        script=getattr(args, "script", None),
        workdir=getattr(args, "workdir", None),
        profile=getattr(args, "profile", None),
        no_agent=getattr(args, "no_agent", None),
    )
    if not result.get("success"):
        logger.info(color(f"Failed to update job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1

    updated = result["job"]
    logger.info(color(f"Updated job: {updated['job_id']}", Colors.GREEN))
    logger.info(f"  Name: {updated['name']}")
    logger.info(f"  Schedule: {updated['schedule']}")
    if updated.get("skills"):
        logger.info(f"  Skills: {', '.join(updated['skills'])}")
    else:
        logger.info("  Skills: none")
    if updated.get("script"):
        logger.info(f"  Script: {updated['script']}")
    if updated.get("no_agent"):
        logger.info("  Mode: no-agent (script stdout delivered directly)")
    if updated.get("workdir"):
        logger.info(f"  Workdir: {updated['workdir']}")
    if updated.get("profile"):
        logger.info(f"  Profile: {updated['profile']}")
    return 0


def _job_action(action: str, job_id: str, success_verb: str) -> int:
    result = _cron_api(action=action, job_id=job_id)
    if not result.get("success"):
        logger.info(color(f"Failed to {action} job: {result.get('error', 'unknown error')}", Colors.RED))
        return 1
    job = result.get("job") or result.get("removed_job") or {}
    logger.info(color(f"{success_verb} job: {job.get('name', job_id)} ({job_id})", Colors.GREEN))
    if action in {"resume", "run"} and result.get("job", {}).get("next_run_at"):
        logger.info(f"  Next run: {result['job']['next_run_at']}")
    if action == "run":
        logger.info("  It will run on the next scheduler tick.")
    return 0


def cron_command(args):
    """Handle cron subcommands."""
    subcmd = getattr(args, 'cron_command', None)

    if subcmd is None or subcmd == "list":
        show_all = getattr(args, 'all', False)
        cron_list(show_all)
        return 0

    if subcmd == "status":
        cron_status()
        return 0

    if subcmd == "tick":
        cron_tick()
        return 0

    if subcmd in {"create", "add"}:
        return cron_create(args)

    if subcmd == "edit":
        return cron_edit(args)

    if subcmd == "pause":
        return _job_action("pause", args.job_id, "Paused")

    if subcmd == "resume":
        return _job_action("resume", args.job_id, "Resumed")

    if subcmd == "run":
        return _job_action("run", args.job_id, "Triggered")

    if subcmd in {"remove", "rm", "delete"}:
        return _job_action("remove", args.job_id, "Removed")

    logger.info(f"Unknown cron command: {subcmd}")
    logger.info("Usage: hermes cron [list|create|edit|pause|resume|run|remove|status|tick]")
    sys.exit(1)
