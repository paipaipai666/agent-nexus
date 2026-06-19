"""Tests for agentnexus.server.auth."""

import pytest
from fastapi import HTTPException

import agentnexus.server.auth as auth_module
from agentnexus.server.auth import (
    generate_token,
    get_token,
    optional_verify,
    verify_api_key,
)


@pytest.fixture(autouse=True)
def _reset_token(mocker):
    """Reset _token to None before every test."""
    mocker.patch.object(auth_module, "_token", None)


class TestGenerateToken:
    def test_returns_nonempty_string(self):
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_stores_token_in_module(self):
        token = generate_token()
        assert auth_module._token == token

    def test_called_twice_overwrites_previous(self):
        first = generate_token()
        second = generate_token()
        assert second != first
        assert auth_module._token == second


class TestGetToken:
    def test_returns_none_before_generate(self):
        assert get_token() is None

    def test_returns_token_after_generate(self):
        token = generate_token()
        assert get_token() == token


class TestVerifyApiKey:
    def test_token_none_rejects_requests(self):
        # _token is None (reset by fixture), should reject (auth not initialized)
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("anything")
        assert exc_info.value.status_code == 503

        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None)
        assert exc_info.value.status_code == 503

    def test_correct_key_passes(self):
        token = generate_token()
        verify_api_key(token)

    def test_wrong_key_raises_401(self):
        generate_token()
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("wrong-key")
        assert exc_info.value.status_code == 401

    def test_none_key_when_token_set_raises_401(self):
        generate_token()
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key(None)
        assert exc_info.value.status_code == 401

    def test_empty_string_key_when_token_set_raises_401(self):
        generate_token()
        with pytest.raises(HTTPException) as exc_info:
            verify_api_key("")
        assert exc_info.value.status_code == 401


class TestOptionalVerify:
    def _make_request(self, path: str, api_key: str | None = None):
        """Create a mock request with the given path and optional X-API-Key header."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.url.path = path
        headers = {}
        if api_key is not None:
            headers["X-API-Key"] = api_key
        request.headers = headers
        return request

    def test_token_none_rejects_non_health_paths(self):
        request = self._make_request("/api/something")
        with pytest.raises(HTTPException) as exc_info:
            optional_verify(request)
        assert exc_info.value.status_code == 503

    def test_token_none_allows_health_path(self):
        request = self._make_request("/health")
        optional_verify(request)  # health is always allowed

    def test_health_path_passes_with_wrong_key(self):
        generate_token()
        request = self._make_request("/health", api_key="wrong")
        optional_verify(request)  # should not raise

    def test_docs_path_passes(self):
        generate_token()
        request = self._make_request("/docs")
        optional_verify(request)

    def test_openapi_json_path_passes(self):
        generate_token()
        request = self._make_request("/openapi.json", api_key="wrong")
        optional_verify(request)

    def test_other_path_requires_valid_key(self):
        token = generate_token()
        request = self._make_request("/api/data", api_key=token)
        optional_verify(request)  # should not raise

    def test_other_path_wrong_key_raises_401(self):
        generate_token()
        request = self._make_request("/api/data", api_key="wrong")
        with pytest.raises(HTTPException) as exc_info:
            optional_verify(request)
        assert exc_info.value.status_code == 401

    def test_other_path_no_key_raises_401(self):
        generate_token()
        request = self._make_request("/api/data")
        with pytest.raises(HTTPException) as exc_info:
            optional_verify(request)
        assert exc_info.value.status_code == 401
