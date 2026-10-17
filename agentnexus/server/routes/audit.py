"""Audit log API routes."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["audit"])


@router.get("")
def list_audit_entries(tool: str | None = None, limit: int = 50):
    """Read audit entries using the shared public snapshot operation."""
    from agentnexus.services.inspection import list_audit_entries as inspect_audit

    return inspect_audit(tool, limit)
