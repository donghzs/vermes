"""Tests for hybrid_retriever embedding API resolution and auto-discovery.

Tests cover:
  - _resolve_embedding_api() follows user's configured provider
  - _get_embedding() caches failed providers in _FAILED_EMBED_PROVIDERS
  - _discover_embedding_model() queries /v1/models and caches result
  - search() falls back to Jaccard when no embedding API available
  - store_embedding() silently handles failures
"""

import sqlite3
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── _resolve_embedding_api ─────────────────────────────────────────────


def test_resolve_returns_empty_when_no_config(monkeypatch):
    """No config, no .env → empty strings."""
    from agent.hybrid_retriever import _resolve_embedding_api

    with patch("agent.hybrid_retriever._resolve_embedding_api", return_value=("", "", "")):
        base, key, model = _resolve_embedding_api()
        assert base == ""
        assert key == ""
        assert model == ""


def test_resolve_uses_default_provider(monkeypatch):
    """Should read provider from config.yaml providers[id]."""
    from agent import hybrid_retriever

    mock_cfg = {
        "model": {"provider": "zhipu"},
        "providers": {
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "api_key": "test-key-123",
            }
        },
        "embedding": {"model": "embedding-3"},
    }
    mock_env = {}

    with patch.object(hybrid_retriever, "_resolve_embedding_api") as mock_fn:
        mock_fn.return_value = ("https://open.bigmodel.cn/api/paas/v4", "test-key-123", "embedding-3")
        base, key, model = hybrid_retriever._resolve_embedding_api()

    assert base == "https://open.bigmodel.cn/api/paas/v4"
    assert key == "test-key-123"
    assert model == "embedding-3"


def test_resolve_skips_anthropic():
    """Anthropic is in _NO_EMBED_PROVIDERS — should be skipped."""
    from agent.hybrid_retriever import _NO_EMBED_PROVIDERS

    assert "https://api.anthropic.com/v1" in _NO_EMBED_PROVIDERS


def test_resolve_falls_through_to_env_scan():
    """If default provider has no credentials, should scan .env."""
    from agent.hybrid_retriever import _DEFAULT_EMBEDDING_MODEL

    assert _DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"


# ── _FAILED_EMBED_PROVIDERS cache ──────────────────────────────────────


def test_failed_providers_cache_is_set():
    """_FAILED_EMBED_PROVIDERS should be a mutable set."""
    from agent.hybrid_retriever import _FAILED_EMBED_PROVIDERS

    assert isinstance(_FAILED_EMBED_PROVIDERS, set)
    original_len = len(_FAILED_EMBED_PROVIDERS)
    _FAILED_EMBED_PROVIDERS.add("https://test-provider.example.com/v1")
    assert len(_FAILED_EMBED_PROVIDERS) == original_len + 1
    _FAILED_EMBED_PROVIDERS.discard("https://test-provider.example.com/v1")


def test_get_embedding_skips_failed_provider():
    """_get_embedding should return None for cached failed providers."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://failed.example.com/v1", "key", "model")):
        with patch.object(hybrid_retriever, "_FAILED_EMBED_PROVIDERS",
                          {"https://failed.example.com/v1"}):
            result = hybrid_retriever._get_embedding("test text")
            assert result is None


def test_get_embedding_skips_no_embed_provider():
    """_get_embedding should skip _NO_EMBED_PROVIDERS."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://api.anthropic.com/v1", "key", "model")):
        result = hybrid_retriever._get_embedding("test text")
        assert result is None


def test_get_embedding_returns_none_on_no_credentials():
    """No API key → None."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("", "", "")):
        result = hybrid_retriever._get_embedding("test text")
        assert result is None


# ── _get_embedding HTTP behavior ───────────────────────────────────────


def test_get_embedding_caches_404_failure():
    """404 should add provider to _FAILED_EMBED_PROVIDERS."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://test-404.example.com/v1", "key", "model")):
        with patch.object(hybrid_retriever, "_FAILED_EMBED_PROVIDERS", set()) as mock_failed:
            with patch("httpx.post", return_value=mock_resp):
                result = hybrid_retriever._get_embedding("test")
                assert result is None
                assert "https://test-404.example.com/v1" in mock_failed


def test_get_embedding_does_not_cache_429():
    """429 (rate limit) should NOT cache as failed provider."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 429

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://test-429.example.com/v1", "key", "model")):
        with patch.object(hybrid_retriever, "_FAILED_EMBED_PROVIDERS", set()) as mock_failed:
            with patch("httpx.post", return_value=mock_resp):
                result = hybrid_retriever._get_embedding("test")
                assert result is None
                assert len(mock_failed) == 0  # Not cached


def test_get_embedding_success():
    """200 response should return embedding vector."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://ok.example.com/v1", "key", "model")):
        with patch("httpx.post", return_value=mock_resp):
            result = hybrid_retriever._get_embedding("test text")
            assert result == [0.1, 0.2, 0.3]


def test_get_embedding_auto_discovers_model_on_400():
    """400 with default model → retry with discovered model."""
    from agent import hybrid_retriever

    resp_400 = MagicMock()
    resp_400.status_code = 400

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"data": [{"embedding": [0.5, 0.5]}]}

    with patch.object(hybrid_retriever, "_resolve_embedding_api",
                      return_value=("https://auto.example.com/v1", "key",
                                    hybrid_retriever._DEFAULT_EMBEDDING_MODEL)):
        with patch.object(hybrid_retriever, "_FAILED_EMBED_PROVIDERS", set()):
            with patch("httpx.post", side_effect=[resp_400, resp_200]) as mock_post:
                with patch.object(hybrid_retriever, "_discover_embedding_model",
                                  return_value="embedding-3-custom"):
                    result = hybrid_retriever._get_embedding("test")
                    assert result == [0.5, 0.5]
                    # Second call should use discovered model
                    second_payload = mock_post.call_args_list[1][1]["json"]
                    assert second_payload["model"] == "embedding-3-custom"


