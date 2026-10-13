"""Approval replies must name the live request before a tool may execute."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect

from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService
from agentnexus.tools.confirm_bridge import ConfirmBridge


@pytest.mark.asyncio
async def test_wrong_approval_id_is_rejected_then_matching_reply_is_acknowledged(monkeypatch):
    bridge = ConfirmBridge()
    agent = MagicMock()
    agent.run.side_effect = lambda *a, **k: "approved" if bridge("write file") else "denied"
    service = ChatService(lambda _: agent, lambda _: lambda: MagicMock())
    session = service.start_session()
    monkeypatch.setattr("agentnexus.server.app._get_runtime", lambda: SimpleNamespace(chat=service, subagent_confirm=bridge))
    events = []
    sent = asyncio.Queue()
    step = 0
    confirmation = {}

    async def wait_for_type(kind):
        while True:
            event = await asyncio.wait_for(sent.get(), timeout=2)
            if event["type"] == kind:
                return event

    async def receive():
        nonlocal step, confirmation
        step += 1
        if step == 1:
            return {"type": "send_message", "content": "work", "client_message_id": "approve-request"}
        if step == 2:
            confirmation = await wait_for_type("confirm_request")
            assert isinstance(confirmation["confirmation_id"], str)
            return {"type": "confirm", "run_id": confirmation["run_id"], "confirmation_id": "wrong", "approved": True}
        if step == 3:
            rejected = await wait_for_type("confirm_result")
            assert rejected["status"] == "rejected"
            return {"type": "confirm", "run_id": confirmation["run_id"], "confirmation_id": confirmation["confirmation_id"], "approved": True}
        await wait_for_type("done")
        raise WebSocketDisconnect()

    async def send(payload):
        events.append(payload)
        await sent.put(payload)

    ws = AsyncMock()
    ws.receive_json.side_effect = receive
    ws.send_json.side_effect = send
    await asyncio.wait_for(ws_agent(ws, session.id), timeout=5)
    assert next(event for event in events if event["type"] == "answer")["content"] == "approved"
    assert [event["status"] for event in events if event["type"] == "confirm_result"] == ["rejected", "accepted"]
