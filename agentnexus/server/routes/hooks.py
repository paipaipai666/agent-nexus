"""Command hooks inspection and trust management API."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["hooks"])


@router.get("")
def list_hooks() -> dict:
    """All discovered command hooks incl. trust status."""
    from agentnexus.core.hook_sources import (
        discover_hooks,
        load_trusted_fingerprints,
    )
    from agentnexus.core.hooks import read_hook_journal

    loaded, errors = discover_hooks(include_untrusted=True)
    return {
        "hooks": [
            {
                "source": item.source,
                "source_path": str(item.source_path),
                "event": item.config.event,
                "matcher": item.config.matcher,
                "command": item.config.command,
                "timeout": item.config.timeout,
                "async": item.config.async_,
                "enabled": item.config.enabled,
                "on_failure": item.config.on_failure,
                "fingerprint": item.fingerprint,
                "trusted": item.trusted,
            }
            for item in loaded
        ],
        "trusted_fingerprints": sorted(load_trusted_fingerprints()),
        "journal": read_hook_journal(50),
        "errors": errors,
    }


class TrustRequest(BaseModel):
    fingerprint: str
    action: str  # "approve" | "revoke"


@router.post("/trust")
def update_trust(request: TrustRequest) -> dict:
    """Approve or revoke trust for a project hook fingerprint."""
    from agentnexus.core.hook_sources import (
        approve_fingerprint,
        load_trusted_fingerprints,
        revoke_fingerprint,
    )

    if request.action == "approve":
        approve_fingerprint(request.fingerprint)
    elif request.action == "revoke":
        revoke_fingerprint(request.fingerprint)
    else:
        return {"error": f"unknown action {request.action!r}", "trusted": sorted(load_trusted_fingerprints())}
    return {"ok": True, "trusted": sorted(load_trusted_fingerprints())}
