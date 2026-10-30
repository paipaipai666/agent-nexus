"""Chat API routes — REST + WebSocket for agent interaction."""

from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agentnexus.core.modules import ModuleUnavailable
from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import APIError

logger = logging.getLogger(__name__)
CONFIRM_TIMEOUT_SECONDS = 300


def _reaction_event(payload: dict, run_id, seq: int) -> dict:
    """Rewrite an express_reaction tool_start into a user_reaction event.

    The reaction tool must never reach the GUI as a tool card; it renders as a
    small emoji under the user's message bubble instead.
    """
    from agentnexus.tools.user_reaction import REACTION_EMOJI, parse_reaction_arguments

    name, comment = parse_reaction_arguments(payload.get("arguments", {}))
    return {
        "type": "user_reaction",
        "reaction": name,
        "emoji": REACTION_EMOJI.get(name, ""),
        "comment": comment,
        "run_id": run_id,
        "seq": seq,
    }


_GUI_EVENT_OVERRIDES = {
    "express_reaction": _reaction_event,
}


def _parse_journal_entry(entry: str) -> dict[str, str]:
    """Parse a TurnRuntime journal entry into structured data."""
    if entry.startswith("thought: "):
        return {"kind": "thought", "content": entry[len("thought: "):]}
    elif entry.startswith("tool start: "):
        rest = entry[len("tool start: "):]
        parts = rest.split(" ", 1)
        return {"kind": "tool_start", "name": parts[0], "arguments": parts[1] if len(parts) > 1 else "{}"}
    elif entry.startswith("tool done: "):
        rest = entry[len("tool done: "):]
        if " -> " in rest:
            name, result = rest.split(" -> ", 1)
            return {"kind": "tool_done", "name": name, "result": result}
        return {"kind": "tool_done", "name": rest, "result": ""}
    elif entry.startswith("retry: "):
        return {"kind": "retry", "content": entry[len("retry: "):]}
    elif entry.startswith("degraded: "):
        return {"kind": "degraded", "strategy": entry[len("degraded: "):]}
    return {"kind": "unknown", "content": entry}


def _map_to_gui_event(event, chat_service, seq: int) -> dict | None:
    """Map a ChatService AgentEvent to GUI-expected format. Returns None to skip."""
    event_type = getattr(event, "type", "")
    payload = getattr(event, "payload", {})
    run_id = getattr(event, "run_id", None)

    if event_type == "tool_start":
        override = _GUI_EVENT_OVERRIDES.get(payload.get("name", ""))
        if override is not None:
            return override(payload, run_id, seq)
        return {
            "type": "tool_call",
            "tool_name": payload.get("name", ""),
            "arguments": payload.get("arguments", {}),
            "tool_call_id": payload.get("id", ""),
            "run_id": run_id,
            "seq": seq,
        }

    if event_type == "tool_done":
        if payload.get("name", "") in _GUI_EVENT_OVERRIDES:
            return None
        return {
            "type": "tool_result",
            "tool_name": payload.get("name", ""),
            "result": payload.get("result", ""),
            "tool_call_id": payload.get("id", ""),
            "duration_ms": payload.get("duration_ms", 0),
            "risk_level": payload.get("risk_level", ""),
            "error": bool(payload.get("error", False)),
            "run_id": run_id,
            "seq": seq,
        }

    if event_type == "turn_journal":
        agent_event_name = payload.get("event", "")

        if agent_event_name in ("TOOLS_REQUESTED", "ANSWER_THOUGHT"):
            thought = (payload.get("thought") or "").strip()
            if not thought:
                turn = chat_service._turns.get(run_id)
                if turn:
                    for entry in reversed(turn._journal):
                        parsed = _parse_journal_entry(entry)
                        if parsed["kind"] == "thought":
                            thought = (parsed["content"] or "").strip()
                            break
            if not thought:
                return None
            return {"type": "thinking", "content": thought, "run_id": run_id, "seq": seq}

        return None

    elif event_type == "stream_token":
        return {"type": "token", "content": payload.get("token", ""), "run_id": run_id, "seq": seq,
                "tok_seq": getattr(event, "tok_seq", 0)}

    elif event_type == "stream_reasoning":
        return {"type": "reasoning", "content": payload.get("token", ""), "run_id": run_id, "seq": seq}

    elif event_type == "message_delta":
        return None

    elif event_type == "run_finished":
        return {**payload, "type": "answer", "content": payload.get("answer", ""), "error": payload.get("error", ""), "run_id": run_id, "seq": seq}

    elif event_type == "run_failed":
        return {**payload, "type": "error", "message": payload.get("error", ""), "run_id": run_id, "seq": seq}

    elif event_type == "run_interrupted":
        return {**payload, "type": "error", "message": payload.get("error", "cancelled"), "run_id": run_id, "seq": seq}

    elif event_type == "run_persisted":
        return {**payload, "type": "done", "run_id": run_id, "seq": seq}

    elif event_type == "run_save_failed":
        return {**payload, "type": "save_failed", "run_id": run_id, "seq": seq}

    elif event_type in ("skill_auto_selected", "workflow_step", "subagent_event"):
        return {**payload, "type": event_type, "run_id": run_id, "seq": seq}

    return None


