"""Version control (checkpoint) and runtime status API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["version", "runtime"])


def _get_version_manager():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    vm = runtime.version_manager
    if vm is None:
        raise HTTPException(status_code=404, detail="Version manager not initialized")
    return vm


@router.get("/status")
def version_status():
    vm = _get_version_manager()
    return vm.status()


@router.get("/log")
def version_log(limit: int = 10):
    vm = _get_version_manager()
    checkpoints = vm.log()
    return {"checkpoints": checkpoints[:limit], "total": len(checkpoints)}


@router.post("/undo")
def version_undo():
    vm = _get_version_manager()
    result = vm.undo()
    if result is None:
        raise HTTPException(status_code=400, detail="Nothing to undo")
    # Restore short-term memory from checkpoint snapshot
    try:
        from agentnexus.server.app import _get_runtime

        runtime = _get_runtime()
        snapshot = result.get("stm_snapshot", "")
        if snapshot:
            from agentnexus.memory.short_term import ShortTermMemory
            restored = ShortTermMemory.from_json(snapshot)
            runtime.memory_manager.short_term._messages = restored._messages
            runtime.memory_manager.short_term._summary = restored._summary
    except Exception:
        pass
    return {"status": "undone", "checkpoint": result}


@router.post("/redo")
def version_redo():
    vm = _get_version_manager()
    result = vm.redo()
    if result is None:
        raise HTTPException(status_code=400, detail="Nothing to redo")
    try:
        from agentnexus.server.app import _get_runtime

        runtime = _get_runtime()
        snapshot = result.get("stm_snapshot", "")
        if snapshot:
            from agentnexus.memory.short_term import ShortTermMemory
            restored = ShortTermMemory.from_json(snapshot)
            runtime.memory_manager.short_term._messages = restored._messages
            runtime.memory_manager.short_term._summary = restored._summary
    except Exception:
        pass
    return {"status": "redone", "checkpoint": result}


@router.post("/reset")
def version_reset():
    vm = _get_version_manager()
    vm.reset()
    return {"status": "reset"}


@router.post("/compact")
def compact_context(custom_instructions: str = ""):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    mm = runtime.memory_manager
    if mm is None:
        raise HTTPException(status_code=404, detail="Memory manager not initialized")
    try:
        tokens_saved = mm.maybe_compact(
            threshold=None,
            custom_instructions=custom_instructions,
            is_auto=False,
        )
        return {"status": "compacted", "tokens_saved": tokens_saved}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
