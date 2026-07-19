"""Command handlers extracted from cli.py (D3c refactoring).

These functions were originally methods on the ChatConsole class.
They are kept as plain functions with a ``self`` parameter so they can
be bound back onto the class as unbound methods:

    class ChatConsole:
        _handle_cron_command = handle_cron_command
        _handle_browser_command = handle_browser_command
        _handle_handoff_command = handle_handoff_command

All imports are either at module level (standard library + constants)
or lazy (inside the function body) to avoid circular imports with cli.py.
"""

import json
import logging
import os
import time

from urllib.parse import urlparse

# hermes_cli.browser_connect constants — needed by handle_browser_command
from hermes_cli.browser_connect import (
    DEFAULT_BROWSER_CDP_URL,
    manual_chrome_debug_command,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /cron — scheduled task management  (originally L7214-7457, 244 lines)
# ---------------------------------------------------------------------------
def handle_cron_command(self, cmd: str):
    """Handle the /cron command to manage scheduled tasks."""
    import shlex
    from tools.cronjob_tools import cronjob as cronjob_tool

    def _cron_api(**kwargs):
        return json.loads(cronjob_tool(**kwargs))

    def _normalize_skills(values):
        normalized = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    def _parse_flags(tokens):
        opts = {
            "name": None,
            "deliver": None,
            "repeat": None,
            "skills": [],
            "add_skills": [],
            "remove_skills": [],
            "clear_skills": False,
            "all": False,
            "prompt": None,
            "schedule": None,
            "positionals": [],
        }
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--name" and i + 1 < len(tokens):
                opts["name"] = tokens[i + 1]
                i += 2
            elif token == "--deliver" and i + 1 < len(tokens):
                opts["deliver"] = tokens[i + 1]
                i += 2
            elif token == "--repeat" and i + 1 < len(tokens):
                try:
                    opts["repeat"] = int(tokens[i + 1])
                except ValueError:
                    logger.info("(._.) --repeat must be an integer")
                    return None
                i += 2
            elif token == "--skill" and i + 1 < len(tokens):
                opts["skills"].append(tokens[i + 1])
                i += 2
            elif token == "--add-skill" and i + 1 < len(tokens):
                opts["add_skills"].append(tokens[i + 1])
                i += 2
            elif token == "--remove-skill" and i + 1 < len(tokens):
                opts["remove_skills"].append(tokens[i + 1])
                i += 2
            elif token == "--clear-skills":
                opts["clear_skills"] = True
                i += 1
            elif token == "--all":
                opts["all"] = True
                i += 1
            elif token == "--prompt" and i + 1 < len(tokens):
                opts["prompt"] = tokens[i + 1]
                i += 2
            elif token == "--schedule" and i + 1 < len(tokens):
                opts["schedule"] = tokens[i + 1]
                i += 2
            else:
                opts["positionals"].append(token)
                i += 1
        return opts

    tokens = shlex.split(cmd)

    if len(tokens) == 1:
        logger.info()
        logger.info("+" + "-" * 68 + "+")
        logger.info("|" + " " * 22 + "(^_^) Scheduled Tasks" + " " * 23 + "|")
        logger.info("+" + "-" * 68 + "+")
        logger.info()
        logger.info("  Commands:")
        logger.info("    /cron list")
        logger.info('    /cron add "every 2h" "Check server status" [--skill blogwatcher]')
        logger.info('    /cron edit <job_id> --schedule "every 4h" --prompt "New task"')
        logger.info("    /cron edit <job_id> --skill blogwatcher --skill maps")
        logger.info("    /cron edit <job_id> --remove-skill blogwatcher")
        logger.info("    /cron edit <job_id> --clear-skills")
        logger.info("    /cron pause <job_id>")
        logger.info("    /cron resume <job_id>")
        logger.info("    /cron run <job_id>")
        logger.info("    /cron remove <job_id>")
        logger.info()
        result = _cron_api(action="list")
        jobs = result.get("jobs", []) if result.get("success") else []
        if jobs:
            logger.info("  Current Jobs:")
            logger.info("  " + "-" * 63)
            for job in jobs:
                repeat_str = job.get("repeat", "?")
                logger.info(f"    {job['job_id'][:12]:<12} | {job['schedule']:<15} | {repeat_str:<8}")
                if job.get("skills"):
                    logger.info(f"      Skills: {', '.join(job['skills'])}")
                logger.info(f"      {job.get('prompt_preview', '')}")
                if job.get("next_run_at"):
                    logger.info(f"      Next: {job['next_run_at']}")
                logger.info()
        else:
            logger.info("  No scheduled jobs. Use '/cron add' to create one.")
        logger.info()
        return

    subcommand = tokens[1].lower()
    opts = _parse_flags(tokens[2:])
    if opts is None:
        return

    if subcommand == "list":
        result = _cron_api(action="list", include_disabled=opts["all"])
        jobs = result.get("jobs", []) if result.get("success") else []
        if not jobs:
            logger.info("(._.) No scheduled jobs.")
            return

        logger.info()
        logger.info("Scheduled Jobs:")
        logger.info("-" * 80)
        for job in jobs:
            logger.info(f"  ID: {job['job_id']}")
            logger.info(f"  Name: {job['name']}")
            logger.info(f"  State: {job.get('state', '?')}")
            logger.info(f"  Schedule: {job['schedule']} ({job.get('repeat', '?')})")
            logger.info(f"  Next run: {job.get('next_run_at', 'N/A')}")
            if job.get("skills"):
                logger.info(f"  Skills: {', '.join(job['skills'])}")
            logger.info(f"  Prompt: {job.get('prompt_preview', '')}")
            if job.get("last_run_at"):
                logger.info(f"  Last run: {job['last_run_at']} ({job.get('last_status', '?')})")
            logger.info()
        return

    if subcommand in {"add", "create"}:
        positionals = opts["positionals"]
        if not positionals:
            logger.info("(._.) Usage: /cron add <schedule> <prompt>")
            return
        schedule = opts["schedule"] or positionals[0]
        prompt = opts["prompt"] or " ".join(positionals[1:])
        skills = _normalize_skills(opts["skills"])
        if not prompt and not skills:
            logger.info("(._.) Please provide a prompt or at least one skill")
            return
        result = _cron_api(
            action="create",
            schedule=schedule,
            prompt=prompt or None,
            name=opts["name"],
            deliver=opts["deliver"],
            repeat=opts["repeat"],
            skills=skills or None,
        )
        if result.get("success"):
            logger.info(f"(^_^)b Created job: {result['job_id']}")
            logger.info(f"  Schedule: {result['schedule']}")
            if result.get("skills"):
                logger.info(f"  Skills: {', '.join(result['skills'])}")
            logger.info(f"  Next run: {result['next_run_at']}")
        else:
            logger.info(f"(x_x) Failed to create job: {result.get('error')}")
        return

    if subcommand == "edit":
        positionals = opts["positionals"]
        if not positionals:
            logger.info("(._.) Usage: /cron edit <job_id> [--schedule ...] [--prompt ...] [--skill ...]")
            return
        job_id = positionals[0]
        from cron import get_job
        existing = get_job(job_id)
        if not existing:
            logger.info(f"(._.) Job not found: {job_id}")
            return

        final_skills = None
        replacement_skills = _normalize_skills(opts["skills"])
        add_skills = _normalize_skills(opts["add_skills"])
        remove_skills = set(_normalize_skills(opts["remove_skills"]))
        existing_skills = list(existing.get("skills") or ([] if not existing.get("skill") else [existing.get("skill")]))
        if opts["clear_skills"]:
            final_skills = []
        elif replacement_skills:
            final_skills = replacement_skills
        elif add_skills or remove_skills:
            final_skills = [skill for skill in existing_skills if skill not in remove_skills]
            for skill in add_skills:
                if skill not in final_skills:
                    final_skills.append(skill)

        result = _cron_api(
            action="update",
            job_id=job_id,
            schedule=opts["schedule"],
            prompt=opts["prompt"],
            name=opts["name"],
            deliver=opts["deliver"],
            repeat=opts["repeat"],
            skills=final_skills,
        )
        if result.get("success"):
            job = result["job"]
            logger.info(f"(^_^)b Updated job: {job['job_id']}")
            logger.info(f"  Schedule: {job['schedule']}")
            if job.get("skills"):
                logger.info(f"  Skills: {', '.join(job['skills'])}")
            else:
                logger.info("  Skills: none")
        else:
            logger.info(f"(x_x) Failed to update job: {result.get('error')}")
        return

    if subcommand in {"pause", "resume", "run", "remove", "rm", "delete"}:
        positionals = opts["positionals"]
        if not positionals:
            logger.info(f"(._.) Usage: /cron {subcommand} <job_id>")
            return
        job_id = positionals[0]
        action = "remove" if subcommand in {"remove", "rm", "delete"} else subcommand
        result = _cron_api(action=action, job_id=job_id, reason="paused from /cron" if action == "pause" else None)
        if not result.get("success"):
            logger.info(f"(x_x) Failed to {action} job: {result.get('error')}")
            return
        if action == "pause":
            logger.info(f"(^_^)b Paused job: {result['job']['name']} ({job_id})")
        elif action == "resume":
            logger.info(f"(^_^)b Resumed job: {result['job']['name']} ({job_id})")
            logger.info(f"  Next run: {result['job'].get('next_run_at')}")
        elif action == "run":
            logger.info(f"(^_^)b Triggered job: {result['job']['name']} ({job_id})")
            logger.info("  It will run on the next scheduler tick.")
        else:
            removed = result.get("removed_job", {})
            logger.info(f"(^_^)b Removed job: {removed.get('name', job_id)} ({job_id})")
        return

    logger.info(f"(._.) Unknown cron command: {subcommand}")
    logger.info("  Available: list, add, edit, pause, resume, run, remove")


# ---------------------------------------------------------------------------
# /browser — manage live Chrome CDP connection  (originally L8257-8470, 217 lines)
# ---------------------------------------------------------------------------
def handle_browser_command(self, cmd: str):
    """Handle /browser connect|disconnect|status — manage live Chrome CDP connection."""
    import platform as _plat

    parts = cmd.strip().split(None, 1)
    sub = parts[1].lower().strip() if len(parts) > 1 else "status"

    _DEFAULT_CDP = DEFAULT_BROWSER_CDP_URL
    current = os.environ.get("BROWSER_CDP_URL", "").strip()

    if sub.startswith("connect"):
        # Optionally accept a custom CDP URL: /browser connect ws://host:port
        connect_parts = cmd.strip().split(None, 2)  # ["/browser", "connect", "ws://..."]
        cdp_url = connect_parts[2].strip() if len(connect_parts) > 2 else _DEFAULT_CDP
        parsed_cdp = urlparse(cdp_url if "://" in cdp_url else f"http://{cdp_url}")
        if parsed_cdp.scheme not in {"http", "https", "ws", "wss"}:
            logger.info()
            logger.info(
                f"   ⚠ Unsupported browser url scheme: {parsed_cdp.scheme or '(missing)'} "
                "(expected one of: http, https, ws, wss)"
            )
            logger.info()
            return
        try:
            _port = parsed_cdp.port or (443 if parsed_cdp.scheme in {"https", "wss"} else 80)
        except ValueError:
            logger.info()
            logger.info(f"   ⚠ Invalid port in browser url: {cdp_url}")
            logger.info()
            return
        if not parsed_cdp.hostname:
            logger.info()
            logger.info(f"   ⚠ Missing host in browser url: {cdp_url}")
            logger.info()
            return
        _host = parsed_cdp.hostname
        if parsed_cdp.path.startswith("/devtools/browser/"):
            cdp_url = parsed_cdp.geturl()
        else:
            cdp_url = parsed_cdp._replace(
                path="",
                params="",
                query="",
                fragment="",
            ).geturl()

        # Clear any existing browser sessions so the next tool call uses the new backend
        try:
            from tools.browser_tool import cleanup_all_browsers
            cleanup_all_browsers()
        except Exception:
            pass

        logger.info()

        # Check if Chrome is already listening on the debug port
        import socket
        _already_open = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect((_host, _port))
            s.close()
            _already_open = True
        except (OSError, socket.timeout):
            pass

        if _already_open:
            logger.info(f"   ✓ Chrome is already listening on port {_port}")
        elif cdp_url == _DEFAULT_CDP:
            # Try to auto-launch Chrome with remote debugging
            logger.info("   Chrome isn't running with remote debugging — attempting to launch...")
            _launched = self._try_launch_chrome_debug(_port, _plat.system())
            if _launched:
                # Wait for the port to come up
                for _wait in range(10):
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(1)
                        s.connect((_host, _port))
                        s.close()
                        _already_open = True
                        break
                    except (OSError, socket.timeout):
                        time.sleep(0.5)
                if _already_open:
                    logger.info(f"   ✓ Chrome launched and listening on port {_port}")
                else:
                    logger.info(f"   ⚠ Chrome launched but port {_port} isn't responding yet")
                    logger.info("     Try again in a few seconds — the debug instance may still be starting")
            else:
                logger.info("   ⚠ Could not auto-launch Chrome")
                sys_name = _plat.system()
                chrome_cmd = manual_chrome_debug_command(_port, sys_name)
                if chrome_cmd:
                    logger.info(f"     Launch Chrome manually:")
                    logger.info(f"     {chrome_cmd}")
                else:
                    logger.info("     No Chrome/Chromium executable found in this environment")
        else:
            logger.info(f"   ⚠ Port {_port} is not reachable at {cdp_url}")

        if not _already_open:
            logger.info()
            logger.info("Browser not connected — start Chrome with remote debugging and retry /browser connect")
            logger.info()
            return

        os.environ["BROWSER_CDP_URL"] = cdp_url
        # Eagerly start the CDP supervisor so pending_dialogs + frame_tree
        # show up in the next browser_snapshot.  No-op if already started.
        try:
            from tools.browser_tool import _ensure_cdp_supervisor  # type: ignore[import-not-found]
            _ensure_cdp_supervisor("default")
        except Exception:
            pass
        logger.info()
        logger.info("🌐 Browser connected to live Chrome via CDP")
        logger.info(f"   Endpoint: {cdp_url}")
        logger.info()

        # Inject context message so the model knows
        if hasattr(self, '_pending_input'):
            self._pending_input.put(
                "[System note: The user has connected your browser tools to their live Chrome browser "
                "via Chrome DevTools Protocol. Your browser_navigate, browser_snapshot, browser_click, "
                "and other browser tools now control their real browser — including any pages they have "
                "open, logged-in sessions, and cookies. They likely opened specific sites or logged into "
                "services before connecting. Please await their instruction before attempting to operate "
                "the browser. When you do act, be mindful that your actions affect their real browser — "
                "don't close tabs or navigate away from pages without asking.]"
            )

    elif sub == "disconnect":
        if current:
            os.environ.pop("BROWSER_CDP_URL", None)
            try:
                from tools.browser_tool import cleanup_all_browsers, _stop_cdp_supervisor
                _stop_cdp_supervisor("default")
                cleanup_all_browsers()
            except Exception:
                pass
            logger.info()
            logger.info("🌐 Browser disconnected from live Chrome")
            logger.info("   Browser tools reverted to default mode (local headless or cloud provider)")
            logger.info()

            if hasattr(self, '_pending_input'):
                self._pending_input.put(
                    "[System note: The user has disconnected the browser tools from their live Chrome. "
                    "Browser tools are back to default mode (headless local browser or cloud provider).]"
                )
        else:
            logger.info()
            logger.info("Browser is not connected to live Chrome (already using default mode)")
            logger.info()

    elif sub == "status":
        logger.info()
        if current:
            logger.info("🌐 Browser: connected to live Chrome via CDP")
            logger.info(f"   Endpoint: {current}")

            _port = 9222
            try:
                _port = int(current.rsplit(":", 1)[-1].split("/")[0])
            except (ValueError, IndexError):
                pass
            try:
                import socket
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                s.connect(("127.0.0.1", _port))
                s.close()
                logger.info("   Status: ✓ reachable")
            except (OSError, Exception):
                logger.info("   Status: ⚠ not reachable (Chrome may not be running)")
        else:
            try:
                from tools.browser_tool import _get_cloud_provider
                provider = _get_cloud_provider()
            except Exception:
                provider = None

            if provider is not None:
                logger.info(f"🌐 Browser: {provider.provider_name()} (cloud)")
            else:
                # Show engine info for local mode
                try:
                    from tools.browser_tool import _get_browser_engine
                    engine = _get_browser_engine()
                except Exception:
                    engine = "auto"
                if engine == "lightpanda":
                    logger.info("🌐 Browser: local Lightpanda (agent-browser --engine lightpanda)")
                    logger.info("   ⚡ Lightpanda: faster navigation, no screenshot support")
                    logger.info("   Automatic Chrome fallback for screenshots and failed commands")
                elif engine == "chrome":
                    logger.info("🌐 Browser: local headless Chrome (agent-browser --engine chrome)")
                else:
                    logger.info("🌐 Browser: local headless Chromium (agent-browser)")
        logger.info()
        logger.info("   /browser connect      — connect to your live Chrome")
        logger.info("   /browser disconnect   — revert to default")
        logger.info()

    else:
        logger.info()
        logger.info("Usage: /browser connect|disconnect|status")
        logger.info()
        logger.info("   connect      Connect browser tools to your live Chrome session")
        logger.info("   disconnect   Revert to default browser backend")
        logger.info("   status       Show current browser mode")
        logger.info()


# ---------------------------------------------------------------------------
# /handoff — transfer CLI session to a gateway platform  (originally L5869-6017, 149 lines)
# ---------------------------------------------------------------------------
def handle_handoff_command(self, cmd_original: str) -> bool:
    """Handle ``/handoff <platform>`` — transfer this CLI session to a gateway platform.

    Flow:
      1. Validate platform name + the gateway has a home channel for it.
      2. Reject if the agent is currently running (the in-flight turn
         would race with the gateway's switch_session).
      3. Write ``handoff_state='pending'`` on this session row.
      4. Block-poll ``state.db`` for terminal state (timeout 60s).
      5. On ``completed`` → print resume hint and signal CLI exit by
         returning False (the caller honors that like ``/quit``).
      6. On ``failed`` / timeout → print error and return True so the
         user keeps their CLI session.

    Returns:
        False to signal CLI exit, True to keep going.
    """
    from cli import _cprint
    from hermes_state import format_session_db_unavailable

    parts = cmd_original.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        _cprint("  Usage: /handoff <platform>")
        _cprint("  Hands the current session off to that platform's home channel.")
        _cprint("  The CLI session ends here; resume it later with /resume.")
        return True

    platform_name = parts[1].strip().lower()

    # Validate platform name + home channel via the live gateway config.
    try:
        from gateway.config import load_gateway_config, Platform
    except Exception as exc:  # pragma: no cover — gateway pkg always shipped
        _cprint(f"  Could not load gateway config: {exc}")
        return True

    try:
        platform = Platform(platform_name)
    except (ValueError, KeyError):
        _cprint(f"  Unknown platform '{platform_name}'.")
        return True

    try:
        gw_config = load_gateway_config()
    except Exception as exc:
        _cprint(f"  Could not load gateway config: {exc}")
        return True

    pcfg = gw_config.platforms.get(platform)
    if not pcfg or not pcfg.enabled:
        _cprint(f"  Platform '{platform_name}' is not configured/enabled in the gateway.")
        return True

    home = gw_config.get_home_channel(platform)
    if not home or not home.chat_id:
        _cprint(f"  No home channel configured for {platform_name}.")
        _cprint(f"  Set one with /sethome on the destination chat first.")
        return True

    # Refuse mid-turn: an in-flight agent run would race with the
    # gateway's switch_session and the synthetic turn dispatch.
    if getattr(self, "_agent_running", False):
        _cprint("  Agent is busy. Wait for the current turn to finish, then retry /handoff.")
        return True

    # Make sure we have a SessionDB handle.
    if not self._session_db:
        try:
            from hermes_state import SessionDB
            self._session_db = SessionDB()
        except Exception:
            pass
    if not self._session_db:
        _cprint(f"  {format_session_db_unavailable()}")
        return True

    # Make sure the session row exists in state.db. Most CLI sessions
    # are written via _flush_messages_to_session_db on the first turn
    # already, but if the user tries to hand off an empty session we
    # still want a row to mark.
    try:
        row = self._session_db.get_session(self.session_id)
        if not row:
            # Nothing has flushed yet. Create a stub so the gateway has
            # something to switch_session onto. Inserting via title-set
            # is the simplest path because set_session_title's INSERT OR
            # IGNORE creates the row.
            placeholder_title = f"handoff-{self.session_id[:8]}"
            self._session_db.set_session_title(self.session_id, placeholder_title)
    except Exception as exc:
        _cprint(f"  Could not ensure session row in state.db: {exc}")
        return True

    # Display title for messaging.
    session_title = ""
    try:
        row = self._session_db.get_session(self.session_id)
        if row:
            session_title = row.get("title") or ""
    except Exception:
        pass
    if not session_title:
        session_title = self.session_id[:8]

    # Mark pending — gateway watcher will pick this up.
    ok = self._session_db.request_handoff(self.session_id, platform_name)
    if not ok:
        _cprint("  Session is already in flight for handoff. Wait for it to settle, then retry.")
        return True

    _cprint(f"  Queued handoff of '{session_title}' → {platform_name} (home: {home.name}).")
    _cprint(f"  Waiting for the gateway to pick it up...")

    # Poll-block on terminal state. Tick every 0.5s; bail at ~60s.
    import time as _time
    deadline = _time.time() + 60.0
    last_state = "pending"
    while _time.time() < deadline:
        try:
            state_row = self._session_db.get_handoff_state(self.session_id)
        except Exception:
            state_row = None
        current = (state_row or {}).get("state") or "pending"
        if current != last_state:
            if current == "running":
                _cprint("  Gateway picked it up; transferring...")
            last_state = current
        if current == "completed":
            _cprint("")
            _cprint(f"  ↻ Handoff complete. The session is now active on {platform_name}.")
            _cprint(f"  Resume it on this CLI later with: /resume {session_title}")
            _cprint("")
            # End the CLI cleanly — same exit semantics as /quit.
            self._should_exit = True
            return False
        if current == "failed":
            err = (state_row or {}).get("error") or "unknown error"
            _cprint(f"  Handoff failed: {err}")
            _cprint("  Your CLI session is intact. Try /handoff again, or /resume on the platform manually.")
            return True
        _time.sleep(0.5)

    # Timed out. Clear the pending flag so the user can retry.
    try:
        self._session_db.fail_handoff(self.session_id, "timed out waiting for gateway")
    except Exception:
        pass
    _cprint("  Timed out waiting for the gateway. Is `hermes gateway` running?")
    _cprint("  Your CLI session is intact.")
    return True
