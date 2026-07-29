"""Vermes webhook — manage dynamic webhook subscriptions from the CLI.

Usage:
    Vermes webhook subscribe <name> [options]
    Vermes webhook list
    Vermes webhook remove <name>
    Vermes webhook test <name> [--payload '{"key": "value"}']

Subscriptions persist to ~/.vermes/webhook_subscriptions.json and are
hot-reloaded by the webhook adapter without a gateway restart.
"""

import logging

logger = logging.getLogger(__name__)
import json
import re
import secrets
import time
from pathlib import Path
from typing import Dict

from vermes_constants import display_vermes_home
from utils import atomic_replace
from vermes_cli.config import cfg_get


_SUBSCRIPTIONS_FILENAME = "webhook_subscriptions.json"


def _vermes_home() -> Path:
    from vermes_constants import get_vermes_home
    return get_vermes_home()


def _subscriptions_path() -> Path:
    return _vermes_home() / _SUBSCRIPTIONS_FILENAME


def _load_subscriptions() -> Dict[str, dict]:
    path = _subscriptions_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_subscriptions(subs: Dict[str, dict]) -> None:
    path = _subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(subs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    atomic_replace(tmp_path, path)


def _get_webhook_config() -> dict:
    """Load webhook platform config. Returns {} if not configured."""
    try:
        from vermes_cli.config import load_config
        cfg = load_config()
        return cfg_get(cfg, "platforms", "webhook", default={})
    except Exception:
        return {}


def _is_webhook_enabled() -> bool:
    return bool(_get_webhook_config().get("enabled"))


def _get_webhook_base_url() -> str:
    wh = _get_webhook_config().get("extra", {})
    host = wh.get("host", "0.0.0.0")
    port = wh.get("port", 8644)
    display_host = "localhost" if host == "0.0.0.0" else host
    return f"http://{display_host}:{port}"


def _setup_hint() -> str:
    _dhh = display_vermes_home()
    return f"""
  Webhook platform is not enabled. To set it up:

  1. Run the gateway setup wizard:
     vermes gateway setup

  2. Or manually add to {_dhh}/config.yaml:
     platforms:
       webhook:
         enabled: true
         extra:
           host: "0.0.0.0"
           port: 8644
           secret: "your-global-hmac-secret"

  3. Or set environment variables in {_dhh}/.env:
     WEBHOOK_ENABLED=true
     WEBHOOK_PORT=8644
     WEBHOOK_SECRET=your-global-secret

  Then start the gateway: vermes gateway run
"""


def _require_webhook_enabled() -> bool:
    """Check webhook is enabled. Print setup guide and return False if not."""
    if _is_webhook_enabled():
        return True
    logger.info(_setup_hint())
    return False


def webhook_command(args):
    """Entry point for 'Vermes webhook' subcommand."""
    sub = getattr(args, "webhook_action", None)

    if not sub:
        logger.info("Usage: Vermes webhook {subscribe|list|remove|test}")
        logger.info("Run 'Vermes webhook --help' for details.")
        return

    if not _require_webhook_enabled():
        return

    if sub in {"subscribe", "add"}:
        _cmd_subscribe(args)
    elif sub in {"list", "ls"}:
        _cmd_list(args)
    elif sub in {"remove", "rm"}:
        _cmd_remove(args)
    elif sub == "test":
        _cmd_test(args)


def _cmd_subscribe(args):
    name = args.name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        logger.info(f"Error: Invalid name '{name}'. Use lowercase alphanumeric with hyphens/underscores.")
        return

    subs = _load_subscriptions()
    is_update = name in subs

    secret = args.secret or secrets.token_urlsafe(32)
    events = [e.strip() for e in args.events.split(",")] if args.events else []

    route = {
        "description": args.description or f"Agent-created subscription: {name}",
        "events": events,
        "secret": secret,
        "prompt": args.prompt or "",
        "skills": [s.strip() for s in args.skills.split(",")] if args.skills else [],
        "deliver": args.deliver or "log",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if getattr(args, "deliver_only", False):
        if route["deliver"] == "log":
            logger.info(
                "Error: --deliver-only requires --deliver to be a real target "
                "(telegram, discord, slack, github_comment, etc.) — not 'log'."
            )
            return
        route["deliver_only"] = True

    if args.deliver_chat_id:
        route["deliver_extra"] = {"chat_id": args.deliver_chat_id}

    subs[name] = route
    _save_subscriptions(subs)

    base_url = _get_webhook_base_url()
    status = "Updated" if is_update else "Created"

    logger.info(f"\n  {status} webhook subscription: {name}")
    logger.info(f"  URL:    {base_url}/webhooks/{name}")
    logger.info(f"  Secret: {secret}")
    if events:
        logger.info(f"  Events: {', '.join(events)}")
    else:
        logger.info("  Events: (all)")
    logger.info(f"  Deliver: {route['deliver']}")
    if route.get("deliver_only"):
        logger.info("  Mode: direct delivery (no agent, zero LLM cost)")
    if route.get("prompt"):
        prompt_preview = route["prompt"][:80] + ("..." if len(route["prompt"]) > 80 else "")
        label = "Message" if route.get("deliver_only") else "Prompt"
        logger.info(f"  {label}: {prompt_preview}")
    logger.info(f"\n  Configure your service to POST to the URL above.")
    logger.info(f"  Use the secret for HMAC-SHA256 signature validation.")
    logger.info(f"  The gateway must be running to receive events (vermes gateway run).\n")


def _cmd_list(args):
    subs = _load_subscriptions()
    if not subs:
        logger.info("  No dynamic webhook subscriptions.")
        logger.info("  Create one with: Vermes webhook subscribe <name>")
        return

    base_url = _get_webhook_base_url()
    logger.info(f"\n  {len(subs)} webhook subscription(s):\n")
    for name, route in subs.items():
        events = ", ".join(route.get("events", [])) or "(all)"
        deliver = route.get("deliver", "log")
        if route.get("deliver_only"):
            deliver = f"{deliver} (direct — no agent)"
        desc = route.get("description", "")
        logger.info(f"  ◆ {name}")
        if desc:
            logger.info(f"    {desc}")
        logger.info(f"    URL:     {base_url}/webhooks/{name}")
        logger.info(f"    Events:  {events}")
        logger.info(f"    Deliver: {deliver}")
        logger.info()


def _cmd_remove(args):
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        logger.info(f"  No subscription named '{name}'.")
        logger.info("  Note: Static routes from config.yaml cannot be removed here.")
        return

    del subs[name]
    _save_subscriptions(subs)
    logger.info(f"  Removed webhook subscription: {name}")


def _cmd_test(args):
    """Send a test POST to a webhook route."""
    name = args.name.strip().lower()
    subs = _load_subscriptions()

    if name not in subs:
        logger.info(f"  No subscription named '{name}'.")
        return

    route = subs[name]
    secret = route.get("secret", "")
    base_url = _get_webhook_base_url()
    url = f"{base_url}/webhooks/{name}"

    payload = args.payload or '{"test": true, "event_type": "test", "message": "Hello from Vermes webhook test"}'

    import hmac
    import hashlib
    sig = "sha256=" + hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    logger.info(f"  Sending test POST to {url}")
    try:
        import urllib.request


        req = urllib.request.Request(
            url,
            data=payload.encode(),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "test",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            logger.info(f"  Response ({resp.status}): {body}")
    except Exception as e:
        logger.info(f"  Error: {e}")
        logger.info("  Is the gateway running? (vermes gateway run)")
