"""流式 LLM 调用 + score 温度 + claim_audit lazy import 测试。

验证：
1. stream_call_llm 逐 chunk yield（mock httpx 流式响应）
2. stream_call_llm 错误处理（网络错误 / 无凭证）
3. write handler 支持 stream_callback（kw 传入回调）
4. score handler 传 temperature=0.2（通过 mock 验证）
5. claim_audit lazy import 不在顶层绑定 validators
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


class TestStreamCallLlm(unittest.IsolatedAsyncioTestCase):
    """stream_call_llm 单测。"""

    async def test_stream_yields_chunks(self):
        """逐 chunk yield delta.content。"""
        from vermes_cli.scholarforge import tools as T

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            'data: {"choices":[{"delta":{"content":"!"}}]}',
            'data: [DONE]',
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter():
            for line in sse_lines:
                yield line

        mock_resp.aiter_lines = _aiter
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_resp)
        mock_client.is_closed = False

        with patch.object(T, "_resolve_credentials", return_value={
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "model": "test-model",
            "provider": "test",
        }), patch.object(T, "_get_llm_client", return_value=mock_client):
            chunks = []
            async for c in T.stream_call_llm("test prompt"):
                chunks.append(c)

        self.assertEqual(chunks, ["Hello", " world", "!"])

    async def test_stream_network_error(self):
        """网络错误 yield 错误消息。"""
        from vermes_cli.scholarforge import tools as T

        mock_client = MagicMock()
        mock_client.stream = MagicMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.is_closed = False

        with patch.object(T, "_resolve_credentials", return_value={
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "model": "test-model",
            "provider": "test",
        }), patch.object(T, "_get_llm_client", return_value=mock_client):
            chunks = []
            async for c in T.stream_call_llm("test"):
                chunks.append(c)

        self.assertTrue(len(chunks) >= 1)
        self.assertIn("❌", chunks[0])

    async def test_stream_no_credentials(self):
        """无凭证时 yield 错误消息。"""
        from vermes_cli.scholarforge import tools as T

        with patch.object(T, "_resolve_credentials", return_value=None):
            chunks = []
            async for c in T.stream_call_llm("test"):
                chunks.append(c)

        self.assertEqual(len(chunks), 1)
        self.assertIn("❌", chunks[0])


class TestWriteStreamCallback(unittest.IsolatedAsyncioTestCase):
    """write handler stream_callback 测试。"""

    async def test_write_with_stream_callback(self):
        """传入 stream_callback 时走流式路径。"""
        from vermes_cli.scholarforge import tools as T

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"# 引言"}}]}',
            'data: {"choices":[{"delta":{"content":"研究背景"}}]}',
            'data: [DONE]',
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def _aiter():
            for line in sse_lines:
                yield line

        mock_resp.aiter_lines = _aiter
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_resp)
        mock_client.is_closed = False

        received_chunks = []

        def my_callback(chunk):
            received_chunks.append(chunk)

        with patch.object(T, "_resolve_credentials", return_value={
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-test",
            "model": "test-model",
            "provider": "test",
        }), patch.object(T, "_get_llm_client", return_value=mock_client):
            result = await T._handle_scholarforge_write(
                {"section_type": "introduction", "topic": "AI"},
                stream_callback=my_callback,
            )

        # 回调收到了 chunks
        self.assertGreaterEqual(len(received_chunks), 1)
        # 最终返回聚合内容
        self.assertTrue(len(result) > 0)

    async def test_write_without_stream_callback(self):
        """不传 stream_callback 时走原有路径。"""
        from vermes_cli.scholarforge import tools as T

        with patch.object(T, "_call_llm", new_callable=AsyncMock, return_value="生成的内容"):
            result = await T._handle_scholarforge_write(
                {"section_type": "introduction", "topic": "AI"},
            )

        self.assertIn("生成的内容", result)


class TestScoreTemperature(unittest.IsolatedAsyncioTestCase):
    """score handler 温度参数测试。"""

    async def test_score_uses_low_temperature(self):
        """score 通过 _analysis_llm wrapper 传 temperature=0.2。"""
        from vermes_cli.scholarforge import tools as T

        captured = {}

        async def mock_call(*args, **kwargs):
            captured.update(kwargs)
            return '{"originality": {"score": 8}, "logic": {"score": 7}, "citation_completeness": {"score": 6}, "overall": 7.0}'

        with patch.object(T, "_call_llm", new=mock_call):
            # 传入足够长的内容（>500字）
            long_content = "这是测试论文内容。" * 100
            await T._handle_scholarforge_score({"content": long_content})

        self.assertEqual(captured.get("temperature"), 0.2)


class TestClaimAuditLazyImport(unittest.TestCase):
    """claim_audit.py lazy import 测试。"""

    def test_no_top_level_validator_import(self):
        """顶层不直接 import validators 函数。"""
        import ast

        with open("vermes_cli/scholarforge/claim_audit.py") as f:
            tree = ast.parse(f.read())

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "validators" in node.module:
                    for alias in node.names:
                        if alias.name in (
                            "verify_citation_authenticity",
                            "check_statistics_consistency",
                            "detect_design_flaws",
                        ):
                            self.fail(
                                f"claim_audit.py 顶层不应 import {alias.name} from validators"
                            )

    def test_lazy_import_works(self):
        """_import_validators 返回 5 个可调用对象。"""
        from vermes_cli.scholarforge.claim_audit import _import_validators

        result = _import_validators()
        self.assertEqual(len(result), 5)
        for fn in result:
            self.assertTrue(callable(fn))


if __name__ == "__main__":
    unittest.main()


class TestContextvarStreamPropagation(unittest.IsolatedAsyncioTestCase):
    """ScholarForge 工具 handler 通过 contextvar 获取 stream callback。"""

    async def test_contextvar_set_get(self):
        """set_stream_callback / get_stream_callback 基本可用。"""
        from vermes_cli.scholarforge.tools import set_stream_callback, get_stream_callback
        self.assertIsNone(get_stream_callback())
        cb = lambda x: None
        set_stream_callback(cb)
        self.assertIs(get_stream_callback(), cb)
        set_stream_callback(None)
        self.assertIsNone(get_stream_callback())

    async def test_write_handler_uses_contextvar(self):
        """write handler 没传 stream_callback kw 时，从 contextvar 获取。"""
        from vermes_cli.scholarforge import tools as T

        chunks = []
        def _cb(delta):
            chunks.append(delta)

        async def mock_stream(prompt, system=""):
            yield "chunk1"
            yield "chunk2"

        T.set_stream_callback(_cb)
        try:
            with patch.object(T, "stream_call_llm", new=mock_stream), \
                 patch.object(T, "_call_llm", new=AsyncMock(return_value="fallback")), \
                 patch("vermes_cli.scholarforge.project_context.save_section", new=AsyncMock()):
                # 不传 stream_callback kw，project_id=0 避免触发 DB
                await T._handle_scholarforge_write({
                    "project_id": 0,
                    "section_type": "introduction",
                    "instructions": "test",
                })
        finally:
            T.set_stream_callback(None)

        self.assertEqual(chunks, ["chunk1", "chunk2"])
