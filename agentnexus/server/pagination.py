"""Pagination helpers for API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Query
from pydantic import BaseModel


class PageParams(BaseModel):
    offset: int = 0
    limit: int = 20


def page_params(
    offset: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int = Query(20, ge=1, le=100, description="Max items to return"),
) -> PageParams:
    return PageParams(offset=offset, limit=limit)


def paginate(items: list[Any], params: PageParams) -> dict[str, Any]:
    """Slice a list and return paginated response."""
    total = len(items)
    page = items[params.offset : params.offset + params.limit]
    return {
        "items": page,
        "total": total,
        "offset": params.offset,
        "limit": params.limit,
        "has_more": params.offset + params.limit < total,
    }
