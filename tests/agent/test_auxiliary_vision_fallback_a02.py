"""Regression tests for A0-2: vision provider fallback (#A0-2).

A named provider (e.g. ``xiaomi``) configured with a vision ``base_url`` but
no usable API key resolves to *no client*. The old gate
``client is None and resolved_provider != "auto" and not resolved_base_url``
skipped the auto-fallback whenever a base_url was present and raised
``RuntimeError``, breaking ``browser_vision`` entirely. These tests pin the
fixed behaviour: named providers fall back to auto, an explicit ``custom``
endpoint keeps the hard-fail, and a truly empty auto chain still raises.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _DummyResponse:
    def __init__(self, text="ok"):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=text))]


def _make_client(url: str, *, async_mode: bool):
    client = MagicMock()
    client.base_url = url
    if async_mode:
        client.chat.completions.create = AsyncMock(
            return_value=_DummyResponse("vision-ok")
        )
    else:
        client.chat.completions.create.return_value = _DummyResponse("vision-ok")
    return client


class TestVisionNamedProviderFallsBackToAuto:
    """A0-2: named provider + base_url + no key must fall back to auto."""

    def test_sync_xiaomi_with_base_url_no_key_falls_back(self):
        auto_client = _make_client("https://openrouter.ai/api/v1", async_mode=False)
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("xiaomi", None, "https://xiaomi-vision.test/v1", None, None),
            ),
            patch(
                "agent.auxiliary_client.resolve_vision_provider_client",
                side_effect=[
                    ("xiaomi", None, None),  # named provider → no client
                    ("openrouter", auto_client, "openrouter-model"),  # auto fallback
                ],
            ),
        ):
            from agent.auxiliary_client import call_llm

            resp = call_llm(
                task="vision",
                provider="xiaomi",
                model=None,
                messages=[{"role": "user", "content": "describe this image"}],
            )
        assert resp.choices[0].message.content == "vision-ok"

    @pytest.mark.asyncio
    async def test_async_xiaomi_with_base_url_no_key_falls_back(self):
        auto_client = _make_client("https://openrouter.ai/api/v1", async_mode=True)
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("xiaomi", None, "https://xiaomi-vision.test/v1", None, None),
            ),
            patch(
                "agent.auxiliary_client.resolve_vision_provider_client",
                side_effect=[
                    ("xiaomi", None, None),
                    ("openrouter", auto_client, "openrouter-model"),
                ],
            ),
        ):
            from agent.auxiliary_client import async_call_llm

            resp = await async_call_llm(
                task="vision",
                provider="xiaomi",
                model=None,
                messages=[{"role": "user", "content": "describe this image"}],
            )
        assert resp.choices[0].message.content == "vision-ok"

    def test_zai_named_provider_with_base_url_falls_back(self):
        """Same fix applies to other named providers (zai) with a base_url."""
        auto_client = _make_client("https://openrouter.ai/api/v1", async_mode=False)
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("zai", None, "https://api.z.ai/api/paas/v4", None, None),
            ),
            patch(
                "agent.auxiliary_client.resolve_vision_provider_client",
                side_effect=[
                    ("zai", None, None),
                    ("openrouter", auto_client, "openrouter-model"),
                ],
            ),
        ):
            from agent.auxiliary_client import call_llm

            resp = call_llm(
                task="vision",
                provider="zai",
                model=None,
                messages=[{"role": "user", "content": "describe"}],
            )
        assert resp.choices[0].message.content == "vision-ok"


class TestVisionExplicitCustomStillHardFails:
    """A genuinely explicit ``custom`` endpoint must keep the hard-fail."""

    def test_explicit_custom_endpoint_no_client_raises(self):
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("custom", None, "https://my-endpoint.test/v1", None, None),
            ),
            patch(
                "agent.auxiliary_client.resolve_vision_provider_client",
                return_value=("custom", None, None),
            ),
        ):
            from agent.auxiliary_client import call_llm

            with pytest.raises(RuntimeError):
                call_llm(
                    task="vision",
                    provider="custom",
                    model=None,
                    messages=[{"role": "user", "content": "x"}],
                )


class TestVisionAutoChainExhaustedRaises:
    """When even auto has no backend, the call must still raise."""

    def test_auto_with_no_backend_raises(self):
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("auto", None, None, None, None),
            ),
            patch(
                "agent.auxiliary_client.resolve_vision_provider_client",
                return_value=("auto", None, None),
            ),
        ):
            from agent.auxiliary_client import call_llm

            with pytest.raises(RuntimeError):
                call_llm(
                    task="vision",
                    messages=[{"role": "user", "content": "x"}],
                )
