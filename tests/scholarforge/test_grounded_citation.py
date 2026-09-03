# -*- coding: utf-8 -*-
"""grounded_citation 通用溯源层的单测。

注入 Fake search + Fake LLM，验证：
1. ground_claim：单条主张命中高置信来源 → verdict=supported。
2. 低于阈值 → verdict=unsupported，best 仍保留（供报告展示）。
3. 检索失败 / 无候选 → error 填充，不抛异常。
4. ground_claims：多条批量，跳过空串。
5. format_grounded_report：Markdown 报告含计数与最佳来源。
6. 工具 schema 含 required=["claims"]，handler 空 claims 返 success=False。
7. 注② smoke：生产 `_call_llm` 注入路径（tools/grounded_citation_tool.py:77）
   接线 + 签名校验（spy 替换真实 _call_llm，不触网）。

可独立运行：python tests/scholarforge/test_grounded_citation.py
"""
import os
import sys

# 让 __main__ 直跑也能 import vermes_cli / tools（pytest 已自动加 rootdir，
# 此处为双入口一致补齐；从 __file__ 上溯 3 级即项目根，避免硬编码绝对路径）。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import asyncio
import json
import unittest
from dataclasses import dataclass, field
from unittest.mock import patch


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


class TestToolProductionLLMInjection(unittest.TestCase):
    """注② 收尾 smoke：生产 `_call_llm` 注入路径（tools/grounded_citation_tool.py:77）。

    现有测试只覆盖「注入 fake llm_call_fn 的 ground_claim/ground_claims」
    以及 handler 空 claims 早返回——**从未触达** tools:77 对真实 ``_call_llm``
    的导入与包裹。本类用 spy 替换真实 ``_call_llm``（不触网）验证：

    1. 真实 ``_call_llm`` 经 handler 的 ``_llm`` 包装器被实际调用（注入路径触达）；
    2. 包装器以默认 ``temperature=0.2`` 透传；
    3. 包装器以 ``model=ANALYSIS_MODEL`` 透传（注④ 记录的真实耦合常量）；
    4. ground_claims 全链路跑通（search 已 stub）并返 success 报告。

    用 ``unittest.mock.patch``（非 pytest fixture）以兼容 pytest 与 ``__main__`` 双入口。
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_handler_injects_real_call_llm(self):
        from vermes_cli.scholarforge.tools import ANALYSIS_MODEL

        calls = []

        async def spy_call_llm(prompt, temperature=0.2, model=None):
            calls.append({"prompt": prompt, "temperature": temperature, "model": model})
            # 关键词提取 prompt 含「提取/关键短语」→ 返回检索词；
            # 其余（精排打分）按既有测试约定返回 "idx: score"。
            if "提取" in prompt or "关键短语" in prompt:
                return "alphafold protein folding"
            return "1: 0.9"

        async def fake_search(keyword, limit=8):
            yield _Paper(
                title="AlphaFold2 and protein structure prediction",
                authors=["Jumper"], year="2021", venue="Nature",
                abstract="protein folding structure prediction", source="openalex",
            )
            yield _Paper(
                title="Protein structure by deep learning",
                authors=["Senior"], year="2020", venue="Nature",
                abstract="deep learning protein", source="openalex",
            )

        from vermes_cli.scholarforge import search as search_mod
        from vermes_cli.scholarforge import tools as tools_mod

        # 替换真实 _call_llm（不触网）+ 替换默认 search_papers（不触网）
        with patch.object(tools_mod, "_call_llm", spy_call_llm), \
             patch.object(search_mod, "search_papers", fake_search):
            from tools.grounded_citation_tool import _handle_grounded_citation
            out = self._run(_handle_grounded_citation({
                "claims": ["AlphaFold 在 2020 年 CASP14 中夺冠"],
            }))

        data = json.loads(out)
        self.assertTrue(data["success"], msg=f"handler 应成功：{out}")
        # ① 真实 _call_llm 至少被调用一次 → 证明注入路径真的触达（注② 核心）
        self.assertGreaterEqual(
            len(calls), 1,
            "真实 _call_llm 应至少被调用一次（注② 注入路径须触达）",
        )
        # ② 包装器默认 temperature 透传
        self.assertTrue(
            all(c["temperature"] == 0.2 for c in calls),
            "handler 的 _llm 必须以默认 temperature=0.2 透传",
        )
        # ③ 包装器 model 透传为 ANALYSIS_MODEL（注④ 记录的真实耦合常量）
        self.assertTrue(
            all(c["model"] == ANALYSIS_MODEL for c in calls),
            "model 必须透传为 ANALYSIS_MODEL（注④ 记录的真实耦合常量）",
        )
        # ④ 全链路跑通：报告 + 单条结果
        self.assertIn("report", data)
        self.assertEqual(len(data.get("results", [])), 1)


if __name__ == "__main__":
    unittest.main()
