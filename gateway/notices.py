"""Per-platform one-time notices (roadmap A3 / sprint board W4).

Why a separate file instead of ``~/.vermes/gateway_state.json``
---------------------------------------------------------------
``gateway_state.json`` is the **runtime health record** written by
``gateway/status.py`` (pid / kind / gateway_state / per-platform connection
state) and consumed by doctor-style diagnostics. Mixing "we already nagged
this user" UX flags into it pollutes that semantics — see errata **E7** in
``reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md``. The A3 roadmap
entry pointed at ``gateway_state.json``; E7 superseded it, and the sprint
board (W4) fixed the落点 as an independent domain.

So notices live in their own file: ``~/.vermes/notices.json``.

Semantics
---------
* Deduplication is **per (platform, key)** and **persistent** — it survives
  gateway restarts. A3 explicitly replaces the old ``not history`` throttle
  (session-scoped, so the same notice re-fired on every new session).
* Reads are cheap: the payload is cached in-process and reloaded only when the
  file's mtime/size changes. Without this, a per-turn ``has_noticed()`` call
  would mean one disk read per inbound message.
* Writes are atomic (tmp file + ``os.replace``) and best-effort: a failed
  write must never break message handling, it just means the notice may fire
  again later.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

NOTICES_FILENAME = "notices.json"

# Notice keys — add new ones here so callers cannot typo them.
HOME_CHANNEL_MISSING = "home_channel_missing"

# A7: we auto-adopted this platform's first DM as its home channel. Deliberately
# a *separate* key from HOME_CHANNEL_MISSING — they are different user-facing
# events with different text, and one succeeding is not the other being
# delivered. Keeping them apart also means disabling auto-set later does not
# retroactively suppress the manual prompt.
HOME_CHANNEL_AUTOSET = "home_channel_autoset"


def _default_notices_path() -> Path:
    """Return ``~/.vermes/notices.json`` (VERMES_HOME aware)."""
    override = os.getenv("VERMES_NOTICES_PATH")
    if override:
        return Path(override)
    try:
        from vermes_cli.config import get_vermes_home

        return get_vermes_home() / NOTICES_FILENAME
    except Exception:
        home = os.getenv("VERMES_HOME") or os.path.expanduser("~/.vermes")
        return Path(home) / NOTICES_FILENAME


def notices_path() -> Path:
    """Public accessor (tests may override via ``VERMES_NOTICES_PATH``)."""
    return _default_notices_path()


# ── in-process cache ──────────────────────────────────────────────────
# Single gateway process is the normal writer; the mtime guard keeps us honest
# if something else (CLI / GUI) writes the file underneath us.
_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_stamp: Optional[tuple] = None


def _stamp(path: Path) -> tuple:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return ()


def _reset_cache_for_tests() -> None:
    """Drop the in-process cache (tests / after manual edits only)."""
    global _cache, _cache_stamp
    with _lock:
        _cache = None
        _cache_stamp = None


def _load() -> Dict[str, Any]:
    """Load the notices payload, tolerating a missing or corrupt file."""
    global _cache, _cache_stamp
    path = notices_path()
    stamp = _stamp(path)
    with _lock:
        if _cache is not None and stamp == _cache_stamp:
            return _cache
        payload: Dict[str, Any] = {}
        if stamp:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    payload = raw
            except Exception:
                # Corrupt/truncated file: treat as "nothing noticed yet"
                # rather than crashing the message pipeline.
                logger.debug("[notices] unreadable %s, starting fresh", path)
                payload = {}
        _cache = payload
        _cache_stamp = stamp
        return payload


def _write(payload: Dict[str, Any]) -> bool:
    """Atomically persist *payload*. Returns False on any failure."""
    global _cache, _cache_stamp
    path = notices_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception as e:
        logger.debug("[notices] failed to persist %s: %s", path, e)
        return False
    # Refresh the cache so the next read does not reload from disk.
    with _lock:
        _cache = payload
        _cache_stamp = _stamp(path)
    return True


def _normalize(value: str) -> str:
    return (value or "").strip().lower()


def has_noticed(platform: str, key: str = HOME_CHANNEL_MISSING) -> bool:
    """True if *key* was already delivered for *platform* (persisted)."""
    platform = _normalize(platform)
    key = _normalize(key)
    if not platform or not key:
        return False
    notices = _load().get("notices") or {}
    if not isinstance(notices, dict):
        return False
    platform_notices = notices.get(platform)
    if not isinstance(platform_notices, dict):
        return False
    return key in platform_notices


def mark_noticed(platform: str, key: str = HOME_CHANNEL_MISSING) -> bool:
    """Record that *key* was delivered for *platform*. Returns write success."""
    platform = _normalize(platform)
    key = _normalize(key)
    if not platform or not key:
        return False

    payload = dict(_load())
    notices = payload.get("notices")
    if not isinstance(notices, dict):
        notices = {}
    platform_notices = notices.get(platform)
    if not isinstance(platform_notices, dict):
        platform_notices = {}
    if key in platform_notices:
        return True  # already recorded — no write needed

    platform_notices[key] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "count": 1,
    }
    notices[platform] = platform_notices
    payload["notices"] = notices
    payload.setdefault("version", 1)
    return _write(payload)


def clear_notices(platform: Optional[str] = None, key: Optional[str] = None) -> bool:
    """Forget notices — whole file, one platform, or one (platform, key)."""
    payload = dict(_load())
    if platform is None:
        payload["notices"] = {}
        return _write(payload)

    platform = _normalize(platform)
    notices = payload.get("notices")
    if not isinstance(notices, dict):
        return True
    if key is None:
        notices.pop(platform, None)
    else:
        platform_notices = notices.get(platform)
        if isinstance(platform_notices, dict):
            platform_notices.pop(_normalize(key), None)
    payload["notices"] = notices
    return _write(payload)
