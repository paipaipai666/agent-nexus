"""Skills API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import APIError

router = APIRouter(tags=["skills"])


def get_skill_service(session_id: str | None = None, runtime=Depends(get_runtime)):
    """Resolve the default skill catalog or a session's workspace catalog."""
    if session_id is None:
        service = runtime.skill
    else:
        try:
            service = runtime.chat.session_skills(session_id)
        except KeyError:
            raise APIError(404, "not_found", f"Session '{session_id}' not found") from None
    if service is None:
        raise APIError(503, "unavailable", "Skill service is unavailable")
    return service


class UseSkillRequest(BaseModel):
    skill_id: str


class RecommendRequest(BaseModel):
    text: str


@router.get("")
def list_skills(service=Depends(get_skill_service)):
    entries = service.list()
    return {
        "skills": [
            {
                "id": e.qualified_id,
                "namespace": e.namespace,
                "workflow_id": e.workflow_id,
                "display_name": e.display_name,
                "description": e.description,
                "source_kind": e.source_kind,
                "enabled": service.is_enabled(e),
            }
            for e in entries
        ],
        "count": len(entries),
    }


@router.get("/status")
def skill_status(service=Depends(get_skill_service)):
    snapshot = service.snapshot()
    if hasattr(snapshot, "__dict__"):
        return snapshot.__dict__
    return snapshot


@router.post("/use")
def use_skill(req: UseSkillRequest, service=Depends(get_skill_service)):
    try:
        entry = service.use(req.skill_id)
        return {
            "id": entry.qualified_id,
            "display_name": entry.display_name,
            "description": entry.description,
        }
    except ValueError as e:
        raise APIError(404, "not_found", str(e))


@router.post("/reset")
def reset_skill(service=Depends(get_skill_service)):
    service.reset()
    return {"status": "reset"}


@router.post("/validate")
def validate_skills(skill_id: str | None = None, service=Depends(get_skill_service)):
    errors = service.validate(target=skill_id)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/refresh")
def refresh_skills(service=Depends(get_skill_service)):
    entries = service.refresh()
    return {"count": len(entries), "refreshed": True}


class BulkToggleRequest(BaseModel):
    enabled: dict[str, bool]


@router.post("/bulk-toggle")
def bulk_toggle_skills(req: BulkToggleRequest, service=Depends(get_skill_service)):
    service.set_enabled_map(req.enabled)
    return {"status": "updated", "count": len(req.enabled)}


@router.get("/context")
def get_skill_context(query: str | None = None, service=Depends(get_skill_service)):
    """Skill catalog block for prompts.

    When ``query`` is set, rank skills for that text and inject a budget-bounded
    shortlist (same path as chat). Without query, returns the unranked catalog.
    """
    recommendations = None
    if query and query.strip():
        recommendations = service.get_recommendations(query) or None
    context = service.available_skill_context(recommendations=recommendations)
    payload: dict = {"context": context}
    if recommendations:
        payload["recommendations"] = [
            {
                "skill_id": r.entry.qualified_id,
                "display_name": r.entry.display_name,
                "score": r.score,
                "reason": r.reason,
                "source": r.source,
            }
            for r in recommendations
        ]
    return payload


@router.post("/recommend")
def recommend_skill(req: RecommendRequest, service=Depends(get_skill_service)):
    routes = service.get_recommendations(req.text)
    return {
        "recommendations": [
            {
                "skill_id": r.entry.qualified_id,
                "display_name": r.entry.display_name,
                "score": r.score,
                "reason": r.reason,
                "source": r.source,
            }
            for r in routes
        ]
    }


@router.post("/{skill_id:path}/enable")
def enable_skill(skill_id: str, service=Depends(get_skill_service)):
    service.set_enabled(skill_id, True)
    return {"status": "enabled", "skill_id": skill_id}


@router.post("/{skill_id:path}/disable")
def disable_skill(skill_id: str, service=Depends(get_skill_service)):
    service.set_enabled(skill_id, False)
    return {"status": "disabled", "skill_id": skill_id}


@router.get("/{skill_id:path}")
def get_skill(skill_id: str, service=Depends(get_skill_service)):
    try:
        entry = service.registry.get(skill_id)
    except ValueError as exc:
        raise APIError(409, "conflict", str(exc)) from exc
    if entry is None:
        raise APIError(404, "not_found", f"Skill '{skill_id}' not found")
    return {
        "id": entry.qualified_id,
        "namespace": entry.namespace,
        "workflow_id": entry.workflow_id,
        "display_name": entry.display_name,
        "description": entry.description,
        "source_kind": entry.source_kind,
        "enabled": service.is_enabled(entry),
    }
