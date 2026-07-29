"""摘要回填单测（注入 FakeProvider + 临时 DB）。

验证：
1. fetch_abstract_by_doi 成功返回 abstract（复用 S2 provider.get_paper）。
2. S2 节点无 abstract → 优雅返回「无可用摘要」。
3. 空 DOI → 直接报错。
4. backfill_project_abstracts 仅回填「有 DOI 且 abstract 空」的文献，并更新 DB。
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from vermes_cli.scholarforge import database as db
from vermes_cli.scholarforge.abstract_backfill import (
    fetch_abstract_by_doi,
    backfill_project_abstracts,
)
from agent.literature_providers.semanticscholar import SemanticScholarProvider


class FakeS2Provider:
    """返回带 abstract 的归一化节点。"""

    def _s2_paper_key(self, paper_id):
        return paper_id.strip()

    def get_paper(self, paper_id):
        if paper_id.strip() == "10.1/hasabs":
            node = SemanticScholarProvider._normalize_s2_paper({
                "title": "Has Abstract", "authors": [{"name": "A"}], "year": 2020,
                "venue": "V", "citationCount": 1, "url": "u", "paperId": "p1",
                "externalIds": {"DOI": "10.1/hasabs"},
                "abstract": "这是一篇关于深度学习的论文摘要。",
            })
            return {"success": True, "paper": node}
        if paper_id.strip() == "10.1/noabs":
            node = SemanticScholarProvider._normalize_s2_paper({
                "title": "No Abstract", "authors": [{"name": "B"}], "year": 2021,
                "venue": "V", "citationCount": 1, "url": "u", "paperId": "p2",
                "externalIds": {"DOI": "10.1/noabs"},
                "abstract": "",
            })
            return {"success": True, "paper": node}
        return {"success": False, "error": "404 not found"}


class TestFetchAbstract(unittest.TestCase):
    def test_success(self):
        res = fetch_abstract_by_doi("10.1/hasabs", provider=FakeS2Provider())
        self.assertTrue(res["success"])
        self.assertIn("深度学习", res["abstract"])
        self.assertEqual(res["doi"], "10.1/hasabs")

    def test_no_abstract(self):
        res = fetch_abstract_by_doi("10.1/noabs", provider=FakeS2Provider())
        self.assertFalse(res["success"])
        self.assertIn("无可用摘要", res["error"])

    def test_empty_doi(self):
        res = fetch_abstract_by_doi("", provider=FakeS2Provider())
        self.assertFalse(res["success"])


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sf_ab_")
        self._patcher = patch.object(db, "DB_PATH", os.path.join(self._tmp, "sf.db"))
        self._patcher.start()
        db.init_db()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_project_with_lits(self):
        proj = db.create_project("测试项目")
        pid = proj["id"]
        # 有 DOI 且 abstract 空 → 应被回填
        db.get_conn  # noop to ensure import
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO literatures (project_id, title, doi, abstract, added_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "有DOI缺摘要", "10.1/hasabs", "", int(__import__("time").time())),
            )
            # 有 DOI 且已有 abstract → 跳过
            conn.execute(
                "INSERT INTO literatures (project_id, title, doi, abstract, added_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "已有摘要", "10.1/other", "已有内容", int(__import__("time").time())),
            )
            # 无 DOI → 跳过
            conn.execute(
                "INSERT INTO literatures (project_id, title, doi, abstract, added_at) "
                "VALUES (?,?,?,?,?)",
                (pid, "无DOI", "", "", int(__import__("time").time())),
            )
        return pid

    def test_backfill_updates_only_empty(self):
        pid = self._make_project_with_lits()
        result = backfill_project_abstracts(pid, provider=FakeS2Provider())
        self.assertEqual(result["checked"], 1)   # 仅「有DOI缺摘要」那行
        self.assertEqual(result["updated"], 1)
        # 验证 DB 已写入摘要
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT abstract FROM literatures WHERE doi=?", ("10.1/hasabs",)
            ).fetchone()
        self.assertIn("深度学习", row["abstract"])


if __name__ == "__main__":
    unittest.main()
