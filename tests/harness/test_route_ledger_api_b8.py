"""B8 — route_ledger 查询 API + SessionDB.list_route_ledger。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_state.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestRouteLedgerApiWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.state = (ROOT / "vermes_state.py").read_text(encoding="utf-8")

    def test_endpoint_registered(self):
        self.assertIn("async def route_ledger_list", self.chat)
        self.assertIn('"/api/route/ledger"', self.chat)
        self.assertIn("route_ledger_list", self.chat)

    def test_db_list_api(self):
        self.assertIn("def list_route_ledger", self.state)


class TestRouteLedgerApiBehavior(unittest.TestCase):
    def test_list_rows_and_summary(self):
        from vermes_state import SessionDB
        from vermes_cli.blueprints.chat import route_ledger_list

        tmp = Path(tempfile.mkdtemp(prefix="route-api-"))
        db = SessionDB(db_path=tmp / "state.db")
        db.record_route_ledger(
            session_id="s1", provider="deepseek", model="deepseek-chat",
            prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
            estimated_cost=0.001,
        )
        db.record_route_ledger(
            session_id="s2", provider="agnes", model="agnes-3.0-flash",
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
            estimated_cost=0.0,
        )
        try:
            data = asyncio.get_event_loop().run_until_complete(route_ledger_list(session_id="", limit=10))
        except RuntimeError:
            data = asyncio.new_event_loop().run_until_complete(route_ledger_list(session_id="", limit=10))
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["count"], 2)
        self.assertGreaterEqual(data["summary"]["total_tokens"], 1515)
        self.assertIn("粗算", data["summary"]["note"])
        # 按 session 过滤
        try:
            one = asyncio.get_event_loop().run_until_complete(route_ledger_list(session_id="s1", limit=10))
        except RuntimeError:
            one = asyncio.new_event_loop().run_until_complete(route_ledger_list(session_id="s1", limit=10))
        self.assertTrue(all(r["session_id"] == "s1" for r in one["rows"]))


if __name__ == "__main__":
    unittest.main()
