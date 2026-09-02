# -*- coding: utf-8 -*-
"""grounded_citation 通用溯源层的单测。

注入 Fake search + Fake LLM，验证：
1. ground_claim：单条主张命中高置信来源 → verdict=supported。
2. 低于阈值 → verdict=unsupported，best 仍保留（供报告展示）。
3. 检索失败 / 无候选 → error 填充，不抛异常。
4. ground_claims：多条批量，跳过空串。
5. format_grounded_report：Markdown 报告含计数与最佳来源。
6. 工具 schema 含 required=["claims"]，handler 空 claims 返 success=False。
"""
import asyncio
import unittest
from dataclasses import dataclass, field


@dataclass
class _Paper:
    title: str
    authors: list = field(default_factory=list)
    year: str = ""
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    source: str = "openalex"


def _p(title, **kw):
    return _Paper(title=title, **kw)


class TestGroundClaim(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_supported_when_high_score(self):
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        pool = [_p("Attention Is All You Need", authors=["Vaswani"], year="2017",
                   venue="NeurIPS", abstract="transformer attention mechanism")]

        async def search_fn(keyword, limit=8):
            for p in pool:
                yield p

        async def llm(prompt, **kw):
            if "提取" in prompt or "关键短语" in prompt:
                return "attention transformer"
            if "打分" in prompt:
                return "1: 0.95"
            return "ok"

        r = self._run(ground_claim(
            "Transformer 的自注意力机制由 Vaswani 等人提出",
            search_fn=search_fn, llm_call_fn=llm,
        ))
        self.assertEqual(r["verdict"], "supported")
        self.assertIsNotNone(r["best"])
        self.assertIn("Attention Is All You Need", r["best"]["title"])

    def test_unsupported_below_threshold(self):
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        pool = [_p("Unrelated Topic Paper", authors=["X"], year="2020")]

        async def search_fn(keyword, limit=8):
            for p in pool:
                yield p

        async def llm(prompt, **kw):
            if "打分" in prompt:
                return "1: 0.05"
            return "kw"

        r = self._run(ground_claim("一条与候选无关的主张", search_fn=search_fn,
                                   llm_call_fn=llm))
        self.assertEqual(r["verdict"], "unsupported")
        self.assertIsNotNone(r["best"])  # 仍保留最佳候选供报告展示

    def test_search_failure_fills_error(self):
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        async def search_fn(keyword, limit=8):
            raise RuntimeError("boom")

        r = self._run(ground_claim("任意主张", search_fn=search_fn, llm_call_fn=None))
        self.assertIn("检索失败", r["error"])

    def test_no_candidates_fills_error(self):
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        async def search_fn(keyword, limit=8):
            return []

        r = self._run(ground_claim("任意主张", search_fn=search_fn, llm_call_fn=None))
        self.assertEqual(r["error"], "无候选来源")

    def test_llm_none_falls_back_to_coarse(self):
        """llm_call_fn=None 时 llm_rerank fail-open 走粗排，不崩溃。"""
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        pool = [_p("Transformer Model", authors=["V"], year="2017",
                   abstract="attention transformer")]
        async def search_fn(keyword, limit=8):
            for p in pool:
                yield p

        r = self._run(ground_claim("transformer attention", search_fn=search_fn,
                                   llm_call_fn=None))
        # 无 LLM 精排 → 粗排结果仍产出，best 非空
        self.assertIsNotNone(r["best"])

    def test_single_candidate_not_forced_to_1_0(self):
        """单候选修复回归：检索只返回一篇无关文献时，不得被判 1.0（supported）。

        守护本实现引入的语义校正——citation_matcher.llm_rerank 对单候选硬编码
        返回 1.0（为「引用编号→唯一论文」设计），开放式溯源须改用真实
        score_relevance 打分，否则假阳性。
        """
        from vermes_cli.scholarforge.grounded_citation import ground_claim

        # 主张谈「深度学习」，候选却是一篇完全无关的「古代陶器考古」论文
        pool = [_p("Ancient Pottery Archaeology in Bronze Age",
                   authors=["X"], year="2010", venue="Archaeology",
                   abstract="ceramic fragments and excavation sites")]

        async def search_fn(keyword, limit=8):
            for p in pool:
                yield p

        # 无 LLM：直接走单候选 score_relevance 路径（非 llm_rerank 的 1.0 捷径）
        r = self._run(ground_claim(
            "深度学习模型的训练需要大量计算资源",
            search_fn=search_fn, llm_call_fn=None,
        ))
        self.assertEqual(r["verdict"], "unsupported")
        # 关键断言：分数必须远低于 1.0（若退回 llm_rerank 捷径会 = 1.0）
        self.assertLess(r["best"]["score"], 0.5)


class TestGroundClaimsAndReport(unittest.TestCase):
    def test_batch_skips_empty_and_reports(self):
        from vermes_cli.scholarforge.grounded_citation import (
            ground_claims, format_grounded_report,
        )

        pool = [_p("Real Paper", authors=["A"], year="2021")]

        async def search_fn(keyword, limit=8):
            for p in pool:
                yield p

        async def llm(prompt, **kw):
            if "打分" in prompt:
                return "1: 0.8"
            return "kw"

        results = asyncio.run(ground_claims(
            ["第一条主张", "  ", "第二条主张"],
            search_fn=search_fn, llm_call_fn=llm,
        ))
        self.assertEqual(len(results), 2)  # 空串被跳过

        report = format_grounded_report(results)
        self.assertIn("共 **2** 条主张", report)
        self.assertIn("Real Paper", report)


class TestToolSchema(unittest.TestCase):
    def test_schema_requires_claims(self):
        from tools.grounded_citation_tool import GROUNDED_CITATION_SCHEMA
        self.assertEqual(GROUNDED_CITATION_SCHEMA["name"], "grounded_citation")
        self.assertIn("claims", GROUNDED_CITATION_SCHEMA["parameters"]["required"])

    def test_handler_empty_claims(self):
        from tools.grounded_citation_tool import _handle_grounded_citation
        import json
        out = asyncio.run(_handle_grounded_citation({"claims": []}))
        self.assertFalse(json.loads(out)["success"])


if __name__ == "__main__":
    unittest.main()