router = APIRouter(tags=["chat"])


class CreateSessionRequest(BaseModel):
    skill: str | None = None
    profile: str | None = None
    workspace: str | None = None


class SendMessageRequest(BaseModel):
    session_id: str
    content: str
    attachments: list[dict] | None = None


class CancelRequest(BaseModel):
    run_id: str
    reason: str = "cancelled"


class ConfirmRequest(BaseModel):
    run_id: str
    approved: bool


def _resolve_workspace_dir(raw: str) -> str:
    """Validate a workspace folder and return its resolved path."""
    from pathlib import Path as _Path

    try:
        resolved = _Path(raw).expanduser().resolve(strict=True)
    except OSError:
        raise APIError(400, "bad_request", f"Directory not found: {raw}")
    if not resolved.is_dir():
        raise APIError(400, "bad_request", f"Not a directory: {raw}")
    return str(resolved)


@router.post("/session")
def create_session(req: CreateSessionRequest | None = None, runtime=Depends(get_runtime)):
    skill = req.skill if req else None
    profile = req.profile if req else None
    workspace = _resolve_workspace_dir(req.workspace) if req and req.workspace else None
    handle = runtime.chat.start_session(skill=skill, profile=profile, workspace=workspace)
    return {
        "session_id": handle.id, "skill": handle.skill, "profile": handle.profile,
        "workspace": handle.workspace,
    }


@router.post("/chat")
def send_message(req: SendMessageRequest, runtime=Depends(get_runtime)):
    try:
        run = runtime.chat.send_message(req.session_id, req.content, attachments=req.attachments)
        snapshot = runtime.chat.get_run_snapshot(run.id)
        return {
            "run_id": run.id,
            "session_id": run.session_id,
            "answer": snapshot.answer if snapshot else "",
            "status": snapshot.status if snapshot else "unknown",
        }
    except KeyError as e:
        raise APIError(404, "not_found", str(e))
    except ModuleUnavailable:
        raise
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.post("/chat/cancel")
def cancel_run(req: CancelRequest, runtime=Depends(get_runtime)):
    runtime.chat.cancel_run(req.run_id, reason=req.reason)
    return {"status": "cancelled", "run_id": req.run_id}


@router.post("/chat/confirm")
def confirm_tool(req: ConfirmRequest, runtime=Depends(get_runtime)):
    runtime.chat.confirm_tool_call(req.run_id, req.approved)
    return {"status": "confirmed" if req.approved else "denied", "run_id": req.run_id}


@router.post("/chat/{run_id}/cancel")
def cancel_run_path(run_id: str, runtime=Depends(get_runtime)):
    runtime.chat.cancel_run(run_id)
    return {"status": "cancelled", "run_id": run_id}


