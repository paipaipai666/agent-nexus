"""Local token authentication for the API server."""

from __future__ import annotations

import secrets

_token: str | None = None


def generate_token() -> str:
    """Generate and store a new API token. Call once on server startup."""
    global _token
    _token = secrets.token_urlsafe(32)
    return _token


def get_token() -> str | None:
    return _token
