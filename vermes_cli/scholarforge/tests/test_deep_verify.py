"""Tier3 深度验证单测（注入 FakeProvider + Fake LLM）。

验证：
1. deep_verify_claims：每篇引文一次 LLM 判断，正确映射 supported 论断。
2. 无摘要的引文 → 标记「无法获取摘要，无法验证」。
3. format_deep_verify_report：按论断聚合（✅ 获支持 / ⚠️ 无支持）。
4. run_all_validators 在 claims+papers 时包含 Tier3 深度验证段落（联网/LLM 均 mock）。
"""
import asyncio
import unittest
from unittest.mock import patch

from vermes_cli.scholarforge.deep_verify import (
    deep_verify_claims,
    format_deep_verify_report,
)
from agent.literature_providers.semanticscholar import SemanticScholarProvider


class FakeS2Provider:
    def _s2_paper_key(self, paper_id):
        return paper_id.strip()

    def get_paper(self, paper_id):
        if paper_id.strip() == "10.1/paperA":
            node = SemanticScholarProvider._normalize_s2_paper({
                "title": "Paper A", "authors": [{"name": "A"}], "year": 2020,
                "venue": "V", "citationCount": 1, "url": "u", "paperId": "a",
                "externalIds": {"DOI": "10.1/paperA"},
                "abstract": "本文提出用注意力机制提升翻译质量。",
            })
            return {"success": True, "paper": node}
        return {"success": False, "error": "404"}


def fake_llm_supports_claim1(prompt):
    # 断言「第1条被支持」
    return '{"supported_claims": [1], "confidence": 0.85, "reason": "摘要直接陈述该结论"}'


class TestDeepVerify(unittest.TestCase):
    def test_maps_supported_claim(self):
        claims = ["用注意力机制提升翻译质量", "本文无显著贡献"]
        papers = [{"title": "Paper A", "doi": "10.1/paperA"}]
        results = asyncio.run(
            deep_verify_claims(claims, papers, provider=FakeS2Provider(), llm=fake_llm_supports_claim1)
        )
        # 2 claims × 1 paper = 2 rows
        self.assertEqual(len(results), 2)
        sup = [r for r in results if r["supported"]]
        self.assertEqual(len(sup), 1)
        self.assertEqual(sup[0]["claim"], "用注意力机制提升翻译质量")
        self.assertEqual(sup[0]["confidence"], 0.85)

    def test_paper_without_abstract_unverifiable(self):
        claims = ["某论断"]
        papers = [{"title": "X", "doi": "10.1/missing"}]
        results = asyncio.run(
            deep_verify_claims(claims, papers, provider=FakeS2Provider(), llm=fake_llm_supports_claim1)
        )
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["supported"])
        self.assertIn("无法获取摘要", results[0]["reason"])

    def test_format_report_groups_by_claim(self):
        results = [
            {"claim": "C1", "paper_doi": "d1", "paper_title": "A", "supported": True, "confidence": 0.8, "reason": "ok"},
            {"claim": "C1", "paper_doi": "d2", "paper_title": "B", "supported": False, "confidence": 0.1, "reason": "no"},
            {"claim": "C2", "paper_doi": "d3", "paper_title": "C", "supported": False, "confidence": 0.1, "reason": "no"},
        ]
        report = format_deep_verify_report(results)
        self.assertIn("### Tier3 深度验证", report)
        self.assertIn("✅", report)
        self.assertIn("⚠️", report)
        # C1 获 1 篇支持；C2 无支持
        self.assertIn("C1", report)
        self.assertIn("C2", report)

    def test_run_all_validators_includes_tier3(self):
        from vermes_cli.scholarforge.validators import run_all_validators

        claims = ["用注意力机制提升翻译质量"]
        papers = [{"title": "Paper A", "doi": "10.1/paperA"}]
        canned = [{
            "claim": "用注意力机制提升翻译质量", "paper_doi": "10.1/paperA",
            "paper_title": "Paper A", "supported": True, "confidence": 0.8, "reason": "ok",
        }]

        async def _fake_dv(*a, **k):
            return canned

        async def _fake_cit(*a, **k):
            return []

        # 避免联网：mock 引用真实性校验 + deep_verify 调用
        with patch(
            "vermes_cli.scholarforge.validators.verify_citation_authenticity",
            new=_fake_cit,
        ), patch(
            "vermes_cli.scholarforge.deep_verify.deep_verify_claims",
            new=_fake_dv,
        ):
            report = asyncio.run(run_all_validators(papers=papers, claims=claims))
        self.assertIn("Tier3 深度验证", report)
        self.assertIn("Paper A", report)


if __name__ == "__main__":
    unittest.main()
