"""Tests for graceful shutdown (决策 1/6：桌面端关闭 = 取消并落盘后退出)。

shutdown 端点与 lifespan 关闭路径必须先取消所有活跃 run（结果落盘、
终态事件入队），再退出进程。
"""

import asyncio
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agentnexus.server.app import create_app
from agentnexus.services.chat import ChatService


def _make_chat():
    agent = MagicMock()
    return ChatService(
        agent_factory=lambda _sid=None: agent,
        memory_factory_builder=lambda _sid: lambda: MagicMock(),
    )


def test_cancel_all_runs_cancels_every_active_run():
    chat = _make_chat()
    s1 = chat.start_session()
    s2 = chat.start_session()
    r1, _e1, _t1 = chat.begin_turn(s1.id, "first")
    r2, _e2, _t2 = chat.begin_turn(s2.id, "second")

    chat.cancel_all_runs(reason="server_shutdown")

    assert chat._turns[r1.id].record_snapshot.status == "interrupted"
    assert chat._turns[r2.id].record_snapshot.status == "interrupted"

    async def types(q):
        out = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=2)
            if ev is None:
                return out
            out.append(ev.type)

    assert "run_interrupted" in asyncio.run(types(chat._async_run_events[r1.id]))
    assert "run_interrupted" in asyncio.run(types(chat._async_run_events[r2.id]))


def test_shutdown_endpoint_cancels_runs_and_schedules_exit():
    chat = _make_chat()
    session = chat.start_session()
    run, _events, _turn = chat.begin_turn(session.id, "hello")
    runtime = MagicMock()
    runtime.services.chat = chat

    app = create_app(runtime)
    with TestClient(app) as client:
        with patch("agentnexus.server.routes.runtime._schedule_process_exit") as mock_exit:
            resp = client.post("/api/runtime/shutdown")

    assert resp.status_code == 200
    mock_exit.assert_called_once()
    assert not chat.is_run_active(run.id), "shutdown 未取消活跃 run"
    assert chat._turns[run.id].record_snapshot.status == "interrupted"


def test_lifespan_teardown_cancels_runs_before_close():
    chat = MagicMock()
    runtime = MagicMock()
    runtime.services.chat = chat

    app = create_app(runtime)
    with TestClient(app):
        pass  # lifespan startup/shutdown bracket the request phase

    chat.cancel_all_runs.assert_called_once_with(reason="server_shutdown")
    runtime.close.assert_called_once()
