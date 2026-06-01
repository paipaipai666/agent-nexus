"""Tests for agentnexus.cli.eval.common: formatting and detection helpers."""

import pytest

from agentnexus.cli.eval.common import (
    _detect_embedding_device,
    _endpoint_mode,
    _fmt_ci,
    _fmt_pct,
    _text_setting,
)


class TestFmtCi:
    """Tests for _fmt_ci."""

    def test_valid_ci_tuple(self):
        assert _fmt_ci(0.5, (0.45, 0.55)) == "0.500 [0.45-0.55]"

    def test_ci_none(self):
        assert _fmt_ci(0.5, None) == "0.500"

    def test_ci_length_one(self):
        assert _fmt_ci(0.5, (1.0,)) == "0.500"

    def test_ci_empty_tuple(self):
        assert _fmt_ci(0.5, ()) == "0.500"

    def test_default_ci_param(self):
        assert _fmt_ci(0.5) == "0.500"


class TestFmtPct:
    """Tests for _fmt_pct."""

    def test_half(self):
        assert _fmt_pct(0.5) == "50.0%"

    def test_zero(self):
        assert _fmt_pct(0.0) == "0.0%"

    def test_one(self):
        assert _fmt_pct(1.0) == "100.0%"

    def test_fraction(self):
        assert _fmt_pct(0.123) == "12.3%"


class TestTextSetting:
    """Tests for _text_setting."""

    def test_normal_string(self):
        assert _text_setting("hello") == "hello"

    def test_none_returns_default(self):
        assert _text_setting(None) == "unknown"

    def test_empty_string_returns_default(self):
        assert _text_setting("") == "unknown"

    def test_strips_whitespace(self):
        assert _text_setting("  foo  ") == "foo"

    def test_whitespace_only_returns_default(self):
        assert _text_setting("  ") == "unknown"

    def test_non_string_with_custom_default(self):
        assert _text_setting(123, "custom") == "custom"

    def test_none_with_custom_default(self):
        assert _text_setting(None, "fallback") == "fallback"

    def test_non_string_returns_default(self):
        assert _text_setting(123) == "unknown"


class TestEndpointMode:
    """Tests for _endpoint_mode."""

    def test_localhost(self):
        assert _endpoint_mode("http://localhost:8080") == "local"

    def test_loopback_ip(self):
        assert _endpoint_mode("http://127.0.0.1:5000") == "local"

    def test_ipv6_loopback(self):
        assert _endpoint_mode("http://[::1]:3000") == "local"

    def test_remote_host(self):
        assert _endpoint_mode("https://api.example.com") == "remote"

    def test_empty_string(self):
        assert _endpoint_mode("") == "unknown"

    def test_private_ip_is_remote(self):
        assert _endpoint_mode("http://10.0.0.1:8080") == "remote"


class TestDetectEmbeddingDevice:
    """Tests for _detect_embedding_device."""

    def test_success(self, mocker):
        mock_resolve = mocker.patch(
            "agentnexus.rag.embeddings.resolve_embedding_device",
            return_value="cuda",
        )
        result = _detect_embedding_device()
        assert result == "cuda"
        mock_resolve.assert_called_once()

    def test_failure_returns_cpu(self, mocker):
        mocker.patch(
            "agentnexus.rag.embeddings.resolve_embedding_device",
            side_effect=ImportError("no module"),
        )
        result = _detect_embedding_device()
        assert result == "cpu"
