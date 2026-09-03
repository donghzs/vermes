"""⑮ 腿 C — DocMemoryProvider 单测。

覆盖：落盘/读取/列出/搜索 四工具 + on_pre_compress 自动落盘 +
on_session_end + backup_paths + on_session_switch + 共存（不消耗外部槽位）。
"""

import json
import os
import shutil
import tempfile
import unittest

from agent.docmemory_provider import DocMemoryProvider, _slugify, _front_matter, _parse_front_matter
from agent.memory_manager import MemoryManager, _FIRST_PARTY_PROVIDER_NAMES


class TestSlugify(unittest.TestCase):
    def test_ascii(self):
        self.assertEqual(_slugify("Hello World"), "Hello_World")

    def test_chinese(self):
        self.assertEqual(_slugify("深度学习入门"), "深度学习入门")

    def test_special_chars(self):
        self.assertEqual(_slugify("test/file?:1"), "test_file_1")

    def test_empty(self):
        self.assertEqual(_slugify(""), "untitled")


class TestFrontMatter(unittest.TestCase):
    def test_roundtrip(self):
        meta = {"title": "Test", "created": "2026-09-03"}
        fm = _front_matter(meta)
        parsed, body = _parse_front_matter(fm + "\nbody text")
        self.assertEqual(parsed["title"], "Test")
        self.assertEqual(parsed["created"], "2026-09-03")
        self.assertEqual(body.strip(), "body text")


class TestDocMemoryProvider(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.provider = DocMemoryProvider()
        self.provider.initialize(
            session_id="test-session-1",
            VERMES_home=self.tmp,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- doc_write --

    def test_write_basic(self):
        r = self.provider.handle_tool_call("doc_write", {
            "title": "Test Conclusion",
            "content": "## 结论\n- foo works",
            "scope": "task",
        })
        data = json.loads(r)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["scope"], "task")
        self.assertTrue(data["slug"])
        # 文件存在
        path = os.path.join(self.tmp, "docs", "task", data["slug"] + ".md")
        self.assertTrue(os.path.isfile(path))

    def test_write_missing_title(self):
        r = self.provider.handle_tool_call("doc_write", {"content": "x"})
        self.assertIn("error", json.loads(r))

    def test_write_missing_content(self):
        r = self.provider.handle_tool_call("doc_write", {"title": "x"})
        self.assertIn("error", json.loads(r))

    def test_write_invalid_scope_defaults_to_task(self):
        r = self.provider.handle_tool_call("doc_write", {
            "title": "T", "content": "C", "scope": "invalid",
        })
        data = json.loads(r)
        self.assertEqual(data["scope"], "task")

    def test_write_with_tags(self):
        r = self.provider.handle_tool_call("doc_write", {
            "title": "Tagged", "content": "body", "tags": "a,b,c",
        })
        data = json.loads(r)
        path = os.path.join(self.tmp, "docs", "task", data["slug"] + ".md")
        content = open(path, encoding="utf-8").read()
        self.assertIn("tags: a,b,c", content)

    # -- doc_read --

    def test_read_existing(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "Read Me", "content": "## Body\nhello",
        })
        slug = _slugify("Read Me")
        r = self.provider.handle_tool_call("doc_read", {
            "scope": "task", "slug": slug,
        })
        data = json.loads(r)
        self.assertIn("content", data)
        self.assertIn("hello", data["content"])

    def test_read_nonexistent(self):
        r = self.provider.handle_tool_call("doc_read", {
            "scope": "task", "slug": "nope",
        })
        self.assertIn("error", json.loads(r))

    def test_read_by_title(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "Read By Title", "content": "## Body\nhello title",
        })
        r = self.provider.handle_tool_call("doc_read", {
            "scope": "task", "title": "Read By Title",
        })
        data = json.loads(r)
        self.assertIn("content", data)
        self.assertIn("hello title", data["content"])

    # -- doc_list --

    def test_list_empty(self):
        r = self.provider.handle_tool_call("doc_list", {"scope": "task"})
        data = json.loads(r)
        self.assertEqual(data["docs"], [])

    def test_list_after_write(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "A", "content": "x", "scope": "project",
        })
        self.provider.handle_tool_call("doc_write", {
            "title": "B", "content": "y", "scope": "project",
        })
        r = self.provider.handle_tool_call("doc_list", {"scope": "project"})
        data = json.loads(r)
        self.assertEqual(len(data["docs"]), 2)

    # -- doc_search --

    def test_search_finds_keyword(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "Deep Learning", "content": "transformers are powerful",
        })
        r = self.provider.handle_tool_call("doc_search", {"query": "transformers"})
        data = json.loads(r)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["title"], "Deep Learning")

    def test_search_no_match(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "A", "content": "hello world",
        })
        r = self.provider.handle_tool_call("doc_search", {"query": "nonexistent"})
        data = json.loads(r)
        self.assertEqual(data["results"], [])

    def test_search_cross_scope(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "P1", "content": "keyword alpha", "scope": "project",
        })
        self.provider.handle_tool_call("doc_write", {
            "title": "T1", "content": "keyword beta", "scope": "task",
        })
        r = self.provider.handle_tool_call("doc_search", {"query": "keyword"})
        data = json.loads(r)
        self.assertEqual(len(data["results"]), 2)

    # -- on_pre_compress 自动落盘 --

    def test_on_pre_compress_no_decisions_no_write(self):
        messages = [
            {"role": "user", "content": "what is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]
        result = self.provider.on_pre_compress(messages)
        self.assertEqual(result, "")
        # 不应落盘
        task_dir = os.path.join(self.tmp, "docs", "task")
        if os.path.isdir(task_dir):
            self.assertEqual(len(os.listdir(task_dir)), 0)

    def test_on_pre_compress_with_decision_writes(self):
        messages = [
            {"role": "user", "content": "should we use postgres?"},
            {"role": "assistant", "content": "结论：我们决定使用 PostgreSQL 作为主数据库。"},
        ]
        result = self.provider.on_pre_compress(messages)
        self.assertNotEqual(result, "")
        task_dir = os.path.join(self.tmp, "docs", "task")
        files = os.listdir(task_dir) if os.path.isdir(task_dir) else []
        self.assertEqual(len(files), 1)

    def test_on_pre_compress_with_pending_writes(self):
        messages = [
            {"role": "user", "content": "plan next steps"},
            {"role": "assistant", "content": "接下来需要完成以下任务：\n- fix the authentication bug in module X\n- update the API documentation for v2"},
        ]
        result = self.provider.on_pre_compress(messages)
        self.assertNotEqual(result, "")

    # -- on_session_end --

    def test_on_session_end_writes_if_decisions(self):
        messages = [
            {"role": "user", "content": "decide architecture"},
            {"role": "assistant", "content": "结论：采用微服务架构。"},
        ]
        self.provider.on_session_end(messages)
        task_dir = os.path.join(self.tmp, "docs", "task")
        files = os.listdir(task_dir) if os.path.isdir(task_dir) else []
        self.assertEqual(len(files), 1)

    # -- on_session_switch --

    def test_on_session_switch_updates_session_id(self):
        self.provider.on_session_switch("new-session-2", reset=True)
        self.assertEqual(self.provider._session_id, "new-session-2")

    # -- backup_paths --

    def test_backup_paths(self):
        paths = self.provider.backup_paths()
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].endswith("docs"))

    # -- system_prompt_block --

    def test_system_prompt_empty_when_no_docs(self):
        self.assertEqual(self.provider.system_prompt_block(), "")

    def test_system_prompt_nonempty_when_docs_exist(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "T", "content": "C",
        })
        prompt = self.provider.system_prompt_block()
        self.assertIn("文档记忆", prompt)

    # -- prefetch --

    def test_prefetch_empty_when_no_docs(self):
        self.assertEqual(self.provider.prefetch("anything"), "")

    def test_prefetch_returns_match(self):
        self.provider.handle_tool_call("doc_write", {
            "title": "Python Guide", "content": "Python is a great language",
        })
        result = self.provider.prefetch("Python")
        self.assertIn("文档记忆上下文", result)


