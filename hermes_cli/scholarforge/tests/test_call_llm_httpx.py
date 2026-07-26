"""Phase A 回归：_call_llm 从 urllib+to_thread 迁移到 httpx 异步 + 温度/模型/json_mode 参数。

用假 AsyncClient mock 网络，不依赖真实 API Key，也不触发真实 DNS/连接。
"""
import asyncio
import json
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from hermes_cli.scholarforge.tools import _call_llm


_FAKE_CREDS = {
    "base_url": "http://fake/v1",
    "api_key": "k",
    "model": "default-model",
    "provider": "fake",
}


class _FakeResponse:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class _FakeClient:
    def __init__(self, responder):
        self._responder = responder
        self.is_closed = False

    async def post(self, url, json=None, headers=None):
        return await self._responder(json or {})


def _resp(content, status=200):
    return _FakeResponse(
        status_code=status,
        data={"choices": [{"message": {"content": content}}]},
        text=json.dumps({"choices": [{"message": {"content": content}}]}),
    )


def _patch_both(client, creds=_FAKE_CREDS):
    stack = ExitStack()
    stack.enter_context(
        patch("hermes_cli.scholarforge.tools._get_llm_client", return_value=client)
    )
    stack.enter_context(
        patch("hermes_cli.scholarforge.tools._resolve_credentials", return_value=creds)
    )
    stack.enter_context(patch("asyncio.sleep", new=AsyncMock()))
    return stack


class TestCallLlmHttpx(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_success_passes_temperature_and_system(self):
        captured = {}

        async def responder(body):
            captured.update(body)
            return _resp("hello")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p", system="s", temperature=0.2)

        self.assertEqual(self._run(run()), "hello")
        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(captured["messages"][0]["role"], "system")
        self.assertNotIn("response_format", captured)

    def test_retry_on_500_then_success(self):
        seq = [500, 500, 200]

        async def responder(body):
            code = seq.pop(0)
            return _FakeResponse(
                status_code=code,
                data={"choices": [{"message": {"content": "ok"}}]},
                text="x",
            )

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p", temperature=0.2)

        self.assertEqual(self._run(run()), "ok")
        self.assertEqual(seq, [])  # 两次 500 + 一次成功，恰好 3 次调用

    def test_give_up_after_3_500(self):
        async def responder(body):
            return _FakeResponse(status_code=500, data={}, text="server error")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p")

        out = self._run(run())
        self.assertIn("HTTP 500", out)

    def test_4xx_not_retried(self):
        async def responder(body):
            return _FakeResponse(status_code=401, data={}, text="unauth")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p")

        out = self._run(run())
        self.assertIn("HTTP 401", out)

    def test_empty_content_is_format_error(self):
        async def responder(body):
            return _FakeResponse(
                status_code=200,
                data={"choices": [{"message": {"content": ""}}]},
                text="{}",
            )

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p")

        out = self._run(run())
        self.assertIn("响应格式异常", out)

    def test_json_mode_sets_response_format(self):
        captured = {}

        async def responder(body):
            captured.update(body)
            return _resp("x")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p", json_mode=True)

        self._run(run())
        self.assertEqual(captured["response_format"], {"type": "json_object"})

    def test_model_override(self):
        captured = {}

        async def responder(body):
            captured.update(body)
            return _resp("x")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("p", model="mini")

        self._run(run())
        self.assertEqual(captured["model"], "mini")

    def test_default_signature_backward_compatible(self):
        """既有调用 _call_llm("prompt") 不传关键字参数仍能工作。"""
        async def responder(body):
            return _resp("ok")

        client = _FakeClient(responder)

        async def run():
            with _patch_both(client):
                return await _call_llm("prompt")

        self.assertEqual(self._run(run()), "ok")
        self.assertEqual(client, client)  # sanity


if __name__ == "__main__":
    unittest.main()
