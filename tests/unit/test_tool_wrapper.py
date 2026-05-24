"""Tests for agentnexus.tools.tool_wrapper."""

from unittest.mock import MagicMock

import pytest

from agentnexus.tools.tool_wrapper import (
    FALLBACK_REGISTRY,
    _error_aware_fallback,
    _summarise,
    fallback_api_cache,
    fallback_ascii_chart,
    fallback_bleu_simple,
    safe_call,
    safe_call_with_registry,
)


class TestFallbackAsciiChart:
    def test_empty_data(self):
        result = fallback_ascii_chart({}, "Test")
        assert "(no data)" in result

    def test_with_data(self):
        result = fallback_ascii_chart({"a": 10, "b": 5}, "Chart")
        assert "a" in result
        assert "10.00" in result
        assert "Chart" in result


class TestFallbackBleuSimple:
    def test_exact_match(self):
        result = fallback_bleu_simple("hello world", "hello world")
        assert result["bleu"] == 1.0

    def test_partial_match_subset_ref(self):
        result = fallback_bleu_simple("hello world test full", "hello world test")
        assert result["bleu"] > 0.0
        assert "precisions" in result
        assert "brevity_penalty" in result


class TestFallbackApiCache:
    def test_returns_canned_response(self):
        result = fallback_api_cache()
        assert "[fallback]" in result


class TestSafeCall:
    def test_success_returns_result(self):
        fn = MagicMock(return_value=42)
        assert safe_call(fn) == 42
        fn.assert_called_once()

    def test_failure_with_fallback(self):
        fn = MagicMock(side_effect=ValueError("fail"))
        fallback = MagicMock(return_value="recovered")
        result = safe_call(fn, fallback, 1, 2)
        assert result == "recovered"
        fallback.assert_called_once_with(1, 2)

    def test_failure_without_fallback_raises(self):
        fn = MagicMock(side_effect=ValueError("fail"))
        with pytest.raises(ValueError):
            safe_call(fn)

    def test_fallback_also_fails_raises_runtime(self):
        fn = MagicMock(side_effect=ValueError("original"))
        fallback = MagicMock(side_effect=TypeError("fallback also failed"))
        with pytest.raises(RuntimeError, match="Primary call failed"):
            safe_call(fn, fallback)


class TestSafeCallWithRegistry:
    def test_with_valid_registry_key(self):
        fn = MagicMock(return_value=42)
        result = safe_call_with_registry("api", fn)
        assert result == 42

    def test_with_invalid_registry_key_no_fallback(self):
        fn = MagicMock(side_effect=ValueError("fail"))
        result = safe_call_with_registry("nonexistent", fn)
        assert "fallback" in result

    def test_fallback_is_used_when_fn_fails(self):
        fn = MagicMock(side_effect=ValueError("fail"))
        result = safe_call_with_registry("api", fn)
        assert "fallback" in result


class TestSummarise:
    def test_short_value(self):
        assert _summarise("hello") == "'hello'"

    def test_long_value_truncated(self):
        long_str = "x" * 20000
        result = _summarise(long_str, max_len=10)
        assert len(result) == 13
        assert result.endswith("...")


class TestErrorAwareFallback:
    def test_fallback_succeeds(self):
        fn = MagicMock(return_value="ok")
        result = _error_aware_fallback(fn, ValueError("original"))
        assert result == "ok"

    def test_both_fail(self):
        fn = MagicMock(side_effect=TypeError("fallback error"))
        with pytest.raises(RuntimeError):
            _error_aware_fallback(fn, ValueError("original"))


class TestRegistryContents:
    def test_has_expected_keys(self):
        assert "matplotlib" in FALLBACK_REGISTRY
        assert "nltk" in FALLBACK_REGISTRY
        assert "api" in FALLBACK_REGISTRY
