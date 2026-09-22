"""Tests for agentnexus.server.auth."""

import pytest

import agentnexus.server.auth as auth_module
from agentnexus.server.auth import (
    generate_token,
    get_token,
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