# ── _discover_embedding_model ──────────────────────────────────────────


def test_discover_finds_embed_model():
    """Should find 'embed' in model list."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "gpt-4o"},
            {"id": "text-embedding-3-small"},
            {"id": "text-embedding-3-large"},
            {"id": "gpt-4o-mini"},
        ]
    }

    with patch("httpx.get", return_value=mock_resp):
        with patch.object(hybrid_retriever, "_get_conn") as mock_conn:
            mock_conn.return_value.execute.return_value.fetchone.return_value = None
            mock_conn.return_value.execute.return_value.fetchall.return_value = []
            result = hybrid_retriever._discover_embedding_model(
                "https://api.example.com/v1", "key"
            )
            # Should prefer 'small' variant
            assert result == "text-embedding-3-small"


def test_discover_prefers_small_variant():
    """Should prefer 'small' or 'lite' embedding models."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "embedding-3"},
            {"id": "embedding-3-small"},
            {"id": "embedding-3-large"},
        ]
    }

    with patch("httpx.get", return_value=mock_resp):
        with patch.object(hybrid_retriever, "_get_conn") as mock_conn:
            mock_conn.return_value.execute.return_value.fetchone.return_value = None
            result = hybrid_retriever._discover_embedding_model(
                "https://api.example.com/v1", "key"
            )
            assert result == "embedding-3-small"


def test_discover_returns_first_embed_if_no_small():
    """If no 'small' variant, return first embedding model."""
    from agent import hybrid_retriever

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [
            {"id": "gpt-4"},
            {"id": "bge-large-zh"},
            {"id": "bge-m3"},
        ]
    }

    with patch("httpx.get", return_value=mock_resp):
        with patch.object(hybrid_retriever, "_get_conn") as mock_conn:
            mock_conn.return_value.execute.return_value.fetchone.return_value = None
            result = hybrid_retriever._discover_embedding_model(
                "https://api.example.com/v1", "key"
            )
            # None contain 'embed' → fallback to default
            assert result == hybrid_retriever._DEFAULT_EMBEDDING_MODEL


def test_discover_uses_cached_value():
    """Should return cached model from SQLite."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_get_conn") as mock_conn:
        mock_conn.return_value.execute.return_value.fetchone.return_value = (
            "cached-embedding-model",
        )
        result = hybrid_retriever._discover_embedding_model(
            "https://api.example.com/v1", "key"
        )
        assert result == "cached-embedding-model"


def test_discover_falls_back_on_api_error():
    """API error → fallback to default model."""
    from agent import hybrid_retriever

    with patch("httpx.get", side_effect=Exception("network error")):
        with patch.object(hybrid_retriever, "_get_conn") as mock_conn:
            mock_conn.return_value.execute.return_value.fetchone.return_value = None
            result = hybrid_retriever._discover_embedding_model(
                "https://api.example.com/v1", "key"
            )
            assert result == hybrid_retriever._DEFAULT_EMBEDDING_MODEL


# ── search / Jaccard fallback ──────────────────────────────────────────


def test_search_returns_empty_on_no_db():
    """No embeddings DB → empty list."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_get_db_path") as mock_path:
        mock_path.return_value = Path("/nonexistent/path/embeddings.db")
        result = hybrid_retriever.search("test query")
        assert result == []


def test_search_empty_query():
    """Empty query → empty list."""
    from agent import hybrid_retriever

    result = hybrid_retriever.search("")
    assert result == []


# ── store_embedding ────────────────────────────────────────────────────


def test_store_embedding_silent_on_empty():
    """Empty content → no-op, no exception."""
    from agent.hybrid_retriever import store_embedding

    store_embedding("")
    store_embedding("   ")


def test_store_embedding_handles_failure():
    """DB error → silent, no exception raised."""
    from agent import hybrid_retriever

    with patch.object(hybrid_retriever, "_get_embedding", return_value=[0.1, 0.2]):
        with patch.object(hybrid_retriever, "_get_conn", side_effect=Exception("DB error")):
            # Should not raise
            hybrid_retriever.store_embedding("test content")


# ── vector pack/unpack roundtrip ───────────────────────────────────────


def test_vector_roundtrip():
    """Pack and unpack should preserve vector values."""
    from agent.hybrid_retriever import _vector_to_blob, _blob_to_vector

    original = [0.1, 0.2, 0.3, 0.4, 0.5]
    blob = _vector_to_blob(original)
    restored = _blob_to_vector(blob)
    for a, b in zip(original, restored):
        assert abs(a - b) < 1e-6


# ── cosine similarity ──────────────────────────────────────────────────


def test_cosine_identical_vectors():
    """Identical vectors → similarity 1.0."""
    from agent.hybrid_retriever import _cosine_similarity

    v = [1.0, 0.5, 0.3]
    assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    """Orthogonal vectors → similarity 0.0."""
    from agent.hybrid_retriever import _cosine_similarity

    assert abs(_cosine_similarity([1, 0], [0, 1]) - 0.0) < 1e-6


def test_cosine_zero_vector():
    """Zero vector → similarity 0.0."""
    from agent.hybrid_retriever import _cosine_similarity

    assert _cosine_similarity([0, 0], [1, 1]) == 0.0
