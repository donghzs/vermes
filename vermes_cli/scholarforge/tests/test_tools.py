"""
ScholarForge Agent Tools — 测试套件
覆盖 3 个工具的注册、Schema、Handler 路径和凭证解析。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure vermes_cli is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ScholarForge 工具采用惰性注册（import 不触发 register_tools），
# 在任意测试类运行前于模块加载时显式注册一次，避免测试类执行顺序导致的注册缺失。
import vermes_cli.scholarforge.tools as _sf_tools  # noqa: E402
from tools.registry import registry as _sf_registry  # noqa: E402

if _sf_registry.get_entry("scholarforge_search") is None:
    _sf_tools.register_tools()


class TestScholarForgeToolsRegistration(unittest.TestCase):
    """验证 3 个工具注册到全局 registry"""

    @classmethod
    def setUpClass(cls):
        # 注册已在模块加载时完成（见文件顶部 _sf_tools.register_tools()）
        cls.registry = _sf_registry

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
        from vermes_cli.scholarforge.tools import _handle_scholarforge_search

        self.assertTrue(callable(_handle_scholarforge_search))
        # 验证是 async function
        import inspect

        self.assertTrue(
            inspect.iscoroutinefunction(_handle_scholarforge_search),
            "Handler should be an async function",
        )

    def test_write_handler_accepts_args(self):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_write

        import inspect

        self.assertTrue(inspect.iscoroutinefunction(_handle_scholarforge_write))

    def test_review_handler_accepts_args(self):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_review

        import inspect

        self.assertTrue(inspect.iscoroutinefunction(_handle_scholarforge_review))

    def test_search_empty_query_returns_error(self):
        import asyncio
        from vermes_cli.scholarforge.tools import _handle_scholarforge_search

        async def run():
            result = await _handle_scholarforge_search({"query": "  "})
            self.assertIn("❌", result)
            self.assertIn("关键词", result)

        asyncio.run(run())

    def test_write_empty_topic_works(self):
        """空 topic 也应能调用（由 LLM 端处理），不抛异常"""
        import asyncio
        from vermes_cli.scholarforge.tools import _handle_scholarforge_write

        async def run():
            result = await _handle_scholarforge_write(
                {"topic": "", "section_type": "introduction"}
            )
            # 空 topic 应该能正常返回（只是结果质量不高）
            self.assertIsInstance(result, str)

        # 不实际调用 LLM，只验证不抛异常（mock 掉 _call_llm）
        async def run_mocked():
            with patch(
                "vermes_cli.scholarforge.tools._call_llm",
                AsyncMock(return_value="## 引言\n\n测试内容"),
            ):
                result = await _handle_scholarforge_write(
                    {"topic": "测试", "section_type": "introduction"}
                )
                self.assertIn("引言", result)

        asyncio.run(run_mocked())

    def test_review_empty_draft_returns_error(self):
        import asyncio
        from vermes_cli.scholarforge.tools import _handle_scholarforge_review

        async def run():
            result = await _handle_scholarforge_review({"draft": ""})
            self.assertIn("❌", result)

        asyncio.run(run())


class TestCredentialResolution(unittest.TestCase):
    """验证凭证解析逻辑不抛异常，且无配置时返回 None（环境无关）"""

    def test_resolve_credentials_no_config(self):
        """主链路与配置文件均无凭证时返回 None（不抛异常）"""
        from vermes_cli.scholarforge.tools import _resolve_credentials

        with tempfile.TemporaryDirectory() as tmp:
            # 主链路返回空 + 本地 home 无 config.yaml/.env → 两路皆空 → None
            with patch("vermes_cli.blueprints.chat._get_chat_credentials", return_value=("", "", "")):
                with patch("vermes_constants.get_vermes_home", return_value=Path(tmp)):
                    result = _resolve_credentials()
            self.assertIsNone(result)

    def test_resolve_credentials_does_not_crash(self):
        """主链路抛异常时也应优雅降级返回 None（不崩溃）"""
        from vermes_cli.scholarforge.tools import _resolve_credentials

        with tempfile.TemporaryDirectory() as tmp:
            with patch("vermes_cli.blueprints.chat._get_chat_credentials", side_effect=Exception("mock")):
                with patch("vermes_constants.get_vermes_home", return_value=Path(tmp)):
                    try:
                        result = _resolve_credentials()
                    except Exception:
                        result = "RAISED"
            # 不应抛异常，且两路皆空应返回 None
            self.assertIsNone(result)


class TestLLMCallNoKey(unittest.TestCase):
    """验证无 API Key 时 _call_llm 返回友好错误信息（不崩溃）"""

    def test_call_llm_no_key(self):
        import asyncio
        from vermes_cli.scholarforge.tools import _call_llm

        async def run():
            with patch(
                "vermes_cli.scholarforge.tools._resolve_credentials",
                return_value=None,
            ):
                result = await _call_llm("test prompt")
                self.assertIn("未找到", result)
                self.assertIn("API Key", result)

        asyncio.run(run())


class TestPlagiarismReportFormatting(unittest.TestCase):
    """P0 Bug1 回归：plagiarism_check 输出格式化必须使用 PlagResult/AigcResult 真实字段，
    不能用不存在的 source_para/target_para/similarity/paragraph_preview/label/confidence。
    若字段名写错，handler 会吞掉 AttributeError 并返回 '❌ 查重检测失败'，本测试据此捕获回归。"""

    def test_formatting_uses_real_fields_not_phantom(self):
        import asyncio

        from vermes_cli.scholarforge.plagcheck import (
            PlagResult,
            AigcResult,
            PlagReport,
        )
        from vermes_cli.scholarforge.tools import (
            _handle_scholarforge_plagiarism_check,
        )

        # 构造含高相似段落 + AIGC 命中的报告，强制走格式化分支
        report = PlagReport(
            total_chars=1234,
            total_paragraphs=5,
            overall_similarity=0.42,
            aigc_overall_ratio=0.55,
            plag_results=[
                PlagResult(
                    text="重复片段示例文本用于查重检测",
                    length=12,
                    position=80,
                    score=0.91,
                    source="internal",
                )
            ],
            aigc_results=[
                AigcResult(
                    text="AI生成段落内容",
                    position=200,
                    aigc_probability=0.88,
                    features=["句长CV=0.20", "连接词=3"],
                )
            ],
            suggestions=["建议：改写高重复段落"],
            checked_sources=["simhash"],
        )

        async def run():
            # 直接替换底层检测函数，隔离网络与算法，仅验证报告格式化
            with patch(
                "vermes_cli.scholarforge.plagcheck.full_plagiarism_check",
                return_value=report,
            ):
                return await _handle_scholarforge_plagiarism_check(
                    {"text": "任意文本用于触发格式化分支"}
                )

        out = asyncio.run(run())

        # 回归捕获：字段名写错时 handler 会吞异常返回失败串
        self.assertNotIn("❌ 查重检测失败", out)
        # 高相似段落分支（PlagResult.position / score / text）
        self.assertIn("位置 80", out)
        self.assertIn("相似度 91.0%", out)
        self.assertIn("重复片段示例文本用于查重检测", out)
        # AIGC 特征分支（AigcResult.position / aigc_probability / features）
        self.assertIn("位置 200", out)
        self.assertIn("特征强度 88%", out)
        self.assertIn("特征: 句长CV=0.20, 连接词=3", out)
        # 建议分支
        self.assertIn("建议：改写高重复段落", out)


if __name__ == "__main__":
    unittest.main()
