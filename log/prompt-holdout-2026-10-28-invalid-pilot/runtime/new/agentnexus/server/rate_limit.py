"""Simple in-memory rate limiter for the API server."""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.rpm = requests_per_minute
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.time()
        window_start = now - 60
        self._requests[key] = [t for t in self._requests[key] if t > window_start]
        if len(self._requests[key]) >= self.rpm:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        self._requests[key].append(now)


_global_limiter = RateLimiter(requests_per_minute=120)
_chat_limiter = RateLimiter(requests_per_minute=30)


def rate_limit_global(request: Request) -> None:
    _global_limiter.check(request.client.host if request.client else "unknown")


def rate_limit_chat(request: Request) -> None:
    _chat_limiter.check(request.client.host if request.client else "unknown")
