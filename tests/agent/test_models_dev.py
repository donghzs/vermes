"""Tests for agent.models_dev — models.dev registry integration."""
import json
from unittest.mock import patch, MagicMock

import pytest
from agent.models_dev import (
    PROVIDER_TO_MODELS_DEV,
    _extract_context,
    fetch_models_dev,
    get_model_capabilities,
    lookup_models_dev_context,
)


SAMPLE_REGISTRY = {
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "models": {
            "claude-opus-4-6": {
                "id": "claude-opus-4-6",
                "limit": {"context": 1000000, "output": 128000},
            },
            "claude-sonnet-4-6": {
                "id": "claude-sonnet-4-6",
                "limit": {"context": 1000000, "output": 64000},
            },
            "claude-sonnet-4-0": {
                "id": "claude-sonnet-4-0",
                "limit": {"context": 200000, "output": 64000},
            },
        },
    },
    "github-copilot": {
        "id": "github-copilot",
        "name": "GitHub Copilot",
        "models": {
            "claude-opus-4.6": {
                "id": "claude-opus-4.6",
                "limit": {"context": 128000, "output": 32000},
            },
        },
    },
    "kilo": {
        "id": "kilo",
        "name": "Kilo Gateway",
        "models": {
            "anthropic/claude-sonnet-4.6": {
                "id": "anthropic/claude-sonnet-4.6",
                "limit": {"context": 1000000, "output": 128000},
            },
        },
    },
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek",
        "models": {
            "deepseek-chat": {
                "id": "deepseek-chat",
                "limit": {"context": 128000, "output": 8192},
            },
        },
    },
    "audio-only": {
        "id": "audio-only",
        "models": {
            "tts-model": {
                "id": "tts-model",
                "limit": {"context": 0, "output": 0},
            },
        },
    },
}


class TestProviderMapping:
    def test_all_mapped_providers_are_strings(self):
        for VERMES_id, mdev_id in PROVIDER_TO_MODELS_DEV.items():
            assert isinstance(VERMES_id, str)
            assert isinstance(mdev_id, str)

    def test_known_providers_mapped(self):
        assert PROVIDER_TO_MODELS_DEV["anthropic"] == "anthropic"
        assert PROVIDER_TO_MODELS_DEV["copilot"] == "github-copilot"
        assert PROVIDER_TO_MODELS_DEV["stepfun"] == "stepfun"
        assert PROVIDER_TO_MODELS_DEV["kilocode"] == "kilo"
        assert PROVIDER_TO_MODELS_DEV["ai-gateway"] == "vercel"

    def test_unmapped_provider_not_in_dict(self):
        assert "nous" not in PROVIDER_TO_MODELS_DEV

    def test_openai_codex_mapped_to_openai(self):
        assert PROVIDER_TO_MODELS_DEV["openai"] == "openai"
        assert PROVIDER_TO_MODELS_DEV["openai-codex"] == "openai"


class TestExtractContext:
    def test_valid_entry(self):
        assert _extract_context({"limit": {"context": 128000}}) == 128000

    def test_zero_context_returns_none(self):
        assert _extract_context({"limit": {"context": 0}}) is None

    def test_missing_limit_returns_none(self):
        assert _extract_context({"id": "test"}) is None

    def test_missing_context_returns_none(self):
        assert _extract_context({"limit": {"output": 8192}}) is None

    def test_non_dict_returns_none(self):
        assert _extract_context("not a dict") is None

    def test_float_context_coerced_to_int(self):
        assert _extract_context({"limit": {"context": 131072.0}}) == 131072


