"""B5 — cron monitor-mode API/UI 透传契约（后端 jobs.py + blueprint + Settings）。"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "cron" / "jobs.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestCronMonitorWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = (ROOT / "cron/jobs.py").read_text(encoding="utf-8")
        cls.sched = (ROOT / "cron/scheduler.py").read_text(encoding="utf-8")
        cls.bp = (ROOT / "vermes_cli/blueprints/cron_jobs.py").read_text(encoding="utf-8")
        cls.ui = (ROOT / "frontend/src/components/Settings.vue").read_text(encoding="utf-8")

    def test_backend_create_accepts_monitor_fields(self):
        self.assertIn("monitor_mode: bool = False", self.jobs)
        self.assertIn("monitor_target", self.jobs)
        self.assertIn("monitor_mode: bool = False", self.bp)
        self.assertIn("monitor_mode=bool(body.monitor_mode)", self.bp)
        self.assertIn("monitor_target=(body.monitor_target or None)", self.bp)

    def test_scheduler_short_circuits_on_same_hash(self):
        self.assertIn("monitor_mode", self.sched)
        self.assertIn("_get_monitor_hash", self.sched)
        self.assertIn("_resolve_monitor_target", self.sched)

    def test_settings_ui_has_toggle_and_target(self):
        self.assertIn("cron-monitor-toggle", self.ui)
        self.assertIn("cron-monitor-target", self.ui)
        self.assertIn("toggleCronMonitor", self.ui)
        self.assertIn("monitor_mode", self.ui)
        self.assertIn("PUT", self.ui)

    def test_update_job_normalizes_monitor_fields(self):
        compact = re.sub(r"\s+", "", self.jobs)
        self.assertIn('if"monitor_mode"inupdates:', compact)
        self.assertIn("bool(updates[\"monitor_mode\"])", compact)


class TestCronMonitorCreateUnit(unittest.TestCase):
    def test_create_job_persists_monitor_fields(self):
        import tempfile
        import os
        from cron import jobs as cron_jobs

        tmp = Path(tempfile.mkdtemp(prefix="cron-b5-"))
        old_dir, old_file, old_out = cron_jobs.CRON_DIR, cron_jobs.JOBS_FILE, cron_jobs.OUTPUT_DIR
        try:
            cron_jobs.CRON_DIR = tmp / "cron"
            cron_jobs.JOBS_FILE = cron_jobs.CRON_DIR / "jobs.json"
            cron_jobs.OUTPUT_DIR = cron_jobs.CRON_DIR / "output"
            job = cron_jobs.create_job(
                prompt="check site",
                schedule="0 9 * * *",
                name="monitor-demo",
                monitor_mode=True,
                monitor_target="https://example.com/health",
            )
            self.assertTrue(job.get("monitor_mode"))
            self.assertEqual(job.get("monitor_target"), "https://example.com/health")
            listed = cron_jobs.list_jobs(True)
            found = next(j for j in listed if j.get("id") == job.get("id"))
            self.assertTrue(found.get("monitor_mode"))
        finally:
            cron_jobs.CRON_DIR = old_dir
            cron_jobs.JOBS_FILE = old_file
            cron_jobs.OUTPUT_DIR = old_out
            os.environ.pop("VERMES_HOME", None)


if __name__ == "__main__":
    unittest.main()
