"""Chat API routes — REST + WebSocket for agent interaction."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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

    if event_type == "turn_journal":
        agent_event_name = payload.get("event", "")

        if agent_event_name in ("TOOLS_FOUND", "ANSWER_THOUGHT"):
            turn = chat_service._turns.get(run_id)
            thought = ""
            if turn:
                for entry in reversed(turn._journal):
                    parsed = _parse_journal_entry(entry)
                    if parsed["kind"] == "thought":
                        thought = parsed["content"]
                        break
            return {"type": "thinking", "content": thought, "run_id": run_id, "seq": seq}

        elif agent_event_name == "TOOL_START":
            turn = chat_service._turns.get(run_id)
            tool_name, arguments = "", "{}"
            if turn:
                for entry in reversed(turn._journal):
                    parsed = _parse_journal_entry(entry)
                    if parsed["kind"] == "tool_start":
                        tool_name = parsed["name"]
                        arguments = parsed["arguments"]
                        break
            return {"type": "tool_call", "tool_name": tool_name, "arguments": arguments, "run_id": run_id, "seq": seq}

        elif agent_event_name == "TOOL_DONE":
            turn = chat_service._turns.get(run_id)
            tool_name, result = "", ""
            if turn:
                for entry in reversed(turn._journal):
                    parsed = _parse_journal_entry(entry)
                    if parsed["kind"] == "tool_done":
                        tool_name = parsed["name"]
                        result = parsed["result"]
                        break
            return {"type": "tool_result", "tool_name": tool_name, "result": result, "run_id": run_id, "seq": seq}

        return None

    elif event_type == "stream_token":
        return {"type": "token", "content": payload.get("token", ""), "run_id": run_id, "seq": seq}

    elif event_type == "stream_reasoning":
        return {"type": "reasoning", "content": payload.get("token", ""), "run_id": run_id, "seq": seq}

    elif event_type == "message_delta":
        # Skip — run_finished already provides the complete answer
        return None

    elif event_type == "run_finished":
        return {"type": "answer", "content": payload.get("answer", ""), "run_id": run_id, "seq": seq}

    elif event_type == "run_failed":
        return {"type": "error", "message": payload.get("error", ""), "run_id": run_id, "seq": seq}

    elif event_type == "run_interrupted":
        return {"type": "error", "message": payload.get("error", "cancelled"), "run_id": run_id, "seq": seq}

    elif event_type == "run_persisted":
        return {"type": "done", "run_id": run_id, "seq": seq}

    elif event_type in ("skill_auto_selected", "workflow_step"):
        return {**payload, "type": event_type, "run_id": run_id, "seq": seq}

    return None


router = APIRouter(tags=["chat"])


class CreateSessionRequest(BaseModel):
    skill: str | None = None
    profile: str | None = None


class SendMessageRequest(BaseModel):
    session_id: str
    content: str


class CancelRequest(BaseModel):
    run_id: str
    reason: str = "cancelled"


class ConfirmRequest(BaseModel):
    run_id: str
    approved: bool


@router.post("/session")
def create_session(req: CreateSessionRequest | None = None):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    skill = req.skill if req else None
    profile = req.profile if req else None
    handle = runtime.services.chat.start_session(skill=skill, profile=profile)
    return {"session_id": handle.id, "skill": handle.skill, "profile": handle.profile}


@router.post("/chat")
def send_message(req: SendMessageRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    try:
        run = runtime.services.chat.send_message(req.session_id, req.content)
        snapshot = runtime.services.chat.get_run_snapshot(run.id)
        return {
            "run_id": run.id,
            "session_id": run.session_id,
            "answer": snapshot.answer if snapshot else "",
            "status": snapshot.status if snapshot else "unknown",
        }
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/cancel")
def cancel_run(req: CancelRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.chat.cancel_run(req.run_id, reason=req.reason)
    return {"status": "cancelled", "run_id": req.run_id}


@router.post("/chat/confirm")
def confirm_tool(req: ConfirmRequest):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.chat.confirm_tool_call(req.run_id, req.approved)
    return {"status": "confirmed" if req.approved else "denied", "run_id": req.run_id}


@router.post("/chat/{run_id}/cancel")
def cancel_run_path(run_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.chat.cancel_run(run_id)
    return {"status": "cancelled", "run_id": run_id}


@router.post("/chat/{run_id}/confirm")
def confirm_tool_path(run_id: str, approved: bool = True):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    runtime.services.chat.confirm_tool_call(run_id, approved)
    return {"status": "confirmed" if approved else "denied", "run_id": run_id}


@router.get("/chat/{run_id}/snapshot")
def get_run_snapshot(run_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    record = runtime.services.chat.get_run_snapshot(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if hasattr(record, "__dict__"):
        return record.__dict__
    return record


@router.get("/sessions")
def list_sessions():
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    chat = runtime.services.chat
    sessions = []
    for sid, handle in chat._sessions.items():
        sessions.append({
            "session_id": handle.id,
            "skill": handle.skill,
            "profile": handle.profile,
        })
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/recent")
def list_recent_sessions(limit: int = 5):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    workspace = str(Path.cwd())
    sessions = ConversationVersionManager.find_recent_sessions(
        settings.memory_db_path, workspace, limit=limit
    )
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/session/restore")
def restore_session(req: CreateSessionRequest):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.versioned import ConversationVersionManager

    settings = get_settings()
    workspace = str(Path.cwd())

    # Find the latest session if no session_id provided
    session_id = req.skill  # Reuse skill field for session_id
    if not session_id:
        session_id = ConversationVersionManager.find_latest_session(
            settings.memory_db_path, workspace
        )
    if not session_id:
        raise HTTPException(status_code=404, detail="No session to restore")

    # Verify session belongs to workspace
    if not ConversationVersionManager.session_belongs_to_workspace(
        settings.memory_db_path, session_id, workspace
    ):
        raise HTTPException(status_code=404, detail="Session not found")

    # Create a new handle with the existing session_id
    from agentnexus.server.app import _get_runtime
    from agentnexus.services.chat import SessionHandle

    runtime = _get_runtime()
    handle = SessionHandle(id=session_id, skill=None, profile=req.profile)
    runtime.services.chat._sessions[session_id] = handle

    # Restore memory from version manager
    version = ConversationVersionManager(
        session_id, settings.memory_db_path,
        workspace_path=workspace, profile=req.profile or ""
    )
    snapshot = version.get_head_stm()
    if snapshot:
        from agentnexus.memory.short_term import ShortTermMemory
        restored = ShortTermMemory.from_json(snapshot)
        runtime.memory_manager.short_term._messages = restored._messages
        runtime.memory_manager.short_term._summary = restored._summary

    return {"session_id": session_id, "restored": True}


@router.get("/session/{session_id}/checkpoints")
def list_checkpoints(session_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    try:
        version = runtime.version_manager
        checkpoints = version.log() if hasattr(version, "log") else []
        return {"session_id": session_id, "checkpoints": checkpoints}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}/todos")
def list_todos(session_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
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


@router.get("/session/{session_id}")
def get_session(session_id: str):
    from agentnexus.server.app import _get_runtime

    runtime = _get_runtime()
    try:
        snapshot = runtime.services.chat.get_session_snapshot(session_id)
        session = snapshot["session"]
        return {
            "session_id": session.id,
            "skill": session.skill,
            "profile": session.profile,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


@router.websocket("/ws/agent/{session_id}")
async def ws_agent(ws: WebSocket, session_id: str):
    """WebSocket endpoint for real-time agent event streaming."""
    from agentnexus.server.app import _get_runtime

    await ws.accept()
    runtime = _get_runtime()
    chat = runtime.services.chat

    if session_id not in chat._sessions:
        await ws.send_json({"type": "error", "message": f"Unknown session: {session_id}"})
        await ws.close()
        return

    current_run_id: str | None = None

    # Set up HITL confirm bridge for this WebSocket connection
    confirm_bridge = runtime.subagent_confirm

    # Capture the main event loop for use in ws_confirm (which runs in a thread)
    main_loop = asyncio.get_running_loop()

    # Use threading.Event for blocking wait in the tool execution thread
    confirm_event = threading.Event()
    confirm_approved = [False]

    def ws_confirm(summary: str) -> bool:
        """Send confirm request via WebSocket and wait for response."""
        if closed.is_set():
            return False
        confirm_event.clear()
        confirm_approved[0] = False
        future = asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "confirm_request", "summary": summary}),
            main_loop,
        )
        try:
            future.result(timeout=5)
        except Exception as e:
            logger.debug("Failed to send websocket confirm request: %s", e)
            return False
        # Fail closed if the client disconnects or never answers.
        if not confirm_event.wait(timeout=300):
            return False
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
            await ws.send_json({"type": "error", "message": str(exc), "run_id": run_id, "seq": seq})
        except WebSocketDisconnect:
            return
        except RuntimeError as send_error:
            if is_websocket_closed_error(send_error):
                return
            logger.warning("Failed to send websocket stream error", exc_info=send_error)

    async def stream_events(run_id: str):
        """Stream events from chat service to WebSocket.

        Uses async queue for real-time event delivery.
        """
        nonlocal current_run_id
        current_run_id = run_id
        seq = 0
        try:
            async for event in chat.astream_events(run_id):
                gui_event = _map_to_gui_event(event, chat, seq)
                if gui_event is not None:
                    await ws.send_json(gui_event)
                    seq += 1
        except WebSocketDisconnect:
            return
        except RuntimeError as e:
            if not is_websocket_closed_error(e):
                await send_stream_error(run_id, seq, e)
        except Exception as e:
            await send_stream_error(run_id, seq, e)

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "send_message":
                content = data.get("content", "")
                if not content:
                    await ws.send_json({"type": "error", "message": "Empty content"})
                    continue

                # Store user message as session preview for sidebar display
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

                def record_run_started(run_id: str) -> None:
                    run_holder.append(run_id)
                    run_started_event.set()

                def run_agent():
                    thread_id = threading.get_ident()
                    active_thread_ids.add(thread_id)
                    confirm_bridge.set_target(ws_confirm, thread_id=thread_id)
                    try:
                        chat.send_message(
                            session_id,
                            content,
                            on_run_started=lambda run: main_loop.call_soon_threadsafe(
                                record_run_started,
                                run.id,
                            ),
                        )
                    except Exception as e:
                        logger.debug("WebSocket agent run failed: %s", e)
                    finally:
                        confirm_bridge.set_target(None, thread_id=thread_id)
                        active_thread_ids.discard(thread_id)
                        main_loop.call_soon_threadsafe(run_started_event.set)

                # Run agent in background thread
                agent_task = asyncio.create_task(asyncio.to_thread(run_agent))
                agent_tasks.add(agent_task)
                agent_task.add_done_callback(agent_tasks.discard)

                # Wait for begin_turn to create this session's run.
                try:
                    await asyncio.wait_for(run_started_event.wait(), timeout=5)
                except TimeoutError:
                    logger.warning("Timed out waiting for websocket run to start for session_id=%s", session_id)

                new_run_id = run_holder[0] if run_holder else None
                if new_run_id:
                    current_run_id = new_run_id
                    stream_task = asyncio.create_task(stream_events(new_run_id))
                    stream_tasks.add(stream_task)
                    stream_task.add_done_callback(stream_tasks.discard)
                    # 发送 run_id 给 GUI，用于取消操作
                    await ws.send_json({"type": "run_started", "run_id": new_run_id, "seq": 0})
                # Don't await task — let agent run in background so
                # the handler can continue receiving messages (confirm, cancel)

            elif msg_type == "cancel":
                run_id = data.get("run_id", current_run_id)
                if run_id:
                    chat.cancel_run(run_id)
                    await ws.send_json({"type": "cancelled", "run_id": run_id})

            elif msg_type == "confirm":
                approved = data.get("approved", False)
                confirm_approved[0] = approved
                confirm_event.set()

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        closed.set()
        confirm_approved[0] = False
        confirm_event.set()
        if current_run_id:
            snapshot = chat.get_run_snapshot(current_run_id)
            if snapshot is None or snapshot.status == "running":
                chat.cancel_run(current_run_id, reason="websocket disconnected")
        if stream_tasks:
            for task in stream_tasks:
                task.cancel()
            await asyncio.gather(*stream_tasks, return_exceptions=True)
        for task in agent_tasks:
            task.cancel()
        for thread_id in list(active_thread_ids):
            confirm_bridge.set_target(None, thread_id=thread_id)
