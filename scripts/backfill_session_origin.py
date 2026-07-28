#!/usr/bin/env python3
"""One-off backfill: write channel-session origin_json + chat metadata into
state.db for sessions created before the §3.5 schema extension.

Design ref: docs/design-send-from-desktop-bridge.md §3.5 —存量回填.

For each channel session (source not in {web, cli, api, local, gui}) that lacks
origin_json but has a chat_id, reconstruct a SessionSource.to_dict()-shaped
object from the row's own columns and write it via an idempotent COALESCE
UPDATE (never overwrites a value already present). The origin_json format
matches ``gateway/session.py SessionSource.to_dict`` so the desktop-relay
fallback (``_find_source_by_session_id``) can rebuild the full source from
state.db alone (path A), independent of the gateway process's in-memory
session_store.

Self-contained: uses only the stdlib (sqlite3) so it can run anywhere without
pulling the heavier framework import chain (hermes_state -> harness).

Safe to re-run: COALESCE means only NULL columns are filled.

Usage:
    uv run python scripts/backfill_session_origin.py --dry-run
    uv run python scripts/backfill_session_origin.py
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Sessions that are not channel conversations — never need an origin_json
# (web/cli/api/local store their context elsewhere; web is never relayed).
SKIP_SOURCES = {"web", "cli", "api", "local", "gui"}

STATE_DB_PATH = Path.home() / ".hermes" / "state.db"

_BACKFILL_SQL = """
UPDATE sessions SET
    session_key  = COALESCE(session_key, ?),
    chat_id      = COALESCE(chat_id, ?),
    chat_type    = COALESCE(chat_type, ?),
    thread_id    = COALESCE(thread_id, ?),
    display_name = COALESCE(display_name, ?),
    origin_json  = COALESCE(origin_json, ?)
WHERE id = ?
"""


def build_origin_dict(row: dict) -> dict:
    """Reconstruct a SessionSource.to_dict()-shaped object from db columns.

    Mirrors gateway/session.py SessionSource.to_dict: only the core routing
    fields are emitted (platform, chat_id, chat_name, chat_type, thread_id).
    Optional alt fields (user_id_alt, guild_id, ...) are omitted because the
    state.db column set does not carry them; the fallback reader only requires
    platform + chat_id to rebuild a usable source.
    """
    d = {
        "platform": (row.get("source") or "unknown"),
        "chat_id": row.get("chat_id") or row.get("id"),
        "chat_name": row.get("display_name"),
        "chat_type": row.get("chat_type") or "dm",
        "thread_id": row.get("thread_id"),
    }
    return {k: v for k, v in d.items() if v is not None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be backfilled, write nothing")
    args = ap.parse_args()

    if not STATE_DB_PATH.exists():
        sys.stderr.write(f"state.db not found at {STATE_DB_PATH}\n")
        return 1

    # Read with a reader connection (WAL allows concurrent readers), then close.
    ro = sqlite3.connect(str(STATE_DB_PATH), timeout=30.0)
    ro.row_factory = sqlite3.Row
    rows = ro.execute(
        "SELECT id, source, chat_id, chat_type, thread_id, display_name, "
        "session_key, origin_json FROM sessions"
    ).fetchall()
    ro.close()

    total = skipped = backfilled = 0
    # Writer connection (created even for dry-run so the path is exercised,
    # but nothing is committed unless we actually UPDATE).
    rw = sqlite3.connect(str(STATE_DB_PATH), timeout=30.0)
    rw.isolation_level = None  # autocommit; we COMMIT explicitly
    try:
        for r in rows:
            total += 1
            source = (r["source"] or "").lower()
            if source in SKIP_SOURCES or r["origin_json"] or not r["chat_id"]:
                skipped += 1
                continue
            origin = build_origin_dict(dict(r))
            origin_json = json.dumps(origin, ensure_ascii=False)
            if args.dry_run:
                print(f"[dry-run] {r['id']} ({source}) -> {origin_json}")
            else:
                rw.execute(
                    _BACKFILL_SQL,
                    (r["session_key"], r["chat_id"], r["chat_type"],
                     r["thread_id"], r["display_name"], origin_json, r["id"]),
                )
                print(f"[ok] backfilled {r['id']} ({source})")
            backfilled += 1
        if not args.dry_run:
            rw.execute("COMMIT")
    finally:
        rw.close()

    print(f"\nsummary: scanned={total} backfilled={backfilled} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
