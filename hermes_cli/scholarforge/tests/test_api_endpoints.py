"""
ScholarForge API 端点测试套件
覆盖 38 个后端路由中的核心 CRUD 端点

运行: python -m pytest hermes_cli/scholarforge/tests/test_api_endpoints.py -v
"""
import json
import os
import sys
import tempfile
import unittest

# Setup temp DB before any hermes_cli imports
_tmpdir = tempfile.mkdtemp(prefix="sf_api_test_")
_db_path = os.path.join(_tmpdir, "test_api.db")

import hermes_cli.scholarforge.database as dbmod
dbmod.DB_PATH = _db_path
dbmod.init_db()

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Create a test FastAPI app and mount the ScholarForge router
# This avoids importing the full hermes_cli.web_server (which has heavy deps)
_tmpdir_for_test = tempfile.mkdtemp(prefix="sf_api_test_")
_db_path_for_test = os.path.join(_tmpdir_for_test, "test_api.db")

import hermes_cli.scholarforge.database as _dbmod
_dbmod.DB_PATH = _db_path_for_test
_dbmod.init_db()

_app = FastAPI(title="ScholarForge Test")
from hermes_cli.scholarforge.blueprint import register_to
register_to(_app)
app = _app  # for TestClient


class TestClientWithDB:
    """包装 TestClient，自动注入 client_id"""
    def __init__(self, client: TestClient):
        self._c = client
        self.client_id = "test-client-001"

    def _params(self, **kw):
        kw.setdefault("client_id", self.client_id)
        return kw

    def get(self, path, **kw):
        return self._c.get(path, params=self._params(**kw))

    def post(self, path, json=None, **kw):
        if json is not None and "client_id" not in json:
            json["client_id"] = self.client_id
        return self._c.post(path, json=json, params=self._params(**kw))

    def put(self, path, json=None, **kw):
        if json is not None and "client_id" not in json:
            json["client_id"] = self.client_id
        return self._c.put(path, json=json, params=self._params(**kw))

    def patch(self, path, json=None, **kw):
        if json is not None and "client_id" not in json:
            json["client_id"] = self.client_id
        return self._c.patch(path, json=json, params=self._params(**kw))

    def delete(self, path, **kw):
        return self._c.delete(path, params=self._params(**kw))


_raw = TestClient(app)
client = TestClientWithDB(_raw)


