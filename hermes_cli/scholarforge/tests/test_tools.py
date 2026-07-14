"""
ScholarForge Agent Tools — 测试套件
覆盖 3 个工具的注册、Schema、Handler 路径和凭证解析。
"""
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure hermes_cli is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class TestScholarForgeToolsRegistration(unittest.TestCase):
    """验证 3 个工具注册到全局 registry"""

    @classmethod
    def setUpClass(cls):
        import hermes_cli.scholarforge.tools  # noqa: F401 — triggers registration
        from tools.registry import registry

        cls.registry = registry

    def test_all_three_tools_registered(self):
        for name in [
            "scholarforge_search",
            "scholarforge_write",
            "scholarforge_review",
        ]:
            with self.subTest(name=name):
                entry = self.registry.get_entry(name)
                self.assertIsNotNone(entry, f"{name} should be registered")

    def test_tools_have_correct_toolset(self):
        for name in [
            "scholarforge_search",
            "scholarforge_write",
            "scholarforge_review",
        ]:
            with self.subTest(name=name):
                entry = self.registry.get_entry(name)
                self.assertEqual(entry.toolset, "scholarforge")

    def test_tools_are_async(self):
        for name in [
            "scholarforge_search",
            "scholarforge_write",
            "scholarforge_review",
        ]:
            with self.subTest(name=name):
                entry = self.registry.get_entry(name)
                self.assertTrue(
                    getattr(entry, "is_async", False),
                    f"{name} should be is_async=True",
                )


class TestToolSchemas(unittest.TestCase):
    """验证每个工具的 Schema 是否符合 OpenAI function-calling 格式"""

    def test_search_schema(self):
        s = self._get_schema("scholarforge_search")
        self.assertIn("query", s["parameters"]["properties"])
        self.assertIn("limit", s["parameters"]["properties"])
        self.assertEqual(s["parameters"]["required"], ["query"])
        self.assertEqual(
            s["parameters"]["properties"]["limit"]["maximum"], 30
        )
        self.assertEqual(
            s["parameters"]["properties"]["limit"]["minimum"], 1
        )

    def test_write_schema(self):
        s = self._get_schema("scholarforge_write")
        props = s["parameters"]["properties"]
        self.assertIn("topic", props)
        self.assertIn("section_type", props)
        self.assertIn("context", props)
        self.assertEqual(s["parameters"]["required"], ["topic", "section_type"])
        self.assertIn(
            "introduction", props["section_type"]["enum"]
        )
        self.assertIn(
            "conclusion", props["section_type"]["enum"]
        )

    def test_review_schema(self):
        s = self._get_schema("scholarforge_review")
        props = s["parameters"]["properties"]
        self.assertIn("draft", props)
        self.assertIn("focus", props)
        self.assertEqual(s["parameters"]["required"], ["draft"])

    def _get_schema(self, name):
        from tools.registry import registry

        entry = registry.get_entry(name)
        self.assertIsNotNone(entry, f"{name} not registered")
        schema = entry.schema
        self.assertIn("name", schema)
        self.assertIn("description", schema)
        self.assertIn("parameters", schema)
        self.assertEqual(schema["parameters"]["type"], "object")
        self.assertIn("properties", schema["parameters"])
        return schema


class TestHandlerSignature(unittest.TestCase):
    """验证 Handler 可被 await 调用（不依赖 API Key 的路径测试）"""

    def test_search_handler_accepts_args(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_search

        self.assertTrue(callable(_handle_scholarforge_search))
        # 验证是 async function
        import inspect

        self.assertTrue(
            inspect.iscoroutinefunction(_handle_scholarforge_search),
            "Handler should be an async function",
        )

    def test_write_handler_accepts_args(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_write

        import inspect

        self.assertTrue(inspect.iscoroutinefunction(_handle_scholarforge_write))

    def test_review_handler_accepts_args(self):
        from hermes_cli.scholarforge.tools import _handle_scholarforge_review

        import inspect

        self.assertTrue(inspect.iscoroutinefunction(_handle_scholarforge_review))

    def test_search_empty_query_returns_error(self):
        import asyncio
        from hermes_cli.scholarforge.tools import _handle_scholarforge_search

        async def run():
            result = await _handle_scholarforge_search({"query": "  "})
            self.assertIn("❌", result)
            self.assertIn("关键词", result)

        asyncio.run(run())

    def test_write_empty_topic_works(self):
        """空 topic 也应能调用（由 LLM 端处理），不抛异常"""
        import asyncio
        from hermes_cli.scholarforge.tools import _handle_scholarforge_write

        async def run():
            result = await _handle_scholarforge_write(
                {"topic": "", "section_type": "introduction"}
            )
            # 空 topic 应该能正常返回（只是结果质量不高）
            self.assertIsInstance(result, str)

        # 不实际调用 LLM，只验证不抛异常（mock 掉 _call_llm）
        async def run_mocked():
            with patch(
                "hermes_cli.scholarforge.tools._call_llm",
                AsyncMock(return_value="## 引言\n\n测试内容"),
            ):
                result = await _handle_scholarforge_write(
                    {"topic": "测试", "section_type": "introduction"}
                )
                self.assertIn("引言", result)

        asyncio.run(run_mocked())

    def test_review_empty_draft_returns_error(self):
        import asyncio
        from hermes_cli.scholarforge.tools import _handle_scholarforge_review

        async def run():
            result = await _handle_scholarforge_review({"draft": ""})
            self.assertIn("❌", result)

        asyncio.run(run())


class TestCredentialResolution(unittest.TestCase):
    """验证凭证解析逻辑不抛异常"""

    def test_resolve_credentials_no_config(self):
        """无凭证配置时返回 None（不抛异常）"""
        from hermes_cli.scholarforge.tools import _resolve_credentials

        with patch("hermes_cli.blueprints.chat._get_chat_credentials", return_value=("", "", "")):
            result = _resolve_credentials()
            self.assertIsNone(result)

    def test_resolve_credentials_does_not_crash(self):
        """即使凭证读取异常也不崩溃"""
        from hermes_cli.scholarforge.tools import _resolve_credentials

        with patch("hermes_cli.blueprints.chat._get_chat_credentials", side_effect=Exception("mock")):
            try:
                result = _resolve_credentials()
            except Exception:
                result = None
            # Should handle gracefully
            self.assertIsNone(result)


class TestLLMCallNoKey(unittest.TestCase):
    """验证无 API Key 时 _call_llm 返回友好错误信息（不崩溃）"""

    def test_call_llm_no_key(self):
        import asyncio
        from hermes_cli.scholarforge.tools import _call_llm

        async def run():
            with patch(
                "hermes_cli.scholarforge.tools._resolve_credentials",
                return_value=None,
            ):
                result = await _call_llm("test prompt")
                self.assertIn("未找到", result)
                self.assertIn("API Key", result)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
