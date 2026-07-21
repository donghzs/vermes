"""Step 3 回归测试：baidu_scholar 免费中文源注册 + 中文查询源路由。

验证：
1. baidu_scholar 已注册到搜索源表。
2. _select_default_sources 对中文/英文查询返回不同源集合
   （中文查询用 baidu_scholar 替代 crossref/openalex 并补 cnki；英文查询保持原链）。
3. _search_baidu_scholar_source 将 CnkiPaper 正确转换为 PaperResult。
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

import hermes_cli.scholarforge.search as sf_search
from hermes_cli.scholarforge.search import (
    _SEARCH_SOURCES,
    _select_default_sources,
    _search_baidu_scholar_source,
)


def _run_loop(coro):
    """用独立事件循环跑协程，避免与 pytest-asyncio 已绑定的线程循环冲突。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSearchSourceRegistration(unittest.TestCase):
    def test_baidu_scholar_registered(self):
        """Step 3：baidu_scholar 必须已注册为搜索源（之前是死模块）"""
        self.assertIn("baidu_scholar", _SEARCH_SOURCES)
        self.assertTrue(callable(_SEARCH_SOURCES["baidu_scholar"]))


class TestDefaultSourceSelection(unittest.TestCase):
    def test_chinese_query_uses_baidu_scholar_not_crossref_openalex(self):
        """中文查询：baidu_scholar 入选，crossref/openalex 被其替代"""
        sources = _select_default_sources("大语言模型幻觉检测")
        self.assertIn("baidu_scholar", sources)
        self.assertNotIn("crossref", sources)
        self.assertNotIn("openalex", sources)
        # cnki 作为付费网关源（无 key 时走 OpenAlex 中文兜底）应被补入
        self.assertIn("cnki", sources)

    def test_english_query_keeps_crossref_openalex_no_baidu(self):
        """英文查询：保持原默认链，不含 baidu_scholar"""
        sources = _select_default_sources("transformer attention mechanism")
        self.assertIn("crossref", sources)
        self.assertIn("openalex", sources)
        self.assertNotIn("baidu_scholar", sources)

    def test_english_query_includes_all_free_defaults(self):
        """英文查询默认链完整（7 个免费源）"""
        sources = _select_default_sources("deep learning")
        for s in ("arxiv", "crossref", "openalex", "doaj", "semantic_scholar", "pubmed", "core"):
            self.assertIn(s, sources)


class TestBaiduScholarWrapperConversion(unittest.TestCase):
    def test_converts_cnki_paper_to_paper_result(self):
        """_search_baidu_scholar_source 把 CnkiPaper 转成 PaperResult（字段对齐 cnki 包装）"""
        import hermes_cli.scholarforge.baidu_scholar_fetcher as bsf
        from hermes_cli.scholarforge.cnki_fetcher import CnkiPaper

        fake = CnkiPaper(
            title="面向教育场景的大模型评测框架",
            authors=["张三", "李四"],
            year="2024",
            journal="电化教育研究",
            abstract="摘要内容...",
            cited_count=12,
            url="https://doi.org/10.1234/test",
            doi="10.1234/test",
            source="baidu_scholar",
        )

        async def run():
            with patch.object(bsf, "search_baidu_scholar", new=AsyncMock(return_value=[fake])):
                return await _search_baidu_scholar_source("教育大模型", 5)

        results = _run_loop(run())
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.title, fake.title)
        self.assertEqual(r.authors, ["张三", "李四"])
        self.assertEqual(r.year, "2024")
        self.assertEqual(r.venue, "电化教育研究")
        self.assertEqual(r.abstract, "摘要内容...")
        self.assertEqual(r.citation_count, 12)
        self.assertEqual(r.doi, "10.1234/test")
        self.assertEqual(r.source, "baidu_scholar")
        self.assertTrue(r.paper_id.startswith("baidu_scholar:"))

    def test_empty_result_no_crash(self):
        import hermes_cli.scholarforge.baidu_scholar_fetcher as bsf

        async def run():
            with patch.object(bsf, "search_baidu_scholar", new=AsyncMock(return_value=[])):
                return await _search_baidu_scholar_source("无结果查询", 5)

        results = _run_loop(run())
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
