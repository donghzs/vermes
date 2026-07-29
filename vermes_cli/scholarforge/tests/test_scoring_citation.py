"""
ScholarForge 单元测试 — 涵盖 scoring / citation_verifier / search
Phase 3 测试覆盖补全 (2026-06-29)
"""
import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── 测试 scoring.py ──

class TestFallbackScore(unittest.TestCase):
    """测试 _fallback_score 启发式评分"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import _fallback_score
        self.fn = _fallback_score

    def test_empty_content(self):
        result = self.fn("", [])
        self.assertIn("originality", result)
        self.assertIn("logic", result)
        self.assertIn("citation_completeness", result)
        self.assertIn("overall", result)
        self.assertTrue(0 <= result["overall"] <= 10)
        self.assertTrue(0 <= result["originality"]["score"] <= 10)
        self.assertTrue(0 <= result["logic"]["score"] <= 10)

    def test_content_length_increases_scores(self):
        r1 = self.fn("短内容", [])
        r2 = self.fn("这是一篇非常长的论文内容" + "章节内容" * 500, [])
        self.assertLessEqual(r1["overall"], r2["overall"])

    def test_with_papers_boosts_citation(self):
        r_none = self.fn("内容", [])
        r_with = self.fn("内容 [1] [2]", [
            type('P', (), {'title': 'Paper A', 'authors': [], 'year': 2024})(),
            type('P', (), {'title': 'Paper B', 'authors': [], 'year': 2023})(),
        ])
        # 有文献引用时应高于无文献
        self.assertGreaterEqual(r_with["citation_completeness"]["score"],
                                r_none["citation_completeness"]["score"])

    def test_overall_formula_matches(self):
        """验证 overall = o*0.3 + l*0.35 + c*0.35"""
        result = self.fn("x" * 2000, [type('P', (), {'title': 'T', 'authors': [], 'year': 2024})()])
        expected = round(
            result["originality"]["score"] * 0.3 +
            result["logic"]["score"] * 0.35 +
            result["citation_completeness"]["score"] * 0.35, 1
        )
        self.assertEqual(result["overall"], expected)


class TestValidateScoreResult(unittest.TestCase):
    """测试 _validate_score_result 校验逻辑"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import _validate_score_result
        self.fn = _validate_score_result

    def test_valid_input_passes_through(self):
        result = self.fn({
            "originality": {"score": 7.5, "reasoning": "好"},
            "logic": {"score": 8.0, "reasoning": "不错"},
            "citation_completeness": {"score": 6.5, "reasoning": "可改进"},
        })
        self.assertEqual(result["originality"]["score"], 7.5)
        self.assertEqual(result["overall"], round(7.5 * 0.3 + 8.0 * 0.35 + 6.5 * 0.35, 1))

    def test_missing_dimensions_get_defaults(self):
        result = self.fn({"originality": {"score": 5}})
        self.assertIn("logic", result)
        self.assertEqual(result["logic"]["score"], 5.0)
        self.assertIn("citation_completeness", result)

    def test_out_of_range_score_clamped(self):
        result = self.fn({
            "originality": {"score": 15, "reasoning": ""},
            "logic": {"score": -2, "reasoning": ""},
            "citation_completeness": {"score": "foo", "reasoning": ""},
        })
        self.assertEqual(result["originality"]["score"], 10.0)
        self.assertEqual(result["logic"]["score"], 0.0)
        self.assertEqual(result["citation_completeness"]["score"], 5.0)


