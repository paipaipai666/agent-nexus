"""Version control (checkpoint) and runtime status API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import APIError

router = APIRouter(tags=["version", "runtime"])


def _get_version_manager(session_id: str, runtime, *, changing: bool = False):
    if changing and runtime.chat.is_session_processing(session_id):
        raise APIError(409, "session_busy", "任务仍在执行，请先停止任务再修改会话历史")
    try:
        return runtime.chat.session_version(session_id)
    except KeyError:
        raise APIError(404, "session_not_found", "会话不存在，请检查会话列表")


@router.get("/status")
def version_status(session_id: str, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime)
    return vm.status()


@router.get("/log")
def version_log(session_id: str, limit: int = 10, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime)
    checkpoints = vm.log()
    return {"checkpoints": checkpoints[:limit], "total": len(checkpoints)}


@router.post("/undo")
def version_undo(session_id: str, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime, changing=True)
    result = vm.undo()
    if result is None:
        raise APIError(400, "bad_request", "Nothing to undo")
    runtime.chat.restore_checkpoint_memory(session_id)
    return {"status": "undone", "checkpoint": result}


@router.post("/redo")
def version_redo(session_id: str, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime, changing=True)
    result = vm.redo()
    if result is None:
        raise APIError(400, "bad_request", "Nothing to redo")
    runtime.chat.restore_checkpoint_memory(session_id)
    return {"status": "redone", "checkpoint": result}


@router.post("/reset")
def version_reset(session_id: str, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime, changing=True)
    vm.reset()
    runtime.chat.restore_checkpoint_memory(session_id)
    return {"status": "reset"}


@router.post("/jump")
def version_jump(session_id: str, cp_id: str, runtime=Depends(get_runtime)):
    vm = _get_version_manager(session_id, runtime, changing=True)
    result = vm.jump_to(cp_id)
    if result is None:
        raise APIError(404, "checkpoint_not_found", "检查点不属于此会话或已无法恢复")
    runtime.chat.restore_checkpoint_memory(session_id)
    return {"status": "restored", "checkpoint": result}


class CompactRequest(BaseModel):
    session_id: str
    custom_instructions: str = ""


@router.post("/compact")
def compact_context(req: CompactRequest, runtime=Depends(get_runtime)):
    _get_version_manager(req.session_id, runtime, changing=True)
    mm = runtime.chat.session_memory(req.session_id)
    if mm is None:
        raise APIError(404, "not_found", "Memory manager not initialized")
    try:
        tokens_saved = mm.maybe_compact(
            threshold=None,
            custom_instructions=req.custom_instructions,
            is_auto=False,
        )
        return {"status": "compacted", "tokens_saved": tokens_saved}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))
