"""A3 / W4 — persistent per-platform notice dedupe (gateway/notices.py).

Covers the two properties the sprint board asked for:
  * the same notice fires once per platform (not once per session), and
  * it stays fired across a gateway restart (i.e. it is really persisted).
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from gateway.notices import (
    HOME_CHANNEL_MISSING,
    clear_notices,
    has_noticed,
    mark_noticed,
    notices_path,
    _reset_cache_for_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _NoticesTestCase(unittest.TestCase):
    """Point notices at a throwaway file and drop the in-process cache."""

    def setUp(self):
        self._tmp = self._new_tmp_dir()
        self._old_env = os.environ.get("VERMES_NOTICES_PATH")
        self.notices_file = self._tmp / "notices.json"
        os.environ["VERMES_NOTICES_PATH"] = str(self.notices_file)
        _reset_cache_for_tests()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("VERMES_NOTICES_PATH", None)
        else:
            os.environ["VERMES_NOTICES_PATH"] = self._old_env
        _reset_cache_for_tests()

    def _new_tmp_dir(self):
        import tempfile

        return Path(tempfile.mkdtemp(prefix="notices-test-"))


class TestNoticeStore(_NoticesTestCase):
    def test_path_follows_env_override(self):
        self.assertEqual(Path(notices_path()), self.notices_file)

    def test_unmarked_is_false(self):
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_mark_then_has(self):
        self.assertTrue(mark_noticed("telegram", HOME_CHANNEL_MISSING))
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_mark_persists_to_disk_not_just_memory(self):
        """Guard against a cache-only implementation (mutation guard)."""
        mark_noticed("telegram", HOME_CHANNEL_MISSING)
        self.assertTrue(self.notices_file.exists())
        payload = json.loads(self.notices_file.read_text(encoding="utf-8"))
        self.assertIn(HOME_CHANNEL_MISSING, payload["notices"]["telegram"])

    def test_platforms_are_isolated(self):
        mark_noticed("telegram", HOME_CHANNEL_MISSING)
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_MISSING))
        self.assertFalse(has_noticed("feishu", HOME_CHANNEL_MISSING))

    def test_keys_are_isolated(self):
        mark_noticed("telegram", HOME_CHANNEL_MISSING)
        self.assertFalse(has_noticed("telegram", "some_other_notice"))

    def test_platform_matching_is_case_insensitive(self):
        mark_noticed("Telegram", HOME_CHANNEL_MISSING)
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_corrupt_file_is_treated_as_unnoticed(self):
        self.notices_file.write_text("{not json at all", encoding="utf-8")
        _reset_cache_for_tests()
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_MISSING))
        # ...and a fresh mark still works (self-heals).
        self.assertTrue(mark_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_missing_parent_dir_is_created(self):
        nested = self._tmp / "a" / "b" / "notices.json"
        os.environ["VERMES_NOTICES_PATH"] = str(nested)
        _reset_cache_for_tests()
        self.assertTrue(mark_noticed("telegram", HOME_CHANNEL_MISSING))
        self.assertTrue(nested.exists())

    def test_clear_notices_resets(self):
        mark_noticed("telegram", HOME_CHANNEL_MISSING)
        clear_notices("telegram", HOME_CHANNEL_MISSING)
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_empty_platform_or_key_is_noop(self):
        self.assertFalse(mark_noticed("", HOME_CHANNEL_MISSING))
        self.assertFalse(has_noticed("telegram", ""))


class TestNoticeSurvivesRestart(_NoticesTestCase):
    """The real 'restart' proof: a *fresh interpreter* must still know."""

    def test_dedupe_survives_new_process(self):
        mark_noticed("telegram", HOME_CHANNEL_MISSING)

        code = (
            "import os;"
            "from gateway.notices import has_noticed, HOME_CHANNEL_MISSING;"
            "print('YES' if has_noticed('telegram', HOME_CHANNEL_MISSING) else 'NO')"
        )
        env = dict(os.environ)
        env["VERMES_NOTICES_PATH"] = str(self.notices_file)
        env["PYTHONPATH"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("YES", proc.stdout)


class TestHomeChannelPromptOnce(_NoticesTestCase):
    """Integration: the mixin must nag once, not once per session/turn."""

    def _make_handler(self):
        from gateway.config import Platform
        from gateway.message_handler_mixin import MessageHandlerMixin
        from gateway.session import SessionSource

        class _Handler(MessageHandlerMixin):
            def __init__(self):
                self.config = None
                self.adapters = {}
                self.sent = []

            async def _deliver_platform_notice(self, source, content):
                self.sent.append(content)

        handler = _Handler()
        source = SessionSource(
            platform=Platform.TELEGRAM, chat_id="12345", user_id="u1"
        )
        return handler, source

    def _patch_no_home_channel(self):
        import gateway.message_handler_mixin as mhm
        from unittest.mock import patch

        return patch.object(
            mhm, "resolve_home_channel_chat_id", lambda *a, **k: ""
        )

    def test_prompts_once_and_only_once(self):
        import asyncio

        handler, source = self._make_handler()
        with self._patch_no_home_channel():
            first = asyncio.run(handler._maybe_prompt_missing_home_channel(source))
            second = asyncio.run(handler._maybe_prompt_missing_home_channel(source))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(handler.sent), 1, handler.sent)

    def test_no_prompt_when_home_channel_is_set(self):
        import asyncio

        import gateway.message_handler_mixin as mhm
        from unittest.mock import patch

        handler, source = self._make_handler()
        with patch.object(
            mhm, "resolve_home_channel_chat_id", lambda *a, **k: "-100123"
        ):
            delivered = asyncio.run(
                handler._maybe_prompt_missing_home_channel(source)
            )
        self.assertFalse(delivered)
        self.assertEqual(handler.sent, [])
        # Must NOT burn the one-time notice when the user already has a home.
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_MISSING))

    def test_failed_delivery_is_not_marked_as_delivered(self):
        import asyncio

        handler, source = self._make_handler()

        async def _boom(src, content):
            raise RuntimeError("adapter down")

        handler._deliver_platform_notice = _boom
        with self._patch_no_home_channel():
            delivered = asyncio.run(
                handler._maybe_prompt_missing_home_channel(source)
            )

        self.assertFalse(delivered)
        self.assertFalse(
            has_noticed("telegram", HOME_CHANNEL_MISSING),
            "a failed delivery must not consume the one-time notice",
        )


if __name__ == "__main__":
    unittest.main()
