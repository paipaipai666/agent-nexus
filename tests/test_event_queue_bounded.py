"""Regression: per-run event queues must be bounded when the consumer is gone.

Background: `_async_run_events` / `_run_events` were unbounded `asyncio.Queue()`
/ `queue.Queue()`. When the WS client disconnected mid-run (`stream_events`
returns on WebSocketDisconnect), the run kept producing events into a queue
nobody drained until the NEXT run on the session started — a long
subagent-heavy run could pile up unbounded RAM while the CPU sat idle.
"""

import asyncio
import queue as queue_mod
from types import SimpleNamespace

import agentnexus.services.chat as chat_mod
from agentnexus.services.chat import (
    _EVENT_QUEUE_MAX,
    AgentEvent,
    ChatService,
    _enqueue_bounded,
)


def _make_service(monkeypatch) -> ChatService:
    # keep the test hermetic: no timeline sqlite writes
    monkeypatch.setattr(
        chat_mod, "get_timeline_store",
        lambda: SimpleNamespace(record_event=lambda *a, **k: None),
    )
    return ChatService(agent_factory=lambda sid: None, memory_factory_builder=lambda sid: (lambda: None))


def test_async_queue_bounded_without_consumer(monkeypatch):
    svc = _make_service(monkeypatch)
    svc._async_run_events["r1"] = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX)
    svc._run_event_seq["r1"] = 0

    for i in range(_EVENT_QUEUE_MAX + 500):
        svc._put_event("r1", AgentEvent("token", {"text": "x"}, run_id="r1", session_id="s1"))

    q = svc._async_run_events["r1"]
    assert q.qsize() == _EVENT_QUEUE_MAX          # bounded, no unbounded growth
    assert svc._dropped_events["r1"] == 500        # evictions counted
    # newest event survived (drop-oldest policy)
    last = None
    while not q.empty():
        last = q.get_nowait()
    assert last is not None and last.seq == _EVENT_QUEUE_MAX + 500


def test_sync_queue_put_does_not_block_when_full():
    q: queue_mod.Queue = queue_mod.Queue(maxsize=3)
    for i in range(10):
        _enqueue_bounded(q, AgentEvent("e", run_id="r"))   # must not block
    assert q.qsize() == 3


def test_terminator_lands_when_full_without_wiping_queue():
    """Full queue + run terminator: evict ONE stale event, keep the rest.

    A purge-all policy here would also wipe terminal-state events
    (run_interrupted) that were just enqueued — that regression broke
    test_ws_disconnect_cancels.
    """
    q: queue_mod.Queue = queue_mod.Queue(maxsize=3)
    for i in range(3):
        _enqueue_bounded(q, AgentEvent("e", run_id="r"))
    ok = _enqueue_bounded(q, None)
    assert not ok                 # an eviction happened
    assert q.qsize() == 3         # queue intact, not wiped
    assert q.get_nowait() is not None   # oldest evicted, others survive
    assert q.get_nowait() is not None
    assert q.get_nowait() is None       # terminator landed last


def test_async_terminator_lands_when_full():
    q: asyncio.Queue = asyncio.Queue(maxsize=3)
    for i in range(3):
        _enqueue_bounded(q, AgentEvent("e", run_id="r"))
    _enqueue_bounded(q, None)
    assert q.qsize() == 3
    items = [q.get_nowait() for _ in range(3)]
    assert items[-1] is None
