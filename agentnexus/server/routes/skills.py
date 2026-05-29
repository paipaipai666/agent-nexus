"""Skills API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["skills"])


class UseSkillRequest(BaseModel):
    skill_id: str


class RecommendRequest(BaseModel):
    text: str


@router.get("")
def list_skills():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    service = runtime.services.skill
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
def skill_status():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    snapshot = runtime.services.skill.snapshot()
    if hasattr(snapshot, "__dict__"):
        return snapshot.__dict__
    return snapshot


@router.post("/use")
def use_skill(req: UseSkillRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    try:
        entry = runtime.services.skill.use(req.skill_id)
        return {
            "id": entry.qualified_id,
            "display_name": entry.display_name,
            "description": entry.description,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/reset")
def reset_skill():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.skill.reset()
    return {"status": "reset"}


@router.get("/{skill_id}")
def get_skill(skill_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    service = runtime.services.skill
    entry = service.registry.get(skill_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_id}' not found")
    return {
        "id": entry.qualified_id,
        "namespace": entry.namespace,
        "workflow_id": entry.workflow_id,
        "display_name": entry.display_name,
        "description": entry.description,
        "source_kind": entry.source_kind,
        "enabled": service.is_enabled(entry),
    }


@router.post("/{skill_id}/enable")
def enable_skill(skill_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.skill.set_enabled(skill_id, True)
    return {"status": "enabled", "skill_id": skill_id}


@router.post("/{skill_id}/disable")
def disable_skill(skill_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.skill.set_enabled(skill_id, False)
    return {"status": "disabled", "skill_id": skill_id}


@router.post("/validate")
def validate_skills(skill_id: str | None = None):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    errors = runtime.services.skill.validate(target=skill_id)
    return {"valid": len(errors) == 0, "errors": errors}


@router.post("/refresh")
def refresh_skills():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    entries = runtime.services.skill.refresh()
    return {"count": len(entries), "refreshed": True}


class BulkToggleRequest(BaseModel):
    enabled: dict[str, bool]


@router.post("/bulk-toggle")
def bulk_toggle_skills(req: BulkToggleRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.skill.set_enabled_map(req.enabled)
    return {"status": "updated", "count": len(req.enabled)}


@router.get("/context")
def get_skill_context():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    context = runtime.services.skill.available_skill_context()
    return {"context": context}


@router.post("/recommend")
def recommend_skill(req: RecommendRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    routes = runtime.services.skill.get_recommendations(req.text)
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
