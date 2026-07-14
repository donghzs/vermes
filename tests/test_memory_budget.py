"""Tests for memory_budget — unified token budget management."""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.memory_budget import (
    apply_budget,
    get_memory_stats,
    format_memory_summary,
    _estimate_tokens,
    _trim_block,
    _TOTAL_BUDGET_CHARS,
)


class TestEstimateTokens(unittest.TestCase):
    def test_simple_text(self):
        self.assertEqual(_estimate_tokens("hello world"), 2)  # 11 chars / 4

    def test_empty(self):
        self.assertEqual(_estimate_tokens(""), 0)

    def test_long_text(self):
        text = "a" * 400
        self.assertEqual(_estimate_tokens(text), 100)


class TestTrimBlock(unittest.TestCase):
    def test_no_trimming_needed(self):
        text = "short text"
        self.assertEqual(_trim_block(text, 100), text)

    def test_simple_trimming(self):
        text = "a" * 200
        result = _trim_block(text, 50)
        self.assertLessEqual(len(result), 50)
        self.assertIn("(trimmed)", result)

    def test_xml_tag_preservation(self):
        text = "<recalled_context>" + "content " * 200 + "</recalled_context>"
        result = _trim_block(text, 100)
        self.assertTrue(result.startswith("<recalled_context>"))
        self.assertTrue(result.endswith("</recalled_context>"))
        self.assertIn("(trimmed)", result)

    def test_empty_content(self):
        self.assertEqual(_trim_block("", 100), "")


class TestApplyBudget(unittest.TestCase):
    def test_under_budget_no_trimming(self):
        blocks = {
            "_recall_context": "a" * 500,
            "_handoff_context": "b" * 300,
        }
        result = apply_budget(blocks)
        self.assertEqual(len(result["_recall_context"]), 500)
        self.assertEqual(len(result["_handoff_context"]), 300)

    def test_over_budget_trims_lowest_priority(self):
        # Create blocks that total over budget
        blocks = {
            "_recall_context": "a" * 1600,    # priority 1 (highest)
            "_handoff_context": "b" * 1200,   # priority 2
            "_evolution_context": "c" * 1000,  # priority 3
            "_decisions_context": "d" * 800,   # priority 4 (lowest)
        }
        # Total = 4600 > 4000 budget
        result = apply_budget(blocks)

        # All blocks should still be present (trimmed, not dropped)
        self.assertEqual(len(result), 4)

        # Total should be under budget (approximately)
        total = sum(len(v) for v in result.values())
        self.assertLessEqual(total, _TOTAL_BUDGET_CHARS + 100)  # small margin for tags

        # Highest priority block should be least trimmed
        self.assertGreater(len(result["_recall_context"]), len(result["_decisions_context"]))

    def test_empty_blocks_filtered(self):
        blocks = {
            "_recall_context": "real content",
            "_handoff_context": "",
            "_evolution_context": None,
        }
        result = apply_budget(blocks)
        self.assertIn("_recall_context", result)
        self.assertNotIn("_handoff_context", result)
        self.assertNotIn("_evolution_context", result)

    def test_all_empty_returns_empty(self):
        blocks = {
            "_recall_context": "",
            "_handoff_context": "",
        }
        result = apply_budget(blocks)
        self.assertEqual(result, {})

    def test_single_block_over_budget(self):
        blocks = {
            "_recall_context": "a" * 5000,  # Way over budget
        }
        result = apply_budget(blocks)
        # Should be trimmed to soft cap
        self.assertLessEqual(len(result["_recall_context"]), 1600 + 100)

    def test_budget_exhaustion_drops_blocks(self):
        # Create blocks where high-priority blocks consume all budget
        blocks = {
            "_recall_context": "a" * 1600,
            "_handoff_context": "b" * 1200,
            "_evolution_context": "c" * 1000,
            "_decisions_context": "d" * 800,
        }
        # Total = 4600, budget = 4000
        # recall=1600, handoff=1200 → 2800, remaining=1200
        # evolution gets min(1000, 1200) = 1000 → 3800, remaining=200
        # decisions gets min(800, 200) = 200 → 4000
        result = apply_budget(blocks)

        # All blocks present, but decisions is heavily trimmed
        self.assertIn("_decisions_context", result)
        self.assertLessEqual(len(result["_decisions_context"]), 300)

    def test_unknown_block_name(self):
        blocks = {
            "_unknown_block": "x" * 500,
        }
        result = apply_budget(blocks)
        # Unknown blocks get default priority 99 and soft cap 800
        self.assertIn("_unknown_block", result)


class TestGetMemoryStats(unittest.TestCase):
    def test_stats_structure(self):
        blocks = {
            "_recall_context": "a" * 400,
            "_handoff_context": "b" * 200,
        }
        stats = get_memory_stats(blocks)
        self.assertIn("blocks", stats)
        self.assertIn("total_chars", stats)
        self.assertIn("total_tokens_est", stats)
        self.assertIn("budget_chars", stats)
        self.assertIn("over_budget", stats)
        self.assertEqual(stats["total_chars"], 600)
        self.assertFalse(stats["over_budget"])

    def test_over_budget_flag(self):
        blocks = {
            "_recall_context": "a" * 5000,
        }
        stats = get_memory_stats(blocks)
        self.assertTrue(stats["over_budget"])

    def test_empty_blocks(self):
        stats = get_memory_stats({})
        self.assertEqual(stats["total_chars"], 0)
        self.assertFalse(stats["over_budget"])

    def test_per_block_stats(self):
        blocks = {
            "_recall_context": "a" * 400,
        }
        stats = get_memory_stats(blocks)
        block_stats = stats["blocks"]["_recall_context"]
        self.assertEqual(block_stats["chars"], 400)
        self.assertEqual(block_stats["tokens_est"], 100)
        self.assertEqual(block_stats["priority"], 1)


class TestFormatMemorySummary(unittest.TestCase):
    def test_summary_format(self):
        blocks = {
            "_recall_context": "a" * 400,
            "_handoff_context": "b" * 200,
            "_evolution_context": "c" * 160,
            "_decisions_context": "",
        }
        summary = format_memory_summary(blocks)
        self.assertIn("recall=400ch", summary)
        self.assertIn("handoff=200ch", summary)
        self.assertIn("evolution=160ch", summary)
        self.assertIn("total=760ch", summary)
        self.assertIn("4000", summary)

    def test_empty_blocks(self):
        summary = format_memory_summary({})
        self.assertIn("total=0ch", summary)

    def test_partial_blocks(self):
        blocks = {
            "_recall_context": "a" * 500,
        }
        summary = format_memory_summary(blocks)
        self.assertIn("recall=500ch", summary)
        self.assertIn("handoff=0ch", summary)


if __name__ == "__main__":
    unittest.main()
