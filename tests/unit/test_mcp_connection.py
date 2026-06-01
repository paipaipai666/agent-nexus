"""Tests for MCP connection-level helpers."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.tools.mcp_connection import build_http_client_kwargs, ensure_sdk_available


def _make_config(**overrides):
    """Create a mock MCPServerConfig with sensible defaults."""
    defaults = dict(
        name="test_server",
        url="http://localhost:8080",
    )
    defaults.update(overrides)
    config = MagicMock()
    for k, v in defaults.items():
        setattr(config, k, v)
    return config


# ---------------------------------------------------------------------------
# build_http_client_kwargs
# ---------------------------------------------------------------------------

class TestBuildHttpClientKwargs:
    """Tests for the build_http_client_kwargs helper."""

    def test_factory_has_url_param(self):
        def factory(url, timeout=30):
            pass

        config = _make_config(url="http://example.com")
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert kwargs["url"] == "http://example.com"

    def test_factory_has_server_url_param(self):
        def factory(server_url, timeout=30):
            pass

        config = _make_config(url="http://example.com")
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert kwargs["server_url"] == "http://example.com"
        assert "url" not in kwargs

    def test_factory_has_http_client_param(self):
        def factory(url, http_client=None):
            pass

        config = _make_config()
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert kwargs["http_client"] is http_client

    def test_factory_lacks_http_client_param(self):
        def factory(url, timeout=30):
            pass

        config = _make_config()
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert "http_client" not in kwargs

    def test_factory_has_no_url_param_server_url_used_as_fallback(self):
        """When factory has no 'url' param, server_url is always set as fallback."""
        def factory(timeout=30):
            pass

        config = _make_config(url="http://fallback.com")
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert "url" not in kwargs
        assert kwargs["server_url"] == "http://fallback.com"

    def test_factory_with_kwargs_star(self):
        """Factory accepting **kwargs has no explicit 'url' param, so server_url is used."""
        def factory(**kwargs):
            pass

        config = _make_config(url="http://example.com")
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        # **kwargs does not expose 'url' in parameters, so server_url fallback
        assert kwargs.get("server_url") == "http://example.com"

    def test_factory_with_all_params(self):
        def factory(url, server_url=None, http_client=None, timeout=30):
            pass

        config = _make_config(url="http://full.com")
        http_client = MagicMock()
        kwargs = build_http_client_kwargs(factory, config, http_client)
        assert kwargs["url"] == "http://full.com"
        assert kwargs["http_client"] is http_client


# ---------------------------------------------------------------------------
# ensure_sdk_available
# ---------------------------------------------------------------------------

class TestEnsureSdkAvailable:
    """Tests for the ensure_sdk_available helper."""

    def test_sdk_installed_no_error(self):
        """When mcp is importable, no exception should be raised."""
        with patch.dict("sys.modules", {"mcp": MagicMock()}):
            # Should not raise
            ensure_sdk_available()

    def test_sdk_not_installed_raises_runtime_error(self):
        """When mcp cannot be imported, RuntimeError should be raised."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "mcp":
                raise ImportError("No module named 'mcp'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="MCP SDK is not installed"):
                ensure_sdk_available()
