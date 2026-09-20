"""M5-a — yuanbao auto-sethome must preserve comments in config.yaml.

This is the *fourth* write path that used to reach config.yaml through
``yaml.safe_load`` + ``yaml.dump`` (the other three were mimo's M5, covered in
``tests/vermes_cli/``). They share one failure mode: the user's comments are
irreversibly flattened on the first save.

This suite drives the real middleware and reads the file back — it does not
introspect source.
"""

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path


SEED = """\
# my hand-written note — must survive
platforms:
  discord:
    enabled: true  # trailing note too
YUANBAO_HOME_CHANNEL: "old-group-123"   # keep this one as well
"""


class _FakeAdapter:
    def __init__(self):
        self.name = "yuanbao"
        self._auto_sethome_done = False


def _run_middleware(ctx, called):
    from gateway.platforms.yuanbao import AutoSetHomeMiddleware

    async def next_fn():
        called.append(True)

    asyncio.run(AutoSetHomeMiddleware().handle(ctx, next_fn))


class TestYuanbaoAutoSetHomePreservesComments(unittest.TestCase):
    def setUp(self):
        self._saved_home = os.environ.get("VERMES_HOME")
        self._saved_env = os.environ.get("YUANBAO_HOME_CHANNEL")
        self._tmp = Path(tempfile.mkdtemp(prefix="yuanbao-m5-"))
        os.environ["VERMES_HOME"] = str(self._tmp)
        os.environ.pop("YUANBAO_HOME_CHANNEL", None)
        (self._tmp / "config.yaml").write_text(SEED, encoding="utf-8")

    def tearDown(self):
        for key, value in (
            ("VERMES_HOME", self._saved_home),
            ("YUANBAO_HOME_CHANNEL", self._saved_env),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _ctx(self, chat_type="dm", chat_id="dm-7788"):
        from gateway.platforms.yuanbao import InboundContext

        return InboundContext(
            adapter=_FakeAdapter(),
            chat_id=chat_id,
            chat_type=chat_type,
            chat_name="DongDong",
        )

    def test_designates_dm_and_keeps_every_comment(self):
        called = []
        _run_middleware(self._ctx(), called)

        raw = (self._tmp / "config.yaml").read_text(encoding="utf-8")
        # Every one of the three seeded comments must survive.
        self.assertIn("# my hand-written note — must survive", raw)
        self.assertIn("# trailing note too", raw)
        self.assertIn("# keep this one as well", raw)
        # The value really changed, and the pipeline still ran.
        self.assertIn("dm-7788", raw)
        self.assertNotIn("old-group-123", raw)
        self.assertEqual(os.environ["YUANBAO_HOME_CHANNEL"], "dm-7788")
        self.assertEqual(called, [True], "middleware must not stop the pipeline")

    def test_written_file_still_parses(self):
        from utils import load_roundtrip_yaml

        _run_middleware(self._ctx(), [])
        data = load_roundtrip_yaml(self._tmp / "config.yaml")
        self.assertEqual(data.get("YUANBAO_HOME_CHANNEL"), "dm-7788")
        # Sibling structure untouched.
        self.assertTrue(data["platforms"]["discord"]["enabled"])

    def test_group_does_not_replace_existing_home(self):
        os.environ["YUANBAO_HOME_CHANNEL"] = "group:existing"
        _run_middleware(self._ctx(chat_type="group", chat_id="group-99"), [])
        raw = (self._tmp / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("old-group-123", raw)  # untouched
        self.assertEqual(os.environ["YUANBAO_HOME_CHANNEL"], "group:existing")

    def test_missing_config_file_is_created(self):
        (self._tmp / "config.yaml").unlink()
        _run_middleware(self._ctx(), [])
        raw = (self._tmp / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("dm-7788", raw)

    def test_write_failure_does_not_break_pipeline(self):
        """A read-only home must not take the inbound pipeline down."""
        called = []
        (self._tmp / "config.yaml").unlink()
        self._tmp.chmod(0o500)
        try:
            _run_middleware(self._ctx(), called)
        finally:
            self._tmp.chmod(0o700)
        self.assertEqual(called, [True], "pipeline must continue despite failure")
