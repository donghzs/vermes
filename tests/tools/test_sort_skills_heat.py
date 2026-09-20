"""L1 热度 tie-breaker — 同分类内软排序，禁止全局重排（Phase 2）。"""
from __future__ import annotations

import unittest
from unittest.mock import patch


class TestSortSkillsHeatTieBreaker(unittest.TestCase):
    def test_same_category_recorded_first_by_use_count(self):
        from tools.skills_tool import _sort_skills

        skills = [
            {"name": "aaa", "category": "devops", "description": "a"},
            {"name": "zzz-hot", "category": "devops", "description": "z"},
            {"name": "mmm", "category": "devops", "description": "m"},
        ]
        usage = {
            "zzz-hot": {"use_count": 30},
            "mmm": {"use_count": 5},
            # aaa: no record
        }
        with patch("tools.skill_usage.load_usage", return_value=usage):
            out = _sort_skills(skills)
        names = [s["name"] for s in out]
        # recorded first by use_count desc; no-record last by name
        self.assertEqual(names, ["zzz-hot", "mmm", "aaa"])

    def test_no_usage_keeps_name_order(self):
        from tools.skills_tool import _sort_skills

        skills = [
            {"name": "b", "category": "creative"},
            {"name": "a", "category": "creative"},
            {"name": "c", "category": "creative"},
        ]
        with patch("tools.skill_usage.load_usage", return_value={}):
            out = _sort_skills(skills)
        self.assertEqual([s["name"] for s in out], ["a", "b", "c"])

    def test_categories_still_primary(self):
        """Heat must not promote a skill across category boundaries."""
        from tools.skills_tool import _sort_skills

        skills = [
            {"name": "cold-dev", "category": "devops"},
            {"name": "hot-creative", "category": "creative"},
        ]
        usage = {"hot-creative": {"use_count": 99}}
        with patch("tools.skill_usage.load_usage", return_value=usage):
            out = _sort_skills(skills)
        self.assertEqual([s["name"] for s in out], ["hot-creative", "cold-dev"])

    def test_zero_use_count_still_recorded_before_unrecorded(self):
        """A usage *record* with use_count=0 still counts as 'has telemetry'."""
        from tools.skills_tool import _sort_skills

        skills = [
            {"name": "no-rec", "category": "x"},
            {"name": "has-rec-zero", "category": "x"},
        ]
        usage = {"has-rec-zero": {"use_count": 0}}
        with patch("tools.skill_usage.load_usage", return_value=usage):
            out = _sort_skills(skills)
        # has-rec-zero has a record → comes first even at 0; name order among equals
        self.assertEqual([s["name"] for s in out], ["has-rec-zero", "no-rec"])

    def test_load_usage_failure_falls_back_to_name_order(self):
        from tools.skills_tool import _sort_skills

        skills = [
            {"name": "b", "category": "x"},
            {"name": "a", "category": "x"},
        ]

        def _boom():
            raise RuntimeError("no usage")

        with patch("tools.skill_usage.load_usage", side_effect=_boom):
            out = _sort_skills(skills)
        self.assertEqual([s["name"] for s in out], ["a", "b"])


class TestAnnotateUsage(unittest.TestCase):
    def test_attaches_use_count_only_when_present(self):
        from tools.skills_tool import _annotate_usage

        skills = [
            {"name": "hot", "category": "c"},
            {"name": "cold", "category": "c"},
        ]
        usage = {"hot": {"use_count": 12, "view_count": 3}}
        with patch("tools.skill_usage.load_usage", return_value=usage):
            out = _annotate_usage(list(skills))
        by = {s["name"]: s for s in out}
        self.assertEqual(by["hot"]["use_count"], 12)
        self.assertEqual(by["hot"]["view_count"], 3)
        self.assertNotIn("use_count", by["cold"])


if __name__ == "__main__":
    unittest.main()
