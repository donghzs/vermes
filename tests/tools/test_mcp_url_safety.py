"""Tests for MCP tool URL safety gate — SSRF / secret-in-URL prevention.

The gate sits in ``_check_mcp_url_safety`` and runs before *every* MCP
tool call, regardless of which server or tool is involved.  This ensures
third-party MCP servers get the same SSRF protection as built-in web tools
(``web_extract``, ``web_search``).
"""

import pytest

from tools.mcp_tool import (
    _extract_urls_from_args,
    _check_mcp_url_safety,
)


# ---------------------------------------------------------------------------
# URL extraction from tool arguments
# ---------------------------------------------------------------------------

class TestExtractUrls:
    def test_known_url_param(self):
        urls = _extract_urls_from_args({"url": "https://example.com"})
        assert urls == ["https://example.com"]

    def test_known_urls_param(self):
        urls = _extract_urls_from_args({"urls": "https://example.com"})
        assert urls == ["https://example.com"]

    def test_any_http_string(self):
        urls = _extract_urls_from_args({"target": "http://example.com/page"})
        assert urls == ["http://example.com/page"]

    def test_non_url_string_ignored(self):
        urls = _extract_urls_from_args({"query": "hello world"})
        assert urls == []

    def test_non_string_value_ignored(self):
        urls = _extract_urls_from_args({"url": 12345, "limit": 10})
        assert urls == []

    def test_empty_args(self):
        assert _extract_urls_from_args({}) == []

    def test_none_args(self):
        assert _extract_urls_from_args(None) == []  # type: ignore[arg-type]

    def test_multiple_urls(self):
        urls = _extract_urls_from_args({
            "url": "https://a.com",
            "callback_url": "https://b.com",
        })
        assert len(urls) == 2

    def test_case_insensitive_param_name(self):
        urls = _extract_urls_from_args({"URL": "https://example.com"})
        assert urls == ["https://example.com"]


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

class TestSSRFProtection:
    def test_blocks_cloud_metadata(self):
        result = _check_mcp_url_safety(
            {"url": "http://169.254.169.254/latest/meta-data/"},
            "test_server", "test_tool",
        )
        assert result is not None
        assert "private or internal" in result

    def test_blocks_localhost(self):
        result = _check_mcp_url_safety(
            {"url": "http://localhost:8080/admin"},
            "test_server", "test_tool",
        )
        assert result is not None

    def test_blocks_private_ip_192(self):
        result = _check_mcp_url_safety(
            {"url": "http://192.168.1.1/"},
            "test_server", "test_tool",
        )
        assert result is not None

    def test_blocks_private_ip_10(self):
        result = _check_mcp_url_safety(
            {"url": "http://10.0.0.1/"},
            "test_server", "test_tool",
        )
        assert result is not None

    def test_blocks_private_ip_172(self):
        result = _check_mcp_url_safety(
            {"url": "http://172.16.0.1/"},
            "test_server", "test_tool",
        )
        assert result is not None

    def test_allows_public_url(self):
        result = _check_mcp_url_safety(
            {"url": "https://example.com/article"},
            "test_server", "test_tool",
        )
        assert result is None

    def test_no_urls_returns_none(self):
        result = _check_mcp_url_safety(
            {"query": "hello", "limit": 5},
            "test_server", "test_tool",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Secret-in-URL protection
# ---------------------------------------------------------------------------

class TestSecretInURL:
    def test_blocks_api_key_in_url(self):
        result = _check_mcp_url_safety(
            {"url": "https://example.com/?key=sk-proj-abcdef1234567890abcdef"},
            "test_server", "test_tool",
        )
        assert result is not None
        assert "API key" in result or "token" in result

    def test_blocks_percent_encoded_secret(self):
        result = _check_mcp_url_safety(
            {"url": "https://example.com/?key=%73k-proj-abcdef1234567890"},
            "test_server", "test_tool",
        )
        assert result is not None

    def test_allows_url_without_secrets(self):
        result = _check_mcp_url_safety(
            {"url": "https://example.com/path?foo=bar&page=1"},
            "test_server", "test_tool",
        )
        assert result is None