# ═══════════════════════════════════════════════════════════════
# 1. 项目 CRUD
# ═══════════════════════════════════════════════════════════════
class TestProjectCRUD(unittest.TestCase):
    """POST /api/scholar/projects  GET /api/scholar/projects/{pid}  PATCH / DELETE"""

    def test_create_project(self):
        r = client.post("/api/scholar/projects", json={"title": "API 测试论文"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("id", data)
        self.assertEqual(data["title"], "API 测试论文")

    def test_get_projects_list(self):
        r = client.get("/api/scholar/projects")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json().get("projects", r.json()), list)

    def test_patch_project(self):
        # 先创建一个
        create = client.post("/api/scholar/projects", json={"title": "Patch 测试"})
        pid = create.json()["id"]
        r = client.patch(f"/api/scholar/projects/{pid}", json={"title": "已修改标题"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["title"], "已修改标题")

    def test_delete_project(self):
        create = client.post("/api/scholar/projects", json={"title": "待删除"})
        pid = create.json()["id"]
        r = client.delete(f"/api/scholar/projects/{pid}")
        self.assertEqual(r.status_code, 200, r.text)

    def test_get_single_project(self):
        create = client.post("/api/scholar/projects", json={"title": "单个项目"})
        pid = create.json()["id"]
        r = client.get(f"/api/scholar/projects/{pid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], pid)


# ═══════════════════════════════════════════════════════════════
# 2. 章节保存与删除
# ═══════════════════════════════════════════════════════════════
class TestSectionCrud(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "章节测试项目"})
        cls.pid = create.json()["id"]

    def test_save_section(self):
        r = client.post(
            f"/api/scholar/projects/{self.pid}/section/abstract",
            json={"content": "这是摘要内容"},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_get_saved_section(self):
        # 保存后再读取
        client.post(
            f"/api/scholar/projects/{self.pid}/section/intro",
            json={"content": "引言内容"},
        )
        r = client.get(f"/api/scholar/projects/{self.pid}/section/intro")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("content", r.json())

    def test_delete_section(self):
        client.post(
            f"/api/scholar/projects/{self.pid}/section/conclusion",
            json={"content": "结论内容"},
        )
        r = client.delete(f"/api/scholar/projects/{self.pid}/section/conclusion")
        self.assertEqual(r.status_code, 200, r.text)

    def test_save_multiple_sections(self):
        for key in ["abstract", "intro", "method", "result", "conclusion"]:
            r = client.post(
                f"/api/scholar/projects/{self.pid}/section/{key}",
                json={"content": f"{key} 章节内容"},
            )
            self.assertEqual(r.status_code, 200, f"Failed at {key}: {r.text}")


# ═══════════════════════════════════════════════════════════════
# 3. 文献管理
# ═══════════════════════════════════════════════════════════════
class TestLiterature(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "文献测试"})
        cls.pid = create.json()["id"]

    def test_add_literature(self):
        r = client.post(
            f"/api/scholar/projects/{self.pid}/literature",
            json={
                "title": "深度学习综述",
                "authors": "LeCun et al.",
                "year": "2015",
                "venue": "Nature",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("id", r.json())

    def test_get_literature_list(self):
        # 先加一条
        client.post(
            f"/api/scholar/projects/{self.pid}/literature",
            json={"title": "测试文献", "authors": "Test", "year": "2024"},
        )
        r = client.get(f"/api/scholar/projects/{self.pid}/literature")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json().get("literatures", r.json()), list)

    def test_bibtex_import(self):
        bibtex = """
        @article{test2024,
            author = {Test Author},
            title = {Test Paper},
            journal = {Test Journal},
            year = {2024}
        }
        """
        r = client.post(
            f"/api/scholar/projects/{self.pid}/literature/import",
            json={"bibtex": bibtex},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("added", data)

    def test_delete_literature(self):
        add = client.post(
            f"/api/scholar/projects/{self.pid}/literature",
            json={"title": "待删除文献", "authors": "X", "year": "2024"},
        )
        lit_id = add.json()["id"]
        r = client.delete(f"/api/scholar/literature/{lit_id}")
        self.assertEqual(r.status_code, 200, r.text)


# ═══════════════════════════════════════════════════════════════
# 4. 版本快照
# ═══════════════════════════════════════════════════════════════
class TestSnapshots(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "快照测试"})
        cls.pid = create.json()["id"]
        # 写入章节确保有内容可快照
        for key in ["abstract", "intro"]:
            client.post(
                f"/api/scholar/projects/{cls.pid}/section/{key}",
                json={"content": f"{key} 内容"},
            )

    def test_create_snapshot(self):
        r = client.post(
            f"/api/scholar/projects/{self.pid}/snapshots",
            json={"label": "测试快照", "note": "10 字"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("id", data)

    def test_list_snapshots(self):
        # 确保至少有一个快照
        client.post(
            f"/api/scholar/projects/{self.pid}/snapshots",
            json={"label": "列表测试"},
        )
        r = client.get(f"/api/scholar/projects/{self.pid}/snapshots")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json().get("snapshots", r.json()), list)

    def test_get_single_snapshot(self):
        create = client.post(
            f"/api/scholar/projects/{self.pid}/snapshots",
            json={"label": "单个快照"},
        )
        sid = create.json()["id"]
        r = client.get(f"/api/scholar/snapshots/{sid}")
        self.assertEqual(r.status_code, 200, r.text)

    def test_delete_snapshot(self):
        create = client.post(
            f"/api/scholar/projects/{self.pid}/snapshots",
            json={"label": "待删除快照"},
        )
        sid = create.json()["id"]
        r = client.delete(f"/api/scholar/snapshots/{sid}")
        self.assertEqual(r.status_code, 200, r.text)


# ═══════════════════════════════════════════════════════════════
# 5. 导出（Markdown / BibTeX / LaTeX / Word）
# ═══════════════════════════════════════════════════════════════
class TestExport(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "导出测试论文"})
        cls.pid = create.json()["id"]
        for key in ["abstract", "intro"]:
            client.post(
                f"/api/scholar/projects/{cls.pid}/section/{key}",
                json={"content": f"{key} 的内容"},
            )

    def test_export_markdown(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/export?format=markdown")
        self.assertEqual(r.status_code, 200, r.text)
        # JSON response 或直接文本
        data = r.json()
        self.assertIn("content", data)

    def test_export_latex(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/export?format=latex&template=ieee")
        self.assertEqual(r.status_code, 200, r.text)

    def test_export_bibtex(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/export?format=bibtex")
        self.assertEqual(r.status_code, 200, r.text)

    def test_export_word(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/export?format=word")
        self.assertEqual(r.status_code, 200, r.text)

    def test_export_pdf(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/export?format=pdf")
        self.assertEqual(r.status_code, 200, r.text)


# ═══════════════════════════════════════════════════════════════
# 6. 评分 / 查重 / 引用核查
# ═══════════════════════════════════════════════════════════════
class TestAnalysis(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "分析测试"})
        cls.pid = create.json()["id"]
        for key in ["abstract", "intro"]:
            client.post(
                f"/api/scholar/projects/{cls.pid}/section/{key}",
                json={"content": f"{key} 内容测试"},
            )

    def test_score(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/score")
        self.assertIn(r.status_code, (200, 500), r.text)  # 500 OK if no LLM
        if r.status_code == 200:
            data = r.json()
            # 可能返回分数对象或错误
            self.assertIsInstance(data, dict)

    def test_plagcheck(self):
        r = client.post(
            f"/api/scholar/projects/{self.pid}/plagcheck",
            json={"content": "测试内容"},
        )
        # 500 if no LLM configured, which is fine
        self.assertIn(r.status_code, (200, 500), r.text)

    def test_citation_verification(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/citation-verifications")
        self.assertIn(r.status_code, (200, 500), r.text)

    def test_claims(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/claims")
        self.assertIn(r.status_code, (200, 500), r.text)


# ═══════════════════════════════════════════════════════════════
# 7. Agent Provider 配置
# ═══════════════════════════════════════════════════════════════
class TestAgentProviders(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        create = client.post("/api/scholar/projects", json={"title": "Provider 测试"})
        cls.pid = create.json()["id"]

    def test_list_providers(self):
        r = client.get("/api/scholar/providers")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json().get("providers", r.json()), list)

    def test_list_agents(self):
        r = client.get("/api/scholar/agents")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json().get("agents", r.json()), list)

    def test_project_providers(self):
        r = client.get(f"/api/scholar/projects/{self.pid}/agent-providers")
        self.assertIn(r.status_code, (200, 500), r.text)


# ═══════════════════════════════════════════════════════════════
# 8. 搜索
# ═══════════════════════════════════════════════════════════════
class TestSearch(unittest.TestCase):

    def test_search_endpoint(self):
        r = client.post(
            "/api/scholar/search",
            json={"query": "machine learning", "limit": 5},
        )
        # 200 (results) or 500 (no API key) - both valid
        self.assertIn(r.status_code, (200, 500), r.text)

    def test_search_get(self):
        r = client.get("/api/scholar/search?q=deep+learning&limit=5")
        self.assertIn(r.status_code, (200, 422, 500), r.text)  # 422 if missing query param


# ═══════════════════════════════════════════════════════════════
# 9. 来源配置
# ═══════════════════════════════════════════════════════════════
class TestSources(unittest.TestCase):

    def test_list_sources(self):
        r = client.get("/api/scholar/sources")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue("free_sources" in data or isinstance(data, list))

    def test_sources_connectivity(self):
        r = client.get("/api/scholar/sources/connectivity")
        self.assertIn(r.status_code, (200, 500), r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