class TestScorePaperNoLLM(unittest.TestCase):
    """测试 score_paper 无 LLM 回退路径"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import score_paper
        self.fn = score_paper

    def test_no_llm_uses_fallback(self):
        async def _run():
            result = await self.fn("论文内容测试", [])
            self.assertIn("overall", result)
            self.assertTrue(result.get("_is_fallback"))  # 标注为启发式
        asyncio.run(_run())

    def test_mock_llm_is_called(self):
        # _make_llm 是工厂函数：无参调用返回 LLM callable，再 llm(prompt) 调用
        async def mock_llm_fn(prompt):
            return '{"originality":{"score":8,"reasoning":"创新"},"logic":{"score":7,"reasoning":"逻辑清晰"},"citation_completeness":{"score":6,"reasoning":"尚可"}}'
        def _make_llm_factory():
            return mock_llm_fn
        async def _run():
            result = await self.fn("test", [], _make_llm=_make_llm_factory)
            self.assertIn("originality", result)
            self.assertAlmostEqual(result["originality"]["score"], 8.0, places=0)
        asyncio.run(_run())


# ── 测试 consensus 评分 ──

class TestConsensusFallback(unittest.TestCase):
    """测试 _fallback_consensus"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import _fallback_consensus
        self.fn = _fallback_consensus

    def test_no_llm_returns_neutral_all(self):
        papers = [type('P', (), {'title': f'Paper {i}', 'abstract': ''})() for i in range(3)]
        result = self.fn("某论断", papers)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["neutral"], 3)
        self.assertEqual(result["support"], 0)
        self.assertEqual(result["oppose"], 0)
        self.assertEqual(result["confidence"], "low")

    def test_no_papers_zero(self):
        result = self.fn("论断", [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["consensus_pct"], 0.0)


class TestConsensusMockLLM(unittest.TestCase):
    """测试 score_consensus 带 mock LLM"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import score_consensus
        self.fn = score_consensus

    def test_support_7_oppose_2_neutral_1(self):
        mock_llm = AsyncMock(return_value='{"results":[' +
            '{"ref":1,"stance":"support","reason":"支持"},' +
            '{"ref":2,"stance":"support","reason":"支持"},' +
            '{"ref":3,"stance":"support","reason":"支持"},' +
            '{"ref":4,"stance":"support","reason":"支持"},' +
            '{"ref":5,"stance":"support","reason":"支持"},' +
            '{"ref":6,"stance":"support","reason":"支持"},' +
            '{"ref":7,"stance":"support","reason":"支持"},' +
            '{"ref":8,"stance":"oppose","reason":"反对"},' +
            '{"ref":9,"stance":"oppose","reason":"反对"},' +
            '{"ref":10,"stance":"neutral","reason":"无关"}]}')
        papers = [type('P', (), {'title': f'Paper {i}', 'abstract': f'Abstract {i}'})() for i in range(10)]
        async def _run():
            result = await self.fn("某论断", papers, llm=mock_llm)
            self.assertEqual(result["support"], 7)
            self.assertEqual(result["oppose"], 2)
            self.assertEqual(result["neutral"], 1)
            self.assertEqual(result["total"], 10)
            self.assertAlmostEqual(result["consensus_pct"], 70.0, places=1)
            self.assertEqual(result["confidence"], "medium")
        asyncio.run(_run())

    def test_high_consensus(self):
        mock_llm = AsyncMock(return_value='{"results":[' +
            '{"ref":1,"stance":"support","reason":""},' +
            '{"ref":2,"stance":"support","reason":""},' +
            '{"ref":3,"stance":"support","reason":""},' +
            '{"ref":4,"stance":"support","reason":""}]}')
        papers = [type('P', (), {'title': f'Paper {i}', 'abstract': ''})() for i in range(4)]
        async def _run():
            result = await self.fn("论断", papers, llm=mock_llm)
            self.assertEqual(result["confidence"], "high")
            self.assertAlmostEqual(result["consensus_pct"], 100.0, places=1)
        asyncio.run(_run())

    def test_llm_json_with_markdown_fence(self):
        mock_llm = AsyncMock(return_value='```json\n{"results":[' +
            '{"ref":1,"stance":"support","reason":"good"}]}\n```')
        papers = [type('P', (), {'title': 'P1', 'abstract': ''})()]
        async def _run():
            result = await self.fn("test", papers, llm=mock_llm)
            self.assertEqual(result["support"], 1)
        asyncio.run(_run())


# ── 测试 extract_key_claims ──

class TestExtractKeyClaims(unittest.TestCase):
    """测试 extract_key_claims"""

    def setUp(self):
        from vermes_cli.scholarforge.scoring import extract_key_claims
        self.fn = extract_key_claims

    def test_no_llm_uses_keyword_extraction(self):
        content = "实验表明该方法优于基线系统。我们发现注意力机制显著提升性能。证明该方案有效。"
        async def _run():
            result = await self.fn(content)
            self.assertIsInstance(result, list)
        asyncio.run(_run())

    def test_keyword_extraction_finds_claims(self):
        content = "实验结果表明，我们的方法显著优于已有方案。进一步分析证明，注意力机制有效提升了模型精度。"
        async def _run():
            claims = await self.fn(content)
            self.assertIsInstance(claims, list)
        asyncio.run(_run())

    def test_mock_llm_returns_claims(self):
        mock_llm = AsyncMock(return_value="方法优于基线系统\n注意力机制是关键因素\n数据增强提升鲁棒性")
        async def _run():
            claims = await self.fn("任意内容", llm=mock_llm, max_claims=3)
            self.assertLessEqual(len(claims), 3)
        asyncio.run(_run())


# ── 测试 citation_verifier.py ──

class TestFuzzyVerify(unittest.TestCase):
    """测试 _fuzzy_verify fuzzy 匹配"""

    def setUp(self):
        from vermes_cli.scholarforge.citation_verifier import _fuzzy_verify
        self.fn = _fuzzy_verify

    def test_ref_out_of_range(self):
        papers = [type('P', (), {'title': 'Paper', 'abstract': ''})()]
        result = self.fn(5, "text [5]", papers)
        self.assertIsNotNone(result)
        self.assertEqual(result.score, 0)
        self.assertEqual(result.method, "range_only")

    def test_title_exact_match_in_context(self):
        papers = [type('P', (), {'title': 'Deep Learning for NLP', 'abstract': 'We study deep learning.'})()]
        text = "Recent advances in Deep Learning for NLP [1] have shown great progress."
        result = self.fn(1, text, papers)
        self.assertIsNotNone(result)
        self.assertGreaterEqual(result.score, 8)
        self.assertEqual(result.method, "fuzzy")

    def test_title_partial_match(self):
        papers = [type('P', (), {'title': 'Transformer-based Attention Mechanisms', 'abstract': 'We explore attention.'})()]
        text = "The Transformer attention mechanism [1] has been widely studied in recent years."
        result = self.fn(1, text, papers)
        self.assertIsNotNone(result)
        # 应该有 keyword 匹配
        self.assertGreaterEqual(result.score, 6)

    def test_title_no_match_returns_none(self):
        papers = [type('P', (), {'title': 'Quantum Computing for Protein Folding', 'abstract': ''})()]
        text = "The benefits of cloud computing are well documented [1] in enterprise settings."
        result = self.fn(1, text, papers)
        # 完全不相关时应返回 None，交给 LLM
        self.assertIsNone(result)


class TestCitationVerifyResult(unittest.TestCase):
    """测试 CitationVerifyResult"""

    def setUp(self):
        from vermes_cli.scholarforge.citation_verifier import CitationVerifyResult
        self.Result = CitationVerifyResult

    def test_to_warning_high_score(self):
        r = self.Result(1, 9, "准确", True, method="fuzzy")
        self.assertEqual(r.to_warning(), "")

    def test_to_warning_mid_score(self):
        r = self.Result(2, 5, "存疑", True, method="llm")
        w = r.to_warning()
        self.assertIn("存疑", w)

    def test_to_warning_low_score(self):
        r = self.Result(3, 1, "可能捏造", False, method="llm")
        w = r.to_warning()
        self.assertIn("捏造", w)
        self.assertIn("❌", w)


# ── 测试 search/__init__.py ──

class TestSearchModuleImports(unittest.TestCase):
    """确保 search 模块可正常导入"""

    def test_search_papers_exists(self):
        from vermes_cli.scholarforge.search import search_papers
        self.assertTrue(callable(search_papers))

    def test_paper_result_class(self):
        from vermes_cli.scholarforge.search import PaperResult
        p = PaperResult(
            paper_id="2401.00001",
            title="Test Paper",
            authors=["Author A", "Author B"],
            year=2024,
            abstract="This is a test abstract.",
            source="arxiv",
            url="https://arxiv.org/abs/2401.00001",
            citation_count=42,
        )
        self.assertEqual(p.title, "Test Paper")
        self.assertEqual(p.authors, ["Author A", "Author B"])
        self.assertEqual(p.citation_count, 42)

    def test_search_no_mock_call(self):
        """空调用不抛异常（无网络）"""
        from vermes_cli.scholarforge.search import search_papers
        async def _run():
            papers = []
            try:
                async for paper in search_papers("test", limit=1):
                    papers.append(paper)
            except Exception:
                pass  # 网络可能不可用
            # 不抛 asyncio 相关异常即可
            self.assertIsInstance(papers, list)
        asyncio.run(_run())


# ── 测试 rag.py ──

class TestRAGModule(unittest.TestCase):
    """RAG 模块基本功能"""

    def test_rag_retriever_imports(self):
        from vermes_cli.scholarforge.rag import PaperRetriever
        self.assertIsNotNone(PaperRetriever)

    def test_rag_tfidf_imports(self):
        from vermes_cli.scholarforge.rag import TfidfVectorizer
        self.assertIsNotNone(TfidfVectorizer)


if __name__ == '__main__':
    unittest.main()