@router.post("/chat/{run_id}/confirm")
def confirm_tool_path(run_id: str, approved: bool = True, runtime=Depends(get_runtime)):
    runtime.chat.confirm_tool_call(run_id, approved)
    return {"status": "confirmed" if approved else "denied", "run_id": run_id}


@router.get("/chat/{run_id}/snapshot")
def get_run_snapshot(run_id: str, runtime=Depends(get_runtime)):
    record = runtime.chat.get_run_recovery(run_id)
    if record is None:
        raise APIError(404, "not_found", f"Run {run_id} not found")
    if hasattr(record, "__dict__"):
        return record.__dict__
    return record


@router.post("/chat/{run_id}/retry-save")
def retry_run_save(run_id: str, runtime=Depends(get_runtime)):
    try:
        return runtime.chat.retry_run_save(run_id)
    except KeyError:
        raise APIError(404, "run_not_found", "未找到未保存的任务，请保留当前结果副本")


@router.get("/sessions/{session_id}/run-snapshot")
def run_snapshot(session_id: str, runtime=Depends(get_runtime)):
    """Return current step's accumulated tokens + cursor for WS reconnect (R8).
    MUST be sync def — threading.Lock inside async def would block the event loop.
    FastAPI runs sync handlers in a thread pool automatically."""
    return runtime.chat.get_run_token_snapshot(session_id)


@router.get("/session/{session_id}/recovery")
def session_recovery(session_id: str, runtime=Depends(get_runtime)):
    return {"recovery": runtime.chat.get_session_recovery(session_id)}


@router.get("/session/{session_id}/submissions/{client_message_id}")
def submission_receipt(session_id: str, client_message_id: str, runtime=Depends(get_runtime)):
    receipt = runtime.chat.get_submission_receipt(session_id, client_message_id)
    if receipt is None:
        raise APIError(404, "submission_not_found", "服务端尚未收到此提交，可以使用原提交 ID 重试发送")
    return receipt


@router.post("/session/{session_id}/submissions/{client_message_id}/cancel")
def cancel_submission(session_id: str, client_message_id: str, runtime=Depends(get_runtime)):
    return runtime.chat.request_submission_cancel(session_id, client_message_id)


