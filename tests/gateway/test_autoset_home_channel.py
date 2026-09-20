"""A7 (roadmap) — first *authorized DM* auto-adopts the platform home channel.

Why this suite exists: auto-set writes the user's config.yaml and .env. Any
regression there silently misroutes every cron delivery the user ever gets,
so the suites below assert real persisted state, not just return values.

Gate coverage mirrors the three gates in the implementation:
  1. sender already passed ``_is_user_authorized`` upstream (assumed here —
     the suite drives the method directly);
  2. DM only  -> group / channel / bot traffic never triggers a write;
  3. never overwrite an existing home channel.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway.notices import (
    HOME_CHANNEL_AUTOSET,
    HOME_CHANNEL_MISSING,
    _reset_cache_for_tests,
    clear_notices,
    has_noticed,
)
from gateway.message_handler_mixin import MessageHandlerMixin
from gateway.config import Platform
from gateway.session import SessionSource


class _AutoSetTestCase(unittest.TestCase):
    """Shared isolation: throwaway VERMES_HOME + notices file."""

    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "VERMES_HOME",
                "VERMES_NOTICES_PATH",
                "VERMES_AUTO_SET_HOME_CHANNEL",
                "TELEGRAM_HOME_CHANNEL",
                "TELEGRAM_HOME_CHANNEL_THREAD_ID",
            )
        }
        self._tmp = Path(tempfile.mkdtemp(prefix="autoset-"))
        os.environ["VERMES_HOME"] = str(self._tmp)
        os.environ["VERMES_NOTICES_PATH"] = str(self._tmp / "notices.json")
        os.environ.pop("VERMES_AUTO_SET_HOME_CHANNEL", None)
        os.environ.pop("TELEGRAM_HOME_CHANNEL", None)
        os.environ.pop("TELEGRAM_HOME_CHANNEL_THREAD_ID", None)
        _reset_cache_for_tests()
        clear_notices()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _reset_cache_for_tests()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def _make_handler(self):
        class _Handler(MessageHandlerMixin):
            def __init__(self):
                self.config = None
                self.adapters = {}
                self.sent = []

            async def _deliver_platform_notice(self, source, content):
                self.sent.append(content)

        return _Handler()

    def _source(self, chat_type="dm", **kwargs):
        return SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_name="DongDong",
            chat_type=chat_type,
            user_id="u1",
            **kwargs,
        )

    def _run(self, handler, source):
        return asyncio.run(handler._maybe_auto_set_home_channel(source))


class TestAutoSetGates(_AutoSetTestCase):
    """Every gate must refuse *before* touching disk."""

    def _patch_write(self):
        return patch(
            "vermes_cli.gateway_channels.write_home_channel",
            return_value={"ok": True, "chat_id": "12345"},
        )

    def test_dm_first_message_adopts(self):
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertTrue(self._run(handler, self._source()))
        write.assert_called_once()
        _, kwargs_or_args = write.call_args[0], write.call_args[1]
        self.assertEqual(handler.sent[0].split()[0], "📬")
        self.assertIn("home channel", handler.sent[0])
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_AUTOSET))

    def test_group_chat_is_never_adopted(self):
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, self._source(chat_type="group")))
        write.assert_not_called()
        self.assertEqual(handler.sent, [])
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_AUTOSET))

    def test_unset_chat_type_is_not_treated_as_dm(self):
        """Failing open here would let group traffic hijack the delivery target."""
        handler = self._make_handler()
        source = self._source()
        source.chat_type = ""
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, source))
        write.assert_not_called()

    def test_bot_authored_message_is_ignored(self):
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, self._source(is_bot=True)))
        write.assert_not_called()

    def test_existing_home_channel_is_never_overwritten(self):
        os.environ["TELEGRAM_HOME_CHANNEL"] = "-100old"
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, self._source()))
        write.assert_not_called()
        self.assertEqual(os.environ["TELEGRAM_HOME_CHANNEL"], "-100old")

    def test_opt_out_env_disables_auto_set(self):
        os.environ["VERMES_AUTO_SET_HOME_CHANNEL"] = "false"
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, self._source()))
        write.assert_not_called()

    def test_garbage_opt_out_value_fails_closed(self):
        """A typo like 'flase' must disable, not silently enable."""
        os.environ["VERMES_AUTO_SET_HOME_CHANNEL"] = "flase"
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertFalse(self._run(handler, self._source()))
        write.assert_not_called()

    def test_adopts_only_once_per_platform(self):
        handler = self._make_handler()
        with self._patch_write() as write:
            self.assertTrue(self._run(handler, self._source()))
            self.assertFalse(self._run(handler, self._source()))
        self.assertEqual(write.call_count, 1)

    def test_platfroms_are_isolated(self):
        handler = self._make_handler()
        with self._patch_write():
            self.assertTrue(self._run(handler, self._source()))
        feishu = SessionSource(
            platform=Platform.FEISHU,
            chat_id="ou_1",
            chat_type="dm",
            user_id="u1",
        )
        with patch(
            "vermes_cli.gateway_channels.write_home_channel",
            return_value={"ok": True, "chat_id": "ou_1"},
        ) as write:
            self.assertTrue(self._run(handler, feishu))
        self.assertEqual(write.call_count, 1)


class TestAutoSetFailureHandling(_AutoSetTestCase):
    def test_total_failure_falls_through_to_manual_prompt(self):
        """Nothing stuck -> caller still prompts; the one-shot is NOT burned."""
        handler = self._make_handler()
        with patch(
            "vermes_cli.gateway_channels.write_home_channel",
            return_value={"ok": False, "chat_id": "", "config_error": "disk full"},
        ):
            self.assertFalse(self._run(handler, self._source()))
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_AUTOSET))
        self.assertEqual(handler.sent, [])

    def test_raised_write_is_swallowed_and_retried_later(self):
        handler = self._make_handler()
        with patch(
            "vermes_cli.gateway_channels.write_home_channel",
            side_effect=RuntimeError("boom"),
        ):
            self.assertFalse(self._run(handler, self._source()))
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_AUTOSET))

    def test_partial_application_is_accepted_but_logged(self):
        """config.yaml written, .env failed: it resolves, so report success."""
        handler = self._make_handler()
        with patch(
            "vermes_cli.gateway_channels.write_home_channel",
            return_value={"ok": False, "chat_id": "12345", "env_error": "ro fs"},
        ):
            self.assertTrue(self._run(handler, self._source()))
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_AUTOSET))

    def test_failed_receipt_does_not_unadopt(self):
        """The channel IS set — re-delivering next turn would repeat stale news."""
        handler = self._make_handler()

        async def _boom(src, content):
            raise RuntimeError("adapter down")

        handler._deliver_platform_notice = _boom
        with patch(
            "vermes_cli.gateway_channels.write_home_channel",
            return_value={"ok": True, "chat_id": "12345"},
        ):
            self.assertTrue(self._run(handler, self._source()))
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_AUTOSET))


class TestAutoSetRealPersistence(_AutoSetTestCase):
    """No mocking of the write path — really write into a throwaway home."""

    def test_writes_config_yaml_and_env(self):
        from vermes_cli.gateway_channels import read_home_channel

        handler = self._make_handler()
        # Seed a config.yaml the way a real user's looks — WITH comments.
        cfg = self._tmp / "config.yaml"
        cfg.write_text(
            "# my hand-written note\n"
            "platforms:\n"
            "  telegram:\n"
            "    enabled: true  # trailing note\n",
            encoding="utf-8",
        )

        self.assertTrue(self._run(handler, self._source()))

        # 1) structured truth source is written where the resolver looks
        raw = cfg.read_text(encoding="utf-8")
        self.assertIn("home_channel", raw)
        self.assertIn("chat_id", raw)
        self.assertEqual(
            read_home_channel("telegram")["chat_id"], "12345"
        )
        # 2) env layer agrees — this is what cron delivery reads
        self.assertEqual(os.environ.get("TELEGRAM_HOME_CHANNEL"), "12345")
        # 3) comments survive (M5/P1 regression guard)
        self.assertIn("# my hand-written note", raw)
        self.assertIn("# trailing note", raw)
        # 4) a stale thread pin from a previous group must not survive
        self.assertEqual(
            os.environ.get("TELEGRAM_HOME_CHANNEL_THREAD_ID", ""), ""
        )

    def test_in_process_config_is_mirrored(self):
        """Cron that reads self.config must see it before any .env reload."""
        from gateway.config import GatewayConfig

        handler = self._make_handler()
        handler.config = GatewayConfig()
        self.assertTrue(self._run(handler, self._source()))
        mirrored = handler.config.platforms[Platform.TELEGRAM].home_channel
        self.assertIsNotNone(mirrored)
        self.assertEqual(mirrored.chat_id, "12345")
        self.assertEqual(mirrored.name, "DongDong")

    def test_notices_key_is_distinct_from_missing_prompt(self):
        """Adopting must not silence the manual prompt before it could fire."""
        handler = self._make_handler()
        self.assertTrue(self._run(handler, self._source()))
        self.assertTrue(has_noticed("telegram", HOME_CHANNEL_AUTOSET))
        self.assertFalse(has_noticed("telegram", HOME_CHANNEL_MISSING))