class TestFirstPartyProviderCoexistence(unittest.TestCase):
    """验证 docmemory 是 first-party，不消耗外部 provider 槽位。"""

    def test_docmemory_in_first_party_names(self):
        self.assertIn("docmemory", _FIRST_PARTY_PROVIDER_NAMES)

    def test_docmemory_does_not_consume_external_slot(self):
        mm = MemoryManager()
        # add docmemory (first-party) + mock external should both succeed
        from agent.docmemory_provider import DocMemoryProvider
        dp = DocMemoryProvider()
        mm.add_provider(dp)
        self.assertFalse(mm._has_external)

        # Now add a fake external provider
        from agent.memory_provider import MemoryProvider

        class FakeExternal(MemoryProvider):
            @property
            def name(self):
                return "fake_external"

            def is_available(self):
                return True

            def initialize(self, session_id, **kwargs):
                pass

            def sync_turn(self, *a, **kw):
                pass

            def get_tool_schemas(self):
                return []

            def handle_tool_call(self, *a, **kw):
                return "{}"

        fe = FakeExternal()
        mm.add_provider(fe)
        self.assertTrue(mm._has_external)

        # Second external should be rejected
        class FakeExternal2(MemoryProvider):
            @property
            def name(self):
                return "fake_external_2"

            def is_available(self):
                return True

            def initialize(self, session_id, **kwargs):
                pass

            def sync_turn(self, *a, **kw):
                pass

            def get_tool_schemas(self):
                return []

            def handle_tool_call(self, *a, **kw):
                return "{}"

        fe2 = FakeExternal2()
        mm.add_provider(fe2)
        # Should only have 2 providers (docmemory + fake_external)
        self.assertEqual(len(mm.providers), 2)


if __name__ == "__main__":
    unittest.main()
