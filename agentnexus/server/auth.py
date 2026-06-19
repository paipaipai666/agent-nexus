"""Local token authentication for the API server."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_token: str | None = None


def generate_token() -> str:
    """Generate and store a new API token. Call once on server startup."""
    global _token
    _token = secrets.token_urlsafe(32)
    return _token


def rotate_token() -> str:
    """Generate a new API token, replacing the current one. Returns the new token."""
    global _token
    _token = secrets.token_urlsafe(32)
    return _token


def get_token() -> str | None:
    return _token


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Dependency that verifies the X-API-Key header."""
    if _token is None:
        raise HTTPException(status_code=503, detail="Authentication not initialized")
    if api_key is None or not secrets.compare_digest(api_key, _token):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def optional_verify(request: Request) -> None:
    """Optional auth — skip for health endpoint, require for everything else."""
    if request.url.path in ("/health", "/docs", "/openapi.json"):
        return
    if _token is None:
        raise HTTPException(status_code=503, detail="Authentication not initialized")
    api_key = request.headers.get("X-API-Key")
    if api_key is None or not secrets.compare_digest(api_key, _token):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
