"""Tests for WS-disconnect cancellation (产品决策: 断连 = 立刻停止).

ws_agent 的 finally 必须取消仍活跃的当前 run（cancel_run 落盘并发出
run_interrupted/run_persisted），而不是让 agent 在后台跑完；对已完成的
run 不得重复发中断事件。
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.server.routes.chat import ws_agent
from agentnexus.services.chat import ChatService


@pytest.fixture(autouse=True)
def _mock_trace_manager():
    mock_tm = MagicMock()
    with patch("agentnexus.observability.tracer.trace_manager", mock_tm):
        yield


@pytest.fixture
def runtime_chat_session():
    from agentnexus.tools.confirm_bridge import ConfirmBridge

    agent = MagicMock()
    chat = ChatService(
        agent_factory=lambda _sid=None: agent,
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )
    session = chat.start_session()
    runtime = MagicMock()
    runtime.chat = chat
    runtime.subagent_confirm = ConfirmBridge()
    return runtime, chat, session, agent


def _make_ws(should_disconnect: asyncio.Event):
    """ws mock：第一条 send_message；之后等 should_disconnect 再断。"""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    received = False

    async def receive_json():
        nonlocal received
        if not received:
            received = True
            return {"type": "send_message", "content": "hello"}
        await asyncio.wait_for(should_disconnect.wait(), timeout=15)
        raise WebSocketDisconnect()

    ws.receive_json = AsyncMock(side_effect=receive_json)
    ws.send_json = AsyncMock()
    return ws


async def _wait_for(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met in time")


async def _drain_event_types(chat, run_id: str) -> list[str]:
    """Consume the run's async queue until the None sentinel; return types."""
    types: list[str] = []
    q = chat._async_run_events.get(run_id)
    assert q is not None, "run event queue missing"
    while True:
        event = await asyncio.wait_for(q.get(), timeout=2)
        if event is None:
            break
        types.append(event.type)
    return types


@pytest.mark.asyncio
async def test_disconnect_cancels_active_run(runtime_chat_session):
    runtime, chat, session, agent = runtime_chat_session
    started = threading.Event()
    run_finished = threading.Event()

    def run(_text, memory_manager=None):
        try:
            started.set()
            run_id = chat._session_last_run.get(session.id)
            turn = chat._turns.get(run_id)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if turn is not None and turn.cancel_checker():
                    raise AgentCancelled("cancelled")
                time.sleep(0.05)
            return "too late"
        finally:
            run_finished.set()

    agent.run.side_effect = run
    should_disconnect = asyncio.Event()

    async def disconnect_once_started():
        await asyncio.to_thread(started.wait, 5)
        await _wait_for(lambda: chat._session_last_run.get(session.id) is not None)
        await asyncio.sleep(0.2)  # 让 send_message 进入 agent.run
        should_disconnect.set()

    ws = _make_ws(should_disconnect)
    with patch("agentnexus.server.app._get_runtime", return_value=runtime):
        watcher = asyncio.ensure_future(disconnect_once_started())
        await ws_agent(ws, session.id)
        watcher.cancel()

    run_id = chat._session_last_run.get(session.id)
    turn = chat._turns.get(run_id)
    assert turn is not None
    assert turn.cancel_checker(), "disconnect 后 run 未被取消"
    assert turn.record_snapshot.status == "interrupted"
    assert run_finished.wait(timeout=5), "run 线程未随取消退出"

    event_types = await _drain_event_types(chat, run_id)
    assert "run_interrupted" in event_types, f"terminal events missing: {event_types}"
    assert "run_persisted" in event_types, f"terminal events missing: {event_types}"


@pytest.mark.asyncio
async def test_disconnect_after_completion_does_not_reinterrupt(runtime_chat_session):
    """已完成的 run 断连时不得重复发 run_interrupted。"""
    runtime, chat, session, agent = runtime_chat_session
    run_started_sent = asyncio.Event()
    sent_events: list[dict] = []

    async def send_json(payload):
        sent_events.append(payload)
        if payload.get("type") == "run_started":
            run_started_sent.set()

    agent.run.return_value = "quick answer"
    should_disconnect = asyncio.Event()

    async def disconnect_after_snapshot():
        await asyncio.wait_for(run_started_sent.wait(), timeout=5)
        # 等 run 真正 settle（turn 对象在 _turns 里时 get_run_snapshot
        # 返回的是实时 record，必须等 status 离开 running）。
        await _wait_for(lambda: (
            (rid := chat._session_last_run.get(session.id)) is not None
            and not chat.is_run_active(rid)
        ))
        should_disconnect.set()

    ws = _make_ws(should_disconnect)
    ws.send_json = AsyncMock(side_effect=send_json)
    with patch("agentnexus.server.app._get_runtime", return_value=runtime):
        watcher = asyncio.ensure_future(disconnect_after_snapshot())
        await ws_agent(ws, session.id)
        watcher.cancel()

    run_id = chat._session_last_run.get(session.id)
    turn = chat._turns.get(run_id)
    assert turn is not None
    assert turn.record_snapshot.status == "finished", "已完成的 run 不应被改成 interrupted"
    socket_types = [event.get("type") for event in sent_events]
    assert "run_interrupted" not in socket_types, f"spurious interrupt event: {socket_types}"


@pytest.mark.asyncio
async def test_disconnect_during_confirm_wakes_and_cancels_run(runtime_chat_session):
    """决策 4 级联：confirm 等待中断连 → 等待以 False 唤醒且 run 被取消。"""
    runtime, chat, session, agent = runtime_chat_session
    confirm_started = threading.Event()
    confirm_result: list[bool] = []
    run_finished = threading.Event()

    def run(_text, memory_manager=None):
        try:
            confirm_started.set()
            confirm_result.append(runtime.subagent_confirm("approve dangerous tool"))
            return "after confirm"
        finally:
            run_finished.set()

    agent.run.side_effect = run
    should_disconnect = asyncio.Event()

    async def disconnect_during_confirm():
        await asyncio.to_thread(confirm_started.wait, 5)
        # 等 confirm_request 真正发到 socket（ws_confirm 已注册为桥目标）
        await _wait_for(lambda: chat._session_last_run.get(session.id) is not None)
        await asyncio.sleep(0.2)
        should_disconnect.set()

    ws = _make_ws(should_disconnect)
    with patch("agentnexus.server.app._get_runtime", return_value=runtime):
        watcher = asyncio.ensure_future(disconnect_during_confirm())
        await ws_agent(ws, session.id)
        watcher.cancel()

    assert confirm_result == [False], f"confirm 等待未被断连唤醒: {confirm_result}"
    assert run_finished.wait(timeout=5), "run 线程未退出"
    run_id = chat._session_last_run.get(session.id)
    turn = chat._turns.get(run_id)
    assert turn is not None and turn.cancel_checker(), "confirm 场景的断连未取消 run"
