"""
CLI commands for the DM pairing system.

Usage:
    vermes pairing list              # Show all pending + approved users
    vermes pairing approve <platform> <code>  # Approve a pairing code
    vermes pairing revoke <platform> <user_id> # Revoke user access
    vermes pairing clear-pending     # Clear all expired/pending codes
"""

import logging

logger = logging.getLogger(__name__)
def pairing_command(args):
    """Handle vermes pairing subcommands."""
    from gateway.pairing import PairingStore

    store = PairingStore()
    action = getattr(args, "pairing_action", None)

    if action == "list":
        _cmd_list(store)
    elif action == "approve":
        _cmd_approve(store, args.platform, args.code)
    elif action == "revoke":
        _cmd_revoke(store, args.platform, args.user_id)
    elif action == "clear-pending":
        _cmd_clear_pending(store)
    else:
        logger.info("Usage: vermes pairing {list|approve|revoke|clear-pending}")
        logger.info("Run 'vermes pairing --help' for details.")


def _cmd_list(store):
    """List all pending and approved users."""
    pending = store.list_pending()
    approved = store.list_approved()

    if not pending and not approved:
        logger.info("No pairing data found. No one has tried to pair yet~")
        return

    if pending:
        logger.info(f"\n  Pending Pairing Requests ({len(pending)}):")
        logger.info(f"  {'Platform':<12} {'Code':<10} {'User ID':<20} {'Name':<20} {'Age'}")
        logger.info(f"  {'--------':<12} {'----':<10} {'-------':<20} {'----':<20} {'---'}")
        for p in pending:
            logger.info(
                f"  {p['platform']:<12} {p['code']:<10} {p['user_id']:<20} "
                f"{(p.get('user_name') or ''):<20} {p['age_minutes']}m ago"
            )
    else:
        logger.info("\n  No pending pairing requests.")

    if approved:
        logger.info(f"\n  Approved Users ({len(approved)}):")
        logger.info(f"  {'Platform':<12} {'User ID':<20} {'Name':<20}")
        logger.info(f"  {'--------':<12} {'-------':<20} {'----':<20}")
        for a in approved:
            logger.info(f"  {a['platform']:<12} {a['user_id']:<20} {(a.get('user_name') or ''):<20}")
    else:
        logger.info("\n  No approved users.")

    logger.info()


def _cmd_approve(store, platform: str, code: str):
    """Approve a pairing code."""
    platform = platform.lower().strip()
    code = code.upper().strip()

    result = store.approve_code(platform, code)
    if result:
        uid = result["user_id"]
        name = result.get("user_name") or ""
        display = f"{name} ({uid})" if name else uid
        logger.info(f"\n  Approved! User {display} on {platform} can now use the bot~")
        logger.info("  They'll be recognized automatically on their next message.\n")
    elif store._is_locked_out(platform):
        # Disambiguate: approve_code returns None for both invalid codes
        # and lockout. Tell the operator it's lockout so they don't chase
        # a "wrong code" rabbit hole (#10195).
        import time as _time


        limits = store._load_json(store._rate_limit_path())
        lockout_until = limits.get(f"_lockout:{platform}", 0)
        remaining = max(0, int(lockout_until - _time.time()))
        mins = remaining // 60
        logger.info(
            f"\n  Platform '{platform}' is locked out after too many failed "
            f"approval attempts."
        )
        logger.info(f"  Lockout clears in ~{mins} minute(s).")
        logger.info(
            "  To reset sooner, delete the '_lockout:{0}' entry from "
            "~/.vermes/platforms/pairing/_rate_limits.json\n".format(platform)
        )
    else:
        logger.info(f"\n  Code '{code}' not found or expired for platform '{platform}'.")
        logger.info("  Run 'vermes pairing list' to see pending codes.\n")


def _cmd_revoke(store, platform: str, user_id: str):
    """Revoke a user's access."""
    platform = platform.lower().strip()

    if store.revoke(platform, user_id):
        logger.info(f"\n  Revoked access for user {user_id} on {platform}.\n")
    else:
        logger.info(f"\n  User {user_id} not found in approved list for {platform}.\n")


def _cmd_clear_pending(store):
    """Clear all pending pairing codes."""
    count = store.clear_pending()
    if count:
        logger.info(f"\n  Cleared {count} pending pairing request(s).\n")
    else:
        logger.info("\n  No pending requests to clear.\n")
