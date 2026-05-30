"""Chat API routes — REST + WebSocket for agent interaction."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel


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
    confirm_result: asyncio.Future[bool] | None = None

    # Set up HITL confirm bridge for this WebSocket connection
    confirm_bridge = runtime.subagent_confirm
    original_target = confirm_bridge._target

    def ws_confirm(summary: str) -> bool:
        """Send confirm request via WebSocket and wait for response."""
        nonlocal confirm_result
        loop = asyncio.get_running_loop()
        confirm_result = loop.create_future()
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "confirm_request", "summary": summary}),
            loop,
        )
        # Block until response
        return confirm_result.result()

    confirm_bridge.set_target(ws_confirm)

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
        except Exception as e:
            await ws.send_json({"type": "error", "message": str(e), "run_id": run_id, "seq": seq})

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "send_message":
                content = data.get("content", "")
                if not content:
                    await ws.send_json({"type": "error", "message": "Empty content"})
                    continue

                # Find the run_id that will be created by send_message.
                # begin_turn() adds to _run_events before the agent runs,
                # so we can detect the new key after starting the thread.
                pre_run_ids = set(chat._run_events.keys())

                def run_agent():
                    try:
                        chat.send_message(session_id, content)
                    except Exception:
                        pass

                task = asyncio.create_task(asyncio.to_thread(run_agent))

                # Wait for begin_turn to create the run (adds to _run_events)
                new_run_id = None
                for _ in range(500):
                    post_ids = set(chat._run_events.keys()) - pre_run_ids
                    if post_ids:
                        new_run_id = post_ids.pop()
                        break
                    await asyncio.sleep(0.01)

                if new_run_id:
                    asyncio.create_task(stream_events(new_run_id))
                await task

            elif msg_type == "cancel":
                run_id = data.get("run_id", current_run_id)
                if run_id:
                    chat.cancel_run(run_id)
                    await ws.send_json({"type": "cancelled", "run_id": run_id})

            elif msg_type == "confirm":
                approved = data.get("approved", False)
                if confirm_result and not confirm_result.done():
                    confirm_result.set_result(approved)

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
        # Restore original confirm target
        confirm_bridge.set_target(original_target)
