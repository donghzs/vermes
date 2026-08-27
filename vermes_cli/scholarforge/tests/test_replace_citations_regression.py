"""
ScholarForge replace_citations 回归测试
覆盖 Step 0（force=True 碰撞重搜索，消除死代码）与 Step 1（llm_rerank 相对排序 + fail-open 兜底）。

背景：f77528eaf 之前，碰撞重搜索因 guard 恒 False 是死代码（LLM 精炼关键词从未生效），
且 score_relevance/llm_rerank 是 handler 内部闭包、无单测守护。本文件把这两项逻辑纳入守护，
确保"撞车占位符真正分离候选池 / 评分按 LLM 分数排序 / LLM 异常时回退粗排"不被未来改动破坏。
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _paper(title, abstract="", authors=None, year=2024, venue="J", doi=""):
    return SimpleNamespace(
        title=title,
        abstract=abstract,
        authors=authors or ["Author"],
        year=year,
        venue=venue,
        doi=doi,
    )


class TestScoreRelevance(unittest.TestCase):
    """score_relevance 是粗排打分（0-1），应优先命中关键词重叠。"""

    def test_favors_keyword_overlap(self):
        from vermes_cli.scholarforge.citation_matcher import score_relevance

        kw = "memory consolidation"
        high = _paper("Memory Consolidation in Sleep", abstract="studies memory consolidation")
        low = _paper("Quantum Computing Advances", abstract="qubits and gates")
        self.assertGreater(score_relevance(high, "ctx", kw), score_relevance(low, "ctx", kw))

    def test_range_zero_to_one(self):
        from vermes_cli.scholarforge.citation_matcher import score_relevance

        p = _paper("Memory Consolidation Study", abstract="about memory consolidation")
        s = score_relevance(p, "ctx", "memory consolidation")
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 1.0)


class TestLLMRerank(unittest.TestCase):
    """llm_rerank 用 LLM 分数对候选重新排序；分数数不符或异常时 fail-open 回退粗排。"""

    def _run(self, candidates, llm_return):
        from vermes_cli.scholarforge.citation_matcher import llm_rerank

        async def fake_llm(prompt, system="", **kwargs):
            return llm_return

        async def go():
            with patch("vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm):
                return await llm_rerank(candidates, "ctx", "kw")

        return asyncio.run(go())

    def test_orders_by_llm_scores(self):
        a, b, c = _paper("A"), _paper("B"), _paper("C")
        # LLM 给出顺序 0.2 / 0.9 / 0.5 → 校验 (paper, score) 映射正确
        # 注意：llm_rerank 返回顺序与输入一致（排序由调用方做），这里只验分数归属
        result = self._run([a, b, c], "1: 0.2\n2: 0.9\n3: 0.5")
        scores = {p.title: s for p, s in result}
        self.assertEqual(scores, {"A": 0.2, "B": 0.9, "C": 0.5})

    def test_falls_back_on_score_count_mismatch(self):
        # 候选 3 个，但 LLM 只回 2 个分数 → 数量不符 → 回退粗排（score_relevance）
        from vermes_cli.scholarforge.citation_matcher import score_relevance

        a = _paper("Memory Consolidation A", abstract="memory consolidation overlap")
        b = _paper("Quantum B", abstract="unrelated")
        c = _paper("Sleep C", abstract="partial")
        candidates = [a, b, c]
        result = self._run(candidates, "1: 0.9\n2: 0.4")  # 只有 2 行
        # 回退顺序应与 score_relevance 降序一致
        expected = sorted(candidates, key=lambda p: score_relevance(p, "ctx", "kw"), reverse=True)
        self.assertEqual([p.title for p, _ in result], [p.title for p in expected])

    def test_falls_back_on_llm_exception(self):
        from vermes_cli.scholarforge.citation_matcher import score_relevance

        a = _paper("Memory Consolidation A", abstract="memory consolidation overlap")
        b = _paper("Quantum B", abstract="unrelated")
        c = _paper("Sleep C", abstract="partial")
        candidates = [a, b, c]

        async def boom(prompt, system="", **kwargs):
            raise RuntimeError("llm down")

        async def go():
            with patch("vermes_cli.scholarforge.tools._call_llm", side_effect=boom):
                from vermes_cli.scholarforge.citation_matcher import llm_rerank
                return await llm_rerank(candidates, "ctx", "kw")

        result = asyncio.run(go())
        expected = sorted(candidates, key=lambda p: score_relevance(p, "ctx", "kw"), reverse=True)
        self.assertEqual([p.title for p, _ in result], [p.title for p in expected])


class TestForceResarchOnCollision(unittest.TestCase):
    """Step 0 回归：碰撞检测后，LLM 精炼的关键词必须真正触发 force=True 重搜索，
    且最终引用应来自 refined 候选池（而非初始池）。这是当初死代码溜入同款风险的守护。"""

    def test_collision_triggers_refined_research(self):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_replace_citations

        # 初始池（关键词未精炼时命中）与 refined 池（LLM 精炼后命中）
        initial_pool = [
            _paper("Initial Study Alpha", abstract="x"),
            _paper("Initial Study Beta", abstract="y"),
            _paper("Initial Study Gamma", abstract="z"),
        ]
        refined_pool = [
            _paper("Refined Study Delta", abstract="x"),
            _paper("Refined Study Epsilon", abstract="y"),
            _paper("Refined Study Zeta", abstract="z"),
        ]

        search_calls = []

        async def fake_search_papers(keyword, limit=10):
            search_calls.append(keyword)
            pool = refined_pool if "refined" in keyword.lower() else initial_pool
            for p in pool:
                yield p

        async def fake_llm(prompt, system="", **kwargs):
            if "关键短语" in prompt:
                return "refined keyword"
            if "打分" in prompt:
                # 每占位符候选 3 篇 → top-5 取全部 3 篇 → 3 个分数
                return "1: 0.9\n2: 0.5\n3: 0.4"
            return "ok"

        fake_verify = MagicMock(score=10, accurate=True)

        draft = (
            "Memory consolidation [1] helps learning. "
            "Memory consolidation [2] helps learning."
        )

        async def go():
            with patch(
                "vermes_cli.scholarforge.search.search_papers", fake_search_papers
            ), patch(
                "vermes_cli.scholarforge.tools._call_llm", side_effect=fake_llm
            ), patch(
                "vermes_cli.scholarforge.citation_verifier._fuzzy_verify",
                return_value=fake_verify,
            ):
                return await _handle_scholarforge_replace_citations({"draft": draft})

        report = asyncio.run(go())

        # 1) 初始搜索确实发生
        self.assertTrue(
            any("memory consolidation" in c.lower() for c in search_calls),
            f"初始搜索未触发，calls={search_calls}",
        )
        # 2) 碰撞后 LLM 精炼关键词的 force=True 重搜索确实执行
        self.assertIn(
            "refined keyword",
            search_calls,
            "碰撞重搜索（refined keyword）未执行——死代码回归！",
        )
        # 3) 最终引用来自 refined 池，而非初始池（撞车占位符已被 LLM 精炼真正分离/重搜）
        self.assertIn("Refined Study", report)
        self.assertNotIn("Initial Study", report)


class TestNegativeScoreThreshold(unittest.TestCase):
    """#5 负值/过低分回归：llm_rerank 全返回低于阈值(0.3)时，match_citations 必须跳过该引用，
    不得 argmax 强塞噪声文献（避免伪造引用）。守护 F-23 的 0.3 阈值不被未来改动破坏。"""

    def test_skips_all_below_threshold(self):
        from vermes_cli.scholarforge.citation_matcher import match_citations, MIN_MATCH_SCORE

        # 锁定阈值常量：若被调低，本条即报警
        self.assertEqual(MIN_MATCH_SCORE, 0.3)

        # 候选与引用上下文完全不相关
        pool = [
            _paper("Quantum Computing Survey", abstract="qubits and quantum gates"),
            _paper("Stock Market Prediction", abstract="trading and volatility"),
            _paper("Cooking Recipes", abstract="ingredients and oven"),
        ]

        async def fake_llm(prompt, system="", **kwargs):
            # 所有候选分数均远低于阈值
            return "1: 0.1\n2: 0.1\n3: 0.1"

        async def go():
            return await match_citations(
                unique_nums=[1],
                candidates={1: pool},
                num_context={1: "memory consolidation during slow-wave sleep"},
                num_keywords={1: "memory consolidation"},
                llm_call_fn=fake_llm,
            )

        result = asyncio.run(go())
        self.assertIn(1, result.failed, "低于阈值的匹配应被跳过(failed)")
        self.assertNotIn(1, result.num_to_ref, "不得把噪声文献编入参考文献")
        self.assertEqual(len(result.ref_list), 0, "ref_list 应为空")

    def test_cites_above_threshold(self):
        # 对照：分数高于阈值时应被正确引用，证明上面的跳过不是测试本身恒真
        from vermes_cli.scholarforge.citation_matcher import match_citations

        pool = [_paper("Memory Consolidation in Sleep", abstract="memory consolidation sleep")]

        async def fake_llm(prompt, system="", **kwargs):
            return "1: 0.9"

        async def go():
            return await match_citations(
                unique_nums=[1],
                candidates={1: pool},
                num_context={1: "memory consolidation during sleep"},
                num_keywords={1: "memory consolidation"},
                llm_call_fn=fake_llm,
            )

        result = asyncio.run(go())
        self.assertNotIn(1, result.failed)
        self.assertIn(1, result.num_to_ref)
        self.assertEqual(len(result.ref_list), 1)


if __name__ == "__main__":
    unittest.main()
