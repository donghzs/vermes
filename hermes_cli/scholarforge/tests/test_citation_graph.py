"""scholarforge_citation_graph 工具 — 无网单测（注入 FakeProvider + 临时 DB 缓存）。

验证：
1. S2 paper key 规范化（裸 DOI / DOI: 前缀）。
2. S2 原始节点 → 统一字段归一化。
3. build_citation_graph 聚合 counts / 节点去重 / 三类边透传。
4. kinds 子集过滤（只拉 citations）。
5. 本地缓存命中后不再打网络（避开 S2 限流）。
6. provider 失败 → 优雅报错（success=False）。
7. 空 paper_id → 直接报错，不触网。
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from hermes_cli.scholarforge import database as db
from hermes_cli.scholarforge.citation_graph import build_citation_graph
from agent.literature_providers.semanticscholar import SemanticScholarProvider


NODE_A = {
    "title": "Paper A", "authors": [{"name": "Xu Ming"}], "year": 2020,
    "venue": "NeurIPS", "citationCount": 10, "url": "http://x/a",
    "paperId": "aaa", "abstract": "abstract of A",
    "externalIds": {"DOI": "10.1/aaa"},
}
NODE_B = {
    "title": "Paper B", "authors": [{"name": "Li Na"}], "year": 2021,
    "venue": "ICML", "citationCount": 5, "url": "http://x/b",
    "paperId": "bbb", "abstract": "abstract of B",
    "externalIds": {"DOI": "10.1/bbb"},
}
NODE_C = {
    "title": "Paper C", "authors": [{"name": "Wang Lei"}], "year": 2019,
    "venue": "ACL", "citationCount": 3, "url": "http://x/c",
    "paperId": "ccc", "abstract": "abstract of C",
    "externalIds": {"DOI": "10.1/ccc"},
}

# 归一化后的节点（模拟真实 SemanticScholarProvider.citation_graph 的产出契约：
# authors 为字符串列表、含 doi/paperId），供 FakeProvider 与 handler 测试复用。
NORM_A = SemanticScholarProvider._normalize_s2_paper(NODE_A)
NORM_B = SemanticScholarProvider._normalize_s2_paper(NODE_B)
NORM_C = SemanticScholarProvider._normalize_s2_paper(NODE_C)


class FakeS2Provider:
    """可注入的假 S2 provider，记录调用次数与参数。"""

    DEFAULT_GRAPH = {
        "citations": [NORM_A, NORM_B],
        "references": [NORM_A, NORM_C],  # A 重复 → 去重
        "recommendations": [NORM_B],
    }

    def __init__(self, graph=None, fail=False):
        self._calls = []
        self._graph = graph if graph is not None else dict(self.DEFAULT_GRAPH)
        self._fail = fail

    def _s2_paper_key(self, paper_id):
        return paper_id.strip()

    def citation_graph(self, paper_id, limit=50,
                       kinds=("citations", "references", "recommendations")):
        self._calls.append((paper_id, kinds, limit))
        if self._fail:
            return {"success": False, "error": "429 rate limited", "errors": {"citations": "rate"}}
        data = {"citations": [], "references": [], "recommendations": []}
        for k in kinds:
            data[k] = self._graph.get(k, [])
        return {"success": True, "data": data, "errors": {}}


class TestS2KeyNormalization(unittest.TestCase):
    def test_bare_doi_gets_prefix(self):
        self.assertEqual(SemanticScholarProvider._s2_paper_key("10.1145/3292500.3330701"),
                         "DOI:10.1145/3292500.3330701")

    def test_doi_prefix_preserved(self):
        self.assertEqual(SemanticScholarProvider._s2_paper_key("DOI:10.1/2"), "DOI:10.1/2")

    def test_paperid_preserved(self):
        self.assertEqual(
            SemanticScholarProvider._s2_paper_key("649def34f418204c8467b9f968239371136d241e"),
            "649def34f418204c8467b9f968239371136d241e",
        )


class TestNormalizeNode(unittest.TestCase):
    def test_normalize_shape(self):
        n = SemanticScholarProvider._normalize_s2_paper(NODE_A)
        self.assertEqual(n["title"], "Paper A")
        self.assertEqual(n["authors"], ["Xu Ming"])
        self.assertEqual(n["citationCount"], 10)
        self.assertEqual(n["doi"], "10.1/aaa")
        self.assertEqual(n["paperId"], "aaa")


class TestBuildCitationGraph(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sf_cg_test_")
        self._patcher = patch.object(db, "DB_PATH", os.path.join(self._tmp, "sf.db"))
        self._patcher.start()
        db.init_db()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _provider(self, **kw):
        return FakeS2Provider(graph={
            "citations": [NORM_A, NORM_B],
            "references": [NORM_A, NORM_C],  # A 重复 → 去重
            "recommendations": [NORM_B],
        }, **kw)

    def test_aggregates_counts_and_dedup(self):
        res = build_citation_graph("10.1/test", provider=self._provider())
        self.assertTrue(res["success"])
        self.assertEqual(res["counts"]["citations"], 2)
        self.assertEqual(res["counts"]["references"], 2)
        self.assertEqual(res["counts"]["recommendations"], 1)
        # A 同时出现在 citations/references，应去重为 1 个节点
        self.assertEqual(res["node_count"], 3)

    def test_kinds_filter_only_citations(self):
        prov = self._provider()
        res = build_citation_graph("10.1/test", kinds=["citations"], provider=prov)
        self.assertTrue(res["success"])
        self.assertEqual(res["counts"]["citations"], 2)
        self.assertEqual(res["counts"]["references"], 0)
        self.assertEqual(res["counts"]["recommendations"], 0)
        # 只拉了 citations 一种关系
        self.assertEqual(prov._calls[0][1], ("citations",))

    def test_cache_hit_avoids_network(self):
        prov = self._provider()
        first = build_citation_graph("10.1/cached", provider=prov)
        self.assertTrue(first["success"])
        self.assertFalse(first["cache_hit"])
        # 第二次：同 key 应命中缓存，provider 不再被调用
        second = build_citation_graph("10.1/cached", provider=prov)
        self.assertTrue(second["success"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(prov._calls), 1)

    def test_cache_disabled_still_works(self):
        prov = self._provider()
        build_citation_graph("10.1/nocache", use_cache=False, provider=prov)
        build_citation_graph("10.1/nocache", use_cache=False, provider=prov)
        # 禁用缓存 → 每次都打网络
        self.assertEqual(len(prov._calls), 2)

    def test_provider_failure_surfaced(self):
        res = build_citation_graph("10.1/fail", provider=self._provider(fail=True))
        self.assertFalse(res["success"])
        self.assertIn("rate limited", res["error"])

    def test_empty_paper_id_no_network(self):
        prov = self._provider()
        res = build_citation_graph("", provider=prov)
        self.assertFalse(res["success"])
        self.assertEqual(prov._calls, [])  # 未触网


def _run_loop(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestHandlerFormatting(unittest.TestCase):
    """端到端：handler 经注入的 FakeProvider 产出 markdown。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sf_cg_h_")
        self._patcher = patch.object(db, "DB_PATH", os.path.join(self._tmp, "sf.db"))
        self._patcher.start()
        db.init_db()
        self._prov_patch = patch(
            "hermes_cli.scholarforge.citation_graph.SemanticScholarProvider",
            FakeS2Provider,
        )
        self._prov_patch.start()

    def tearDown(self):
        self._prov_patch.stop()
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_handler_renders_markdown(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_citation_graph

        out = _run_loop(_handle_scholarforge_citation_graph({"paper_id": "10.1/test"}))
        self.assertIn("论文引用图谱", out)
        self.assertIn("被以下论文引用 (citations)", out)
        self.assertIn("引用了以下论文 (references)", out)
        self.assertIn("Semantic Scholar 推荐 (recommendations)", out)
        self.assertIn("Paper A", out)
        self.assertIn("Paper B", out)
        self.assertIn("Paper C", out)

    def test_handler_empty_id(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_citation_graph

        out = _run_loop(_handle_scholarforge_citation_graph({"paper_id": ""}))
        self.assertIn("❌", out)


if __name__ == "__main__":
    unittest.main()
