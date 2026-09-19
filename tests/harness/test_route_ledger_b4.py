"""B4 — route_ledger 粗算成本 + 契约接线。"""
from __future__ import annotations

import unittest
from pathlib import Path


def _root():
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "vermes_cli" / "route_economics.py").exists():
            return p
    return here.parents[2]


ROOT = _root()


class TestRouteEconomics(unittest.TestCase):
    def test_estimate_free_provider_zero(self):
        from vermes_cli.route_economics import estimate_cost_usd
        self.assertEqual(estimate_cost_usd("agnes", 1000, 1000), 0.0)

    def test_estimate_unknown_provider_zero_not_fake(self):
        from vermes_cli.route_economics import estimate_cost_usd
        self.assertEqual(estimate_cost_usd("mystery", 1_000_000, 1_000_000), 0.0)

    def test_estimate_paid_provider_positive(self):
        from vermes_cli.route_economics import estimate_cost_usd
        cost = estimate_cost_usd("deepseek", 1_000_000, 1_000_000)
        self.assertGreater(cost, 0)

    def test_format_cost_hint_honest(self):
        from vermes_cli.route_economics import format_cost_hint
        self.assertEqual(format_cost_hint(None), "")
        self.assertIn("粗算", format_cost_hint(0.0))
        self.assertIn("非精确", format_cost_hint(0.001))


class TestRouteLedgerWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = (ROOT / "vermes_state.py").read_text(encoding="utf-8")
        cls.chat = (ROOT / "vermes_cli/blueprints/chat.py").read_text(encoding="utf-8")
        cls.header = (ROOT / "frontend/src/components/ChatHeader.vue").read_text(encoding="utf-8")

    def test_schema_and_db_api(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS route_ledger", self.state)
        self.assertIn("def record_route_ledger", self.state)

    def test_chat_fills_estimate_and_persists(self):
        self.assertIn("estimate_cost_usd", self.chat)
        self.assertIn("record_route_ledger", self.chat)
        self.assertIn('"estimated_cost"', self.chat)

    def test_header_tooltip_shows_cost(self):
        self.assertIn("routeTitle", self.header)
        self.assertIn("粗算", self.header)
        self.assertIn("费用暂无信号", self.header)


class TestRouteLedgerPersist(unittest.TestCase):
    def test_insert_row(self):
        import tempfile
        from pathlib import Path as P
        from vermes_state import SessionDB
        tmp = P(tempfile.mkdtemp(prefix="route-ledger-"))
        db = SessionDB(db_path=tmp / "state.db")
        rid = db.record_route_ledger(
            session_id="s1",
            requested_model="auto:cost",
            strategy="cost",
            provider="deepseek",
            model="deepseek-chat",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            estimated_cost=0.0008,
        )
        self.assertGreater(rid, 0)
        row = db._conn.execute("SELECT * FROM route_ledger WHERE id=?", (rid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["provider"], "deepseek")
        self.assertEqual(row["total_tokens"], 1500)


if __name__ == "__main__":
    unittest.main()