class TestLookupModelsDevContext:
    @patch("agent.models_dev.fetch_models_dev")
    def test_exact_match(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_REGISTRY
        assert lookup_models_dev_context("anthropic", "claude-opus-4-6") == 1000000

    @patch("agent.models_dev.fetch_models_dev")
    def test_case_insensitive_match(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_REGISTRY
        assert lookup_models_dev_context("anthropic", "Claude-Opus-4-6") == 1000000

    @patch("agent.models_dev.fetch_models_dev")
    def test_provider_not_mapped(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_REGISTRY
        assert lookup_models_dev_context("nous", "some-model") is None

    @patch("agent.models_dev.fetch_models_dev")
    def test_model_not_found(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_REGISTRY
        assert lookup_models_dev_context("anthropic", "nonexistent-model") is None

    @patch("agent.models_dev.fetch_models_dev")
    def test_provider_aware_context(self, mock_fetch):
        """Same model, different context per provider."""
        mock_fetch.return_value = SAMPLE_REGISTRY
        # Anthropic direct: 1M
        assert lookup_models_dev_context("anthropic", "claude-opus-4-6") == 1000000
        # GitHub Copilot: only 128K for same model
        assert lookup_models_dev_context("copilot", "claude-opus-4.6") == 128000

    @patch("agent.models_dev.fetch_models_dev")
    def test_zero_context_filtered(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_REGISTRY
        # audio-only is not a mapped provider, but test the filtering directly
        data = SAMPLE_REGISTRY["audio-only"]["models"]["tts-model"]
        assert _extract_context(data) is None

    @patch("agent.models_dev.fetch_models_dev")
    def test_empty_registry(self, mock_fetch):
        mock_fetch.return_value = {}
        assert lookup_models_dev_context("anthropic", "claude-opus-4-6") is None


class TestFetchModelsDev:
    @pytest.fixture(autouse=True)
    def _reset_module_cache_state(self):
        """Reset every module-level cache global between test cases.

        ``fetch_models_dev`` keeps three pieces of process-wide state: the
        payload, its timestamp, and the last-network-failure timestamp that
        suppresses retries. Leaking any of them across tests makes results
        order-dependent — a failed fetch in one case would silently make the
        next case skip the very network call it is asserting on.
        """
        import agent.models_dev as md
        _clean = (
            ("_models_dev_cache", {}),
            ("_models_dev_cache_time", 0),
            ("_models_dev_last_failure", 0.0),
        )
        for _name, _value in _clean:
            setattr(md, _name, _value)
        yield
        for _name, _value in _clean:
            setattr(md, _name, _value)

    @patch("agent.models_dev.requests.get")
    def test_fetch_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REGISTRY
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # Clear caches
        import agent.models_dev as md
        md._models_dev_cache = {}
        md._models_dev_cache_time = 0

        with patch.object(md, "_save_disk_cache"):
            result = fetch_models_dev(force_refresh=True)

        assert "anthropic" in result
        assert len(result) == len(SAMPLE_REGISTRY)

    @patch("agent.models_dev.requests.get")
    def test_fetch_failure_returns_stale_cache(self, mock_get):
        mock_get.side_effect = Exception("network error")

        import agent.models_dev as md
        md._models_dev_cache = SAMPLE_REGISTRY
        md._models_dev_cache_time = 0  # expired

        with patch.object(md, "_load_disk_cache", return_value=SAMPLE_REGISTRY):
            result = fetch_models_dev(force_refresh=True)

        assert "anthropic" in result

    @patch("agent.models_dev.requests.get")
    def test_in_memory_cache_used(self, mock_get):
        import agent.models_dev as md
        import time
        md._models_dev_cache = SAMPLE_REGISTRY
        md._models_dev_cache_time = time.time()  # fresh

        result = fetch_models_dev()
        mock_get.assert_not_called()
        assert result == SAMPLE_REGISTRY

    @patch("agent.models_dev.requests.get")
    def test_fresh_disk_cache_skips_network(self, mock_get):
        """When in-mem cache is empty but disk cache exists and is fresh by
        mtime (< TTL), fetch_models_dev returns disk data without ever
        making the network call.

        This is the cold-start fast path: every fresh process previously
        paid ~500 ms re-fetching a registry that was already on disk
        from an earlier run.
        """
        import agent.models_dev as md
        # Empty in-mem cache so stage 1 doesn't short-circuit.
        md._models_dev_cache = {}
        md._models_dev_cache_time = 0

        with patch.object(md, "_disk_cache_age_seconds", return_value=60.0), \
             patch.object(md, "_load_disk_cache", return_value=SAMPLE_REGISTRY):
            result = fetch_models_dev()

        # The whole point: no network call.
        mock_get.assert_not_called()
        assert "anthropic" in result
        # In-mem cache populated so subsequent calls within the same
        # process stay on stage 1.
        assert md._models_dev_cache == SAMPLE_REGISTRY

    @patch("agent.models_dev.requests.get")
    def test_stale_disk_cache_falls_through_to_network(self, mock_get):
        """When the disk cache is OLDER than TTL, we must hit the network
        (and only fall back to the stale disk data if network fails)."""
        import agent.models_dev as md
        md._models_dev_cache = {}
        md._models_dev_cache_time = 0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REGISTRY
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # Disk cache exists but is older than the TTL — must NOT short-circuit.
        with patch.object(md, "_disk_cache_age_seconds",
                          return_value=md._MODELS_DEV_CACHE_TTL + 60), \
             patch.object(md, "_load_disk_cache", return_value=SAMPLE_REGISTRY), \
             patch.object(md, "_save_disk_cache"):
            result = fetch_models_dev()

        mock_get.assert_called_once()
        assert "anthropic" in result

    @patch("agent.models_dev.requests.get")
    def test_repeated_network_failure_does_not_retry_every_call(self, mock_get):
        """An unreachable models.dev must cost ONE timeout, not one per call.

        Regression guard for the GUI-wide hang: models.dev sits on the
        synchronous path of /api/model/info, /api/chat/models and chat
        completions, which FastAPI serves from a bounded threadpool. The old
        code only re-anchored ``_models_dev_cache_time`` inside the
        ``if not _models_dev_cache`` branch, so once a stale cache had been
        loaded the timestamp stayed permanently expired and EVERY subsequent
        lookup paid the full socket timeout again. With enough concurrent
        callers the threadpool drained and every synchronous endpoint stopped
        answering — which the browser reports as "Failed to fetch".
        """
        import agent.models_dev as md
        mock_get.side_effect = OSError("network unreachable")

        with patch.object(md, "_disk_cache_age_seconds",
                          return_value=md._MODELS_DEV_CACHE_TTL + 60), \
             patch.object(md, "_load_disk_cache", return_value=SAMPLE_REGISTRY), \
             patch.object(md, "_save_disk_cache"):
            first = fetch_models_dev()
            for _ in range(5):
                # Age the in-memory timestamp past the TTL before each call.
                # This is what actually happens in a long-lived GUI process:
                # the short post-failure grace period lapses, stage 1 and
                # stage 2 both miss, and control reaches the network stage
                # again. Suppression must come from the failure timestamp,
                # not from the payload timestamp.
                md._models_dev_cache_time = 0
                again = fetch_models_dev()

        # Exactly one network attempt despite six calls.
        assert mock_get.call_count == 1, (
            f"expected 1 network attempt inside the failure grace window, "
            f"got {mock_get.call_count} — negative caching is not engaging"
        )
        # Stale data is still served rather than an empty registry.
        assert "anthropic" in first
        assert "anthropic" in again

    @patch("agent.models_dev.requests.get")
    def test_force_refresh_skips_disk_cache(self, mock_get):
        """force_refresh=True bypasses BOTH the in-mem cache AND the
        disk-cache fast path. Used by ``vermes config refresh`` and
        anywhere else the user explicitly asked for fresh data.
        """
        import agent.models_dev as md
        md._models_dev_cache = {}
        md._models_dev_cache_time = 0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REGISTRY
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        # Disk cache is fresh, but force_refresh must override it.
        with patch.object(md, "_disk_cache_age_seconds", return_value=60.0), \
             patch.object(md, "_load_disk_cache", return_value=SAMPLE_REGISTRY), \
             patch.object(md, "_save_disk_cache"):
            result = fetch_models_dev(force_refresh=True)

        mock_get.assert_called_once()
        assert "anthropic" in result

    @patch("agent.models_dev.requests.get")
    def test_missing_disk_cache_falls_through_to_network(self, mock_get):
        """If the disk cache file doesn't exist (first-ever run, or it
        was deleted), fall through cleanly to network."""
        import agent.models_dev as md
        md._models_dev_cache = {}
        md._models_dev_cache_time = 0

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REGISTRY
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with patch.object(md, "_disk_cache_age_seconds", return_value=None), \
             patch.object(md, "_save_disk_cache"):
            result = fetch_models_dev()

        mock_get.assert_called_once()
        assert "anthropic" in result


# ---------------------------------------------------------------------------
# get_model_capabilities — vision via modalities.input
# ---------------------------------------------------------------------------


CAPS_REGISTRY = {
    "google": {
        "id": "google",
        "models": {
            "gemma-4-31b-it": {
                "id": "gemma-4-31b-it",
                "attachment": False,
                "tool_call": True,
                "modalities": {"input": ["text", "image"]},
                "limit": {"context": 128000, "output": 8192},
            },
            "gemma-3-1b": {
                "id": "gemma-3-1b",
                "tool_call": True,
                "limit": {"context": 32000, "output": 8192},
            },
            "text-only-with-stale-attachment": {
                "id": "text-only-with-stale-attachment",
                "attachment": True,
                "tool_call": True,
                "modalities": {"input": ["text"]},
                "limit": {"context": 128000, "output": 8192},
            },
        },
    },
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-sonnet-4": {
                "id": "claude-sonnet-4",
                "attachment": True,
                "tool_call": True,
                "limit": {"context": 200000, "output": 64000},
            },
        },
    },
}


class TestGetModelCapabilities:
    """Tests for get_model_capabilities vision detection."""

    def test_vision_from_attachment_flag(self):
        """Models with attachment=True and no modalities should report supports_vision=True."""
        with patch("agent.models_dev.fetch_models_dev", return_value=CAPS_REGISTRY):
            caps = get_model_capabilities("anthropic", "claude-sonnet-4")
        assert caps is not None
        assert caps.supports_vision is True

    def test_vision_from_modalities_input_image(self):
        """Models with 'image' in modalities.input but attachment=False should
        still report supports_vision=True (the core fix in this PR)."""
        with patch("agent.models_dev.fetch_models_dev", return_value=CAPS_REGISTRY):
            caps = get_model_capabilities("google", "gemma-4-31b-it")
        assert caps is not None
        assert caps.supports_vision is True

    def test_text_only_modalities_override_stale_attachment_flag(self):
        """Text-only modalities must win over stale attachment=True metadata."""
        with patch("agent.models_dev.fetch_models_dev", return_value=CAPS_REGISTRY):
            caps = get_model_capabilities("google", "text-only-with-stale-attachment")
        assert caps is not None
        assert caps.supports_vision is False

    def test_no_vision_without_attachment_or_modalities(self):
        """Models with neither attachment nor image modality should be non-vision."""
        with patch("agent.models_dev.fetch_models_dev", return_value=CAPS_REGISTRY):
            caps = get_model_capabilities("google", "gemma-3-1b")
        assert caps is not None
        assert caps.supports_vision is False

    def test_modalities_non_dict_handled(self):
        """Non-dict modalities field should not crash."""
        registry = {
            "google": {"id": "google", "models": {
                "weird-model": {
                    "id": "weird-model",
                    "modalities": "text",  # not a dict
                    "limit": {"context": 200000, "output": 8192},
                },
            }},
        }
        with patch("agent.models_dev.fetch_models_dev", return_value=registry):
            caps = get_model_capabilities("gemini", "weird-model")
        assert caps is not None
        assert caps.supports_vision is False

    def test_model_not_found_returns_none(self):
        """Unknown model should return None."""
        with patch("agent.models_dev.fetch_models_dev", return_value=CAPS_REGISTRY):
            caps = get_model_capabilities("anthropic", "nonexistent-model")
        assert caps is None