@router.get("/sessions")
def list_sessions(runtime=Depends(get_runtime)):
    chat = runtime.chat
    sessions = []
    for sid, handle in chat._sessions.items():
        sessions.append({
            "session_id": handle.id,
            "skill": handle.skill,
            "profile": handle.profile,
        })
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/recent")
def list_recent_sessions(
    limit: int = Query(default=5, ge=1, le=500),
    query: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    sessions = ConversationVersionManager.find_recent_sessions(
        settings.memory_db_path, None, limit=limit + 1, query=query, offset=offset
    )
    has_more = len(sessions) > limit
    sessions = sessions[:limit]
    return {"sessions": sessions, "count": len(sessions), "has_more": has_more}


class DeleteSessionsRequest(BaseModel):
    session_ids: list[str]


@router.delete("/sessions")
def delete_sessions(req: DeleteSessionsRequest, runtime=Depends(get_runtime)):
    """Delete sessions and ALL their data (messages, checkpoints, todos,
    timeline events, context snapshots) — user-initiated, irreversible.

    Sessions with a run in flight are skipped, not killed.
    """
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    chat = runtime.chat
    db_path = settings.memory_db_path

    deleted: list[str] = []
    skipped: list[str] = []
    for sid in req.session_ids:
        known = sid in chat._sessions or ConversationVersionManager.session_exists(db_path, sid)
        if not known:
            continue
        if chat.is_session_processing(sid):
            skipped.append(sid)
            continue
        chat.delete_session(sid)
        chat._sessions.pop(sid, None)
        chat._version_managers.pop(sid, None)
        last_run = chat._session_last_run.pop(sid, None)
        if last_run:
            for attr in ("_run_events", "_async_run_events", "_turns",
                         "_run_event_seq", "_run_token_seq", "_run_snapshots"):
                getattr(chat, attr).pop(last_run, None)
        ConversationVersionManager.delete_session(db_path, sid)
        deleted.append(sid)
    return {"deleted": deleted, "skipped": skipped, "count": len(deleted)}


@router.post("/session/restore")
def restore_session(req: CreateSessionRequest, runtime=Depends(get_runtime)):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()

    session_id = req.skill
    if not session_id:
        session_id = ConversationVersionManager.find_latest_session(
            settings.memory_db_path, None
        )

    chat = runtime.chat

    if session_id and chat.ensure_session_restored(session_id):
        return {"session_id": session_id, "restored": True}
    if session_id and session_id in chat._sessions:
        return {"session_id": session_id, "restored": True}
    raise APIError(404, "session_not_found", "会话不存在，已保留当前页面与草稿，请从会话列表选择或新建会话")


@router.get("/session/{session_id}/checkpoints")
def list_checkpoints(session_id: str, runtime=Depends(get_runtime)):
    try:
        version = runtime.chat._get_version_manager(session_id)
        checkpoints = version.log() if hasattr(version, "log") else []
        return {"session_id": session_id, "checkpoints": checkpoints}
    except Exception as e:
        raise APIError(500, "internal_error", str(e))


@router.get("/session/{session_id}/todos")
def list_todos(session_id: str, runtime=Depends(get_runtime)):
    todo_list = getattr(runtime.agent, "_todo_list", None)
    if todo_list is None:
        return {"items": [], "count": 0}
    items = todo_list.list_items()
    return {
        "items": [
            {
                "id": item.id,
                "description": item.description,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in items
        ],
        "count": len(items),
    }


@router.get("/session/{session_id}/subagents")
def list_subagents(session_id: str, runtime=Depends(get_runtime)):
    """Current/recent subagents of this session (in-memory registry)."""
    try:
        subagents = runtime.chat.list_subagents(session_id)
    except Exception as e:
        raise APIError(500, "internal_error", str(e))
    from agentnexus.core.config import get_settings

    return {"session_id": session_id, "subagents": subagents, "collaboration_enabled": get_settings().subagent_collaboration_enabled}


class CollaborationMessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    request_id: str = Field(min_length=1, max_length=128)
    child_run_id: str = Field(min_length=1, max_length=128)
    question_id: int | None = Field(default=None, ge=1)


@router.get("/session/{session_id}/subagents/{subagent_id}/messages")
def list_collaboration_messages(session_id: str, subagent_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=500)):
    """Durable communication history and current delivery receipts."""
    from agentnexus.storage.timeline import get_timeline_store

    messages = get_timeline_store().list_collaboration_messages(session_id, subagent_id, after, limit)
    return {"messages": messages, "last_id": messages[-1]["id"] if messages else after, "count": len(messages)}


@router.post("/session/{session_id}/subagents/{subagent_id}/messages")
def send_collaboration_message(session_id: str, subagent_id: str, body: CollaborationMessageInput, runtime=Depends(get_runtime)):
    """Acknowledge only messages committed for the current owned attempt."""
    try:
        return runtime.chat.send_subagent_message(session_id, subagent_id, body.content, body.request_id, body.child_run_id, body.question_id)
    except ValueError as exc:
        raise APIError(409, "collaboration_conflict", str(exc)) from exc


@router.post("/session/{session_id}/subagents/{subagent_id}/cancel")
def cancel_subagent(session_id: str, subagent_id: str, runtime=Depends(get_runtime)):
    """Return acceptance separately from the worker's eventual terminal status."""
    accepted = runtime.chat.cancel_subagent(session_id, subagent_id)
    entry = runtime.chat._subagents.get(subagent_id)
    from agentnexus.services.subagents import SubagentRegistry

    return {"accepted": accepted, "subagent_id": subagent_id, "subagent": SubagentRegistry.to_dict(entry) if accepted else None}


@router.get("/session/{session_id}/events")
def list_session_events(session_id: str, after: int = 0, limit: int = 2000,
                        agent_id: str | None = None,
                        view: Literal["timeline", "trajectory"] = "timeline"):
    """Append-only session event log for the timeline UI (cursor: row id)."""
    from agentnexus.storage.timeline import get_timeline_store

    events = get_timeline_store().list_events(
        session_id, after_id=after, limit=limit, agent_id=agent_id,
        normalize=view == "timeline",
    )
    last_id = events[-1]["id"] if events else after
    return {"session_id": session_id, "events": events, "last_id": last_id, "count": len(events)}


@router.get("/session/{session_id}/context/{run_id}/{step_id}")
def get_context_snapshot(session_id: str, run_id: str, step_id: int,
                         agent_id: str | None = None):
    """Full context snapshot (the exact messages array) for one model call."""
    from agentnexus.storage.timeline import get_timeline_store

    snapshot = get_timeline_store().get_snapshot(session_id, run_id, step_id, agent_id=agent_id)
    if snapshot is None:
        raise APIError(404, "not_found", f"No context snapshot for step {step_id}")
    return {"session_id": session_id, "run_id": run_id, "step_id": step_id, **snapshot}


@router.get("/session/{session_id}/context-steps/{run_id}")
def list_context_steps(session_id: str, run_id: str):
    """Step ids that have a context snapshot, for the timeline step picker."""
    from agentnexus.storage.timeline import get_timeline_store

    steps = get_timeline_store().list_snapshot_steps(session_id, run_id)
    return {"session_id": session_id, "run_id": run_id, "steps": steps}


@router.get("/session/{session_id}")
def get_session(session_id: str, runtime=Depends(get_runtime)):
    try:
        snapshot = runtime.chat.get_session_snapshot(session_id)
        session = snapshot["session"]
        return {
            "session_id": session.id,
            "skill": session.skill,
            "profile": session.profile,
            "workspace": session.workspace,
            "plan_mode": runtime.chat.is_plan_mode(session_id),
            "thinking_effort": runtime.chat.get_thinking_effort(session_id),
        }
    except KeyError:
        raise APIError(404, "not_found", f"Session {session_id} not found")


class PlanModeRequest(BaseModel):
    enabled: bool


@router.post("/session/{session_id}/plan-mode")
def set_plan_mode(session_id: str, req: PlanModeRequest, runtime=Depends(get_runtime)):
    try:
        active = runtime.chat.set_plan_mode(session_id, req.enabled)
    except KeyError as e:
        raise APIError(404, "not_found", str(e))
    return {"session_id": session_id, "plan_mode": active}


class ThinkingEffortRequest(BaseModel):
    effort: str | None = None


@router.post("/session/{session_id}/thinking-effort")
def set_thinking_effort(session_id: str, req: ThinkingEffortRequest, runtime=Depends(get_runtime)):
    try:
        effort = runtime.chat.set_thinking_effort(session_id, req.effort)
    except KeyError as e:
        raise APIError(404, "not_found", str(e))
    except ValueError as e:
        raise APIError(400, "bad_request", str(e))
    return {"session_id": session_id, "thinking_effort": effort}


@router.websocket("/ws/agent/{session_id}")
async def ws_agent(ws: WebSocket, session_id: str, resumeFrom: int | None = None, resumeEventsFrom: int | None = None, api_key: str | None = None, resumeRunId: str | None = None):
    """WebSocket endpoint for real-time agent event streaming.
    R8: Accepts optional resumeFrom query param for cursor-based reconnect."""
    from agentnexus.server.app import _get_runtime
    from agentnexus.server.auth import get_token

    token = get_token()
    if token is not None:
        if api_key is None or not secrets.compare_digest(api_key, token):
            await ws.close(code=4001, reason="Invalid or missing API key")
            return

    await ws.accept()
    runtime = _get_runtime()
    chat = runtime.chat

    try:
        chat.ensure_session_restored(session_id)
    except Exception:
        pass
    if session_id not in chat._sessions:
        await ws.send_json({"type": "error", "message": f"Unknown session: {session_id}"})
        await ws.close()
        return

    token_cursor_offset = 0
    seq_cursor_offset = 0
    last_run_id = getattr(chat, "_session_last_run", {}).get(session_id)
    matching_run = resumeRunId is not None and resumeRunId == last_run_id
    if matching_run and resumeEventsFrom is not None and resumeEventsFrom > 0:
        seq_cursor_offset = resumeEventsFrom
    if matching_run and resumeFrom is not None and resumeFrom > 0:
        token_cursor_offset = resumeFrom
        snapshot = chat.get_run_token_snapshot(session_id)
        await ws.send_json({
            "type": "reconnect_snapshot",
            "content": snapshot["content"],
            "cursor": snapshot["cursor"],
        })

    current_run_id: str | None = None

    confirm_bridge = runtime.subagent_confirm

    main_loop = asyncio.get_running_loop()

    confirm_event = threading.Event()
    confirm_approved = [False]
    pending_confirmation: dict | None = None
    confirmation_lock = threading.Lock()

    def ws_confirm(summary: str) -> bool:
        """Send confirm request via WebSocket and wait for response."""
        nonlocal pending_confirmation
        if closed.is_set():
            return False
        confirm_event.clear()
        if closed.is_set():
            return False
        confirm_approved[0] = False
        confirmation = {
            "confirmation_id": uuid.uuid4().hex,
            "run_id": chat._session_last_run.get(session_id),
            "summary": summary,
            "expires_at": (time.time() + CONFIRM_TIMEOUT_SECONDS) * 1000,
        }
        with confirmation_lock:
            pending_confirmation = confirmation
        future = asyncio.run_coroutine_threadsafe(
            ws.send_json({**confirmation, "type": "confirm_request"}),
            main_loop,
        )
        try:
            future.result(timeout=2)
        except TimeoutError:
            logger.debug("Timed out sending websocket confirm request")
        except Exception as e:
            logger.debug("Failed to send websocket confirm request: %s", e)
            return False
        if not confirm_event.wait(timeout=CONFIRM_TIMEOUT_SECONDS):
            with confirmation_lock:
                pending_confirmation = None
            try:
                asyncio.run_coroutine_threadsafe(
                    ws.send_json({
                        **confirmation,
                        "type": "confirm_timeout",
                        "message": "工具确认超时（5分钟），已自动拒绝",
                    }),
                    main_loop,
                ).result(timeout=2)
            except Exception:
                pass
            return False
        with confirmation_lock:
            pending_confirmation = None
        return confirm_approved[0]

    stream_tasks: set[asyncio.Task] = set()
    agent_tasks: set[asyncio.Task] = set()
    active_thread_ids: set[int] = set()
    closed = threading.Event()

    def is_websocket_closed_error(exc: RuntimeError) -> bool:
        message = str(exc)
        return "websocket.send" in message and (
            "websocket.close" in message or "response already completed" in message
        )

    async def send_stream_error(run_id: str, seq: int, exc: Exception) -> None:
        try:
            await ws.send_json({"type": "connection_error", "message": "事件连接中断，请重新连接并查询任务状态。", "run_id": run_id, "seq": seq})
        except WebSocketDisconnect:
            return
        except RuntimeError as send_error:
            if is_websocket_closed_error(send_error):
                return
            logger.warning("Failed to send websocket stream error", exc_info=send_error)

    async def stream_events(run_id: str):
        """Stream events from chat service to WebSocket.
        R8: Skips token events the client already received (absolute per-run
        tok_seq — immune to how much of the queue prior consumers drained);
        structural events resume via the seq cursor."""
        nonlocal current_run_id, token_cursor_offset
        current_run_id = run_id
        seq = seq_cursor_offset if run_id == resumeRunId else 0
        if run_id != resumeRunId:
            token_cursor_offset = 0
        try:
            async for event in chat.astream_events(run_id):
                if seq and getattr(event, "seq", 0) and event.seq <= seq:
                    continue
                gui_event = _map_to_gui_event(event, chat, seq)
                if gui_event is not None:
                    gui_event["seq"] = getattr(event, "seq", 0)
                    if gui_event.get("type") == "token":
                        tok_seq = getattr(event, "tok_seq", 0)
                        if tok_seq and tok_seq <= token_cursor_offset:
                            continue
                    await ws.send_json(gui_event)
                    seq = max(seq, getattr(event, "seq", 0))
        except WebSocketDisconnect:
            return
        except RuntimeError as e:
            if not is_websocket_closed_error(e):
                await send_stream_error(run_id, seq, e)
        except Exception as e:
            await send_stream_error(run_id, seq, e)

    if resumeEventsFrom is not None:
        last_run_id = getattr(chat, "_session_last_run", {}).get(session_id)
        if last_run_id and chat._async_run_events.get(last_run_id) is not None:
            attach_task = asyncio.create_task(stream_events(last_run_id))
            stream_tasks.add(attach_task)
            attach_task.add_done_callback(stream_tasks.discard)

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "send_message":
                content = data.get("content", "")
                client_message_id = data.get("client_message_id")
                if client_message_id is not None and (not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 128):
                    await ws.send_json({"type": "submission_rejected", "message": "提交 ID 无效"})
                    continue
                receipt = chat.get_submission_receipt(session_id, client_message_id) if client_message_id else None
                if receipt is not None:
                    if receipt["question"] != content or receipt["attachments"] != (data.get("attachments") or []):
                        await ws.send_json({"type": "submission_rejected", "client_message_id": client_message_id, "message": "提交 ID 已被其他内容使用"})
                    else:
                        await ws.send_json({"type": "message_ack", "client_message_id": client_message_id, "run_id": receipt["run_id"], "recovery": receipt["recovery"]})
                    continue
                if not content:
                    await ws.send_json({"type": "error", "message": "Empty content"})
                    continue

                attachments = data.get("attachments")
                if attachments is not None:
                    if (
                        not isinstance(attachments, list)
                        or any(
                            not isinstance(a, dict)
                            or not all(k in a for k in ("path", "name", "size", "mime"))
                            for a in attachments
                        )
                    ):
                        await ws.send_json({"type": "error", "message": "Invalid attachments"})
                        continue

                try:
                    from agentnexus.core.config import get_settings
                    from agentnexus.memory.versioned import ConversationVersionManager

                    settings = get_settings()
                    ConversationVersionManager.update_session_preview(
                        settings.memory_db_path, session_id, content
                    )
                except Exception as e:
                    logger.debug("Failed to update session preview: %s", e)

                run_started_event = asyncio.Event()
                run_holder: list[str] = []

                def record_run_started(run_id: str, holder=run_holder, ready=run_started_event) -> None:
                    nonlocal current_run_id
                    current_run_id = run_id
                    if closed.is_set():
                        chat.cancel_run(run_id, reason="client_disconnected")
                    holder.append(run_id)
                    ready.set()

                def run_agent(content=content, attachments=attachments, client_message_id=client_message_id, started=record_run_started, ready=run_started_event, holder=run_holder):
                    from agentnexus.core.exceptions import AgentCancelled
                    thread_id = threading.get_ident()
                    active_thread_ids.add(thread_id)
                    confirm_bridge.set_target(ws_confirm, thread_id=thread_id)
                    try:
                        chat.send_message(
                            session_id,
                            content,
                            attachments=attachments,
                            client_message_id=client_message_id,
                            on_run_started=lambda run: main_loop.call_soon_threadsafe(
                                started,
                                run.id,
                            ),
                        )
                    except ModuleUnavailable as exc:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                ws.send_json({"type": "submission_rejected", "client_message_id": client_message_id,
                                              "code": "module_unavailable", "message": str(exc)}),
                                main_loop,
                            ).result(timeout=2)
                        except Exception:
                            pass
                    except ValueError as e:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                ws.send_json({"type": "submission_rejected", "client_message_id": client_message_id, "message": str(e)}),
                                main_loop,
                            ).result(timeout=2)
                        except Exception:
                            pass
                    except Exception as e:
                        if isinstance(e, AgentCancelled):
                            return
                        logger.error("WebSocket agent run failed for session %s: %s", session_id, e, exc_info=True)
                        if holder:
                            return
                        try:
                            asyncio.run_coroutine_threadsafe(
                                ws.send_json({"type": "submission_rejected", "client_message_id": client_message_id, "message": "任务未能开始，请检查恢复记录与提供商设置后重新提交。"}),
                                main_loop,
                            ).result(timeout=2)
                        except Exception:
                            pass
                    finally:
                        confirm_bridge.set_target(None, thread_id=thread_id)
                        active_thread_ids.discard(thread_id)
                        main_loop.call_soon_threadsafe(ready.set)

                agent_task = asyncio.create_task(asyncio.to_thread(run_agent))
                agent_tasks.add(agent_task)
                agent_task.add_done_callback(agent_tasks.discard)

                try:
                    await asyncio.wait_for(run_started_event.wait(), timeout=5)
                except TimeoutError:
                    logger.warning("Timed out waiting for websocket run to start for session_id=%s", session_id)

                new_run_id = run_holder[0] if run_holder else None
                if new_run_id:
                    current_run_id = new_run_id
                    await ws.send_json({"type": "run_started", "run_id": new_run_id, "client_message_id": client_message_id, "seq": 0})
                    stream_task = asyncio.create_task(stream_events(new_run_id))
                    stream_tasks.add(stream_task)
                    stream_task.add_done_callback(stream_tasks.discard)

            elif msg_type == "cancel":
                if data.get("client_message_id"):
                    result = chat.request_submission_cancel(session_id, data["client_message_id"])
                    await ws.send_json({**result, "type": "cancelled"})
                    continue
                run_id = data.get("run_id", current_run_id)
                if run_id:
                    record = chat.get_run_snapshot(run_id)
                    if record is None or record.session_id != session_id:
                        await ws.send_json({"type": "error", "message": "任务不属于当前会话"})
                        continue
                    chat.cancel_run(run_id)
                    await ws.send_json({"type": "cancelled", "run_id": run_id, "status": "requested"})

            elif msg_type == "cancel_subagent":
                sub_id = data.get("subagent_id", "")
                if sub_id:
                    accepted = chat.cancel_subagent(session_id, sub_id)
                    await ws.send_json({"type": "subagent_cancel_requested", "subagent_id": sub_id, "accepted": accepted})

            elif msg_type == "confirm":
                with confirmation_lock:
                    valid = pending_confirmation is not None and data.get("confirmation_id") == pending_confirmation["confirmation_id"] and data.get("run_id") == pending_confirmation["run_id"] and time.time() * 1000 < pending_confirmation["expires_at"] and not confirm_event.is_set()
                    if valid:
                        confirm_approved[0] = data.get("approved") is True
                        confirm_event.set()
                await ws.send_json({"type": "confirm_result", "confirmation_id": data.get("confirmation_id"), "run_id": data.get("run_id"), "status": "accepted" if valid else "rejected", "message": "确认已接收" if valid else "确认请求已处理或已过期，请查询任务状态"})

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception("WebSocket handler error for session %s", session_id)
        try:
            await ws.send_json({"type": "connection_error", "message": "连接已中断，请重连后查询任务状态。"})
        except Exception:
            pass
    finally:
        closed.set()
        confirm_approved[0] = False
        if current_run_id and chat.is_run_active(current_run_id):
            try:
                chat.cancel_run(current_run_id, reason="client_disconnected")
            except Exception:
                logger.exception("Failed to cancel run %s on disconnect", current_run_id)
        confirm_event.set()
        if stream_tasks:
            for task in stream_tasks:
                task.cancel()
            await asyncio.gather(*stream_tasks, return_exceptions=True)
        for thread_id in list(active_thread_ids):
            confirm_bridge.set_target(None, thread_id=thread_id)
