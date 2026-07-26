"""Zotero CSL JSON 导出单测。

验证：
1. parse_references_csl 正确解析作者(含多作者/逗号分隔)/年份/标题/期刊/DOI。
2. format_export_csl_json 生成合法 CSL JSON（id/type/title/author family-given/
   issued date-parts/DOI/URL）。
3. 导出 handler 的 zotero 分支：从参考文献区解析并写出合法 CSL JSON 文件。
"""
import asyncio
import json
import os
import re
import tempfile
import unittest

from hermes_cli.scholarforge.export import (
    parse_references_csl,
    format_export_csl_json,
)
from hermes_cli.scholarforge.export.full import export_csl_json


SAMPLE_REFS = """## 参考文献

[1] Smith, J., Lee, K. (2020). Deep Learning for NLP. Nature. DOI:10.1038/nature123.
[2] 王磊, 李娜 (2021). 大语言模型综述. 计算机学报. https://doi.org/10.1/ccc
"""


class TestParseReferences(unittest.TestCase):
    def test_parses_multi_author_year_title_venue_doi(self):
        papers = parse_references_csl(SAMPLE_REFS)
        self.assertEqual(len(papers), 2)
        p0 = papers[0]
        self.assertEqual(p0["year"], "2020")
        self.assertEqual(p0["title"], "Deep Learning for NLP")
        self.assertEqual(p0["venue"], "Nature")
        self.assertEqual(p0["doi"], "10.1038/nature123")
        # 两位作者
        self.assertEqual(len(p0["authors"]), 2)
        self.assertEqual(p0["authors"][0], {"family": "Smith", "given": "J."})
        self.assertEqual(p0["authors"][1], {"family": "Lee", "given": "K."})

    def test_parses_chinese_authors_and_doi_url(self):
        papers = parse_references_csl(SAMPLE_REFS)
        p1 = papers[1]
        self.assertEqual(p1["year"], "2021")
        self.assertEqual(p1["title"], "大语言模型综述")
        self.assertEqual(p1["venue"], "计算机学报")
        self.assertEqual(p1["doi"], "10.1/ccc")
        # 中文姓名：逗号分隔 → family 为最后一段
        self.assertEqual(p1["authors"][0], {"family": "王磊", "given": ""})


class TestFormatCSLJSON(unittest.TestCase):
    def test_valid_csl_json(self):
        papers = parse_references_csl(SAMPLE_REFS)
        text = format_export_csl_json(papers)
        data = json.loads(text)  # 必须合法 JSON
        self.assertEqual(len(data), 2)
        item = data[0]
        self.assertEqual(item["id"], 1)
        self.assertEqual(item["type"], "article-journal")
        self.assertEqual(item["title"], "Deep Learning for NLP")
        self.assertEqual(item["author"][0]["family"], "Smith")
        self.assertEqual(item["issued"], {"date-parts": [[2020]]})
        self.assertEqual(item["DOI"], "10.1038/nature123")
        self.assertEqual(item["URL"], "https://doi.org/10.1038/nature123")

    def test_handles_string_authors_list(self):
        papers = [{"title": "T", "authors": ["Smith, J.", "Lee, K."], "year": "2020",
                   "venue": "V", "doi": "10.1/x"}]
        data = json.loads(format_export_csl_json(papers))
        self.assertEqual(data[0]["author"][1], {"family": "Lee", "given": "K."})


class TestExportHandlerZotero(unittest.TestCase):
    def test_handler_zotero_branch_writes_file(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_export

        content = "# 标题\n正文。\n" + SAMPLE_REFS
        out = asyncio.run(_handle_scholarforge_export({
            "title": "测试论文", "content": content, "format": "zotero",
        }))
        self.assertIn("Zotero CSL JSON 已导出", out)
        # 从返回串抽取路径并校验文件为合法 CSL JSON
        m = re.search(r"已导出：(.+?)\n", out)
        self.assertIsNotNone(m)
        path = m.group(1).strip()
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertGreaterEqual(len(data), 1)
        self.assertEqual(data[0]["type"], "article-journal")


if __name__ == "__main__":
    unittest.main()
