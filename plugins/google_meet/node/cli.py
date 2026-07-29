"""`Vermes meet node ...` subcommand tree.

Wired into the existing ``Vermes meet`` parser by the plugin's top-level
CLI. This module only defines the subparsers and their dispatch — it
does not mutate the existing cli.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from plugins.google_meet.node.client import NodeClient
from plugins.google_meet.node.registry import NodeRegistry
from plugins.google_meet.node.server import NodeServer

import logging

logger = logging.getLogger(__name__)



def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Add ``run / list / approve / remove / status / ping`` subparsers.

    *subparser* is the ``Vermes meet node`` argparse object — typically
    the result of ``meet_parser.add_parser('node', ...)``.
    """
    sp = subparser.add_subparsers(dest="node_cmd", required=True)

    run = sp.add_parser("run", help="Start a node server on this machine.")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=18789)
    run.add_argument("--display-name", default="Vermes-meet-node")
    run.set_defaults(func=node_command)

    lst = sp.add_parser("list", help="List approved remote nodes.")
    lst.set_defaults(func=node_command)

    app = sp.add_parser("approve", help="Register a remote node on the gateway.")
    app.add_argument("name")
    app.add_argument("url")
    app.add_argument("token")
    app.set_defaults(func=node_command)

    rm = sp.add_parser("remove", help="Forget a registered node.")
    rm.add_argument("name")
    rm.set_defaults(func=node_command)

    st = sp.add_parser("status", help="Ping a registered node.")
    st.add_argument("name")
    st.set_defaults(func=node_command)

    pg = sp.add_parser("ping", help="Alias for status.")
    pg.add_argument("name")
    pg.set_defaults(func=node_command)


def node_command(args: argparse.Namespace) -> int:
    """Dispatch for ``Vermes meet node ...``.

    Returns a process exit code. Side-effects print to stdout/stderr.
    """
    cmd = getattr(args, "node_cmd", None)

    if cmd == "run":
        server = NodeServer(
            host=args.host,
            port=args.port,
            display_name=args.display_name,
        )
        token = server.ensure_token()
        logger.info(f"[meet-node] display_name={server.display_name}")
        logger.info(f"[meet-node] listening on ws://{args.host}:{args.port}")
        logger.info(f"[meet-node] token (copy to gateway): {token}")
        logger.info(f"[meet-node] approve with:")
        logger.info(f"             Vermes meet node approve <name> ws://<host>:{args.port} {token}")
        try:
            asyncio.run(server.serve())
        except KeyboardInterrupt:
            return 0
        except RuntimeError as exc:
            logger.warning(f"[meet-node] error: {exc}")
            return 2
        return 0

    reg = NodeRegistry()

    if cmd == "list":
        nodes = reg.list_all()
        if not nodes:
            logger.info("no nodes registered")
            return 0
        for n in nodes:
            logger.info(f"{n['name']}\t{n['url']}\ttoken={n['token'][:6]}…")
        return 0

    if cmd == "approve":
        reg.add(args.name, args.url, args.token)
        logger.info(f"approved node {args.name!r} at {args.url}")
        return 0

    if cmd == "remove":
        ok = reg.remove(args.name)
        logger.info(f"removed {args.name!r}" if ok else f"no such node: {args.name!r}")
        return 0 if ok else 1

    if cmd in {"status", "ping"}:
        entry = reg.get(args.name)
        if entry is None:
            logger.warning(f"no such node: {args.name!r}")
            return 1
        client = NodeClient(entry["url"], entry["token"])
        try:
            result = client.ping()
        except Exception as exc:  # noqa: BLE001 — surface any connection error
            logger.info(json.dumps({"ok": False, "error": str(exc)}))
            return 1
        logger.info(json.dumps({"ok": True, "node": args.name, **_coerce_dict(result)}))
        return 0

    logger.warning(f"unknown node command: {cmd!r}")
    return 2


def _coerce_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {"result": value}
