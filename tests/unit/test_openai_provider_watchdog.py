"""Watchdog tests for hung OpenAI-compatible streams."""

import threading
import time

import pytest

import agentnexus.core.providers.openai_provider as provider_mod
from agentnexus.core.providers.openai_provider import OpenAIProvider


class _FakeChunk:
    def __init__(self, text="x"):
        self.choices = [type("C", (), {"delta": type("D", (), {"content": text, "reasoning_content": None, "tool_calls": []})(), "finish_reason": ""})()]
        self.usage = None


class _HangingStream:
    """Yields one chunk, then blocks forever on the second next()."""

    def __init__(self):
        self.closed = False
        self._yielded = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise RuntimeError("stream closed by abort")
        if not self._yielded:
            self._yielded = True
            return _FakeChunk("first")
        threading.Event().wait()  # hang until abort closes us
        raise AssertionError("unreachable")

    def close(self):
        self.closed = True


class _SlowStream:
    """Chunks spaced farther apart than the (patched) chunk-gap watchdog."""

    def __init__(self, gap_s: float, n: int = 3):
        self.gap_s = gap_s
        self.n = n
        self.i = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        time.sleep(self.gap_s)
        if self.closed:
            raise RuntimeError("stream closed by abort")
        return _FakeChunk(f"c{self.i}")

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fast_watchdog(monkeypatch):
    monkeypatch.setattr(provider_mod, "_CHUNK_GAP_S", 0.5)
    monkeypatch.setattr(provider_mod, "_TOTAL_BUDGET_MULT", 2.0)


def test_hung_stream_aborted_after_chunk_gap():
    provider = OpenAIProvider()
    stream = _HangingStream()
    provider._active_stream = stream

    items = []
    with pytest.raises(TimeoutError, match="no stream chunk"):
        for chunk in provider._guarded_iter(stream, timeout=30):
            items.append(chunk)

    assert [c.choices[0].delta.content for c in items] == ["first"]
    assert stream.closed  # abort fired, unblocking the pump thread


def test_slow_stream_chunks_below_gap_pass_through():
    provider = OpenAIProvider()
    stream = _SlowStream(gap_s=0.01, n=3)
    provider._active_stream = stream

    texts = [c.choices[0].delta.content for c in provider._guarded_iter(stream, timeout=30)]
    assert texts == ["c1", "c2", "c3"]
    assert not stream.closed


def test_total_budget_blows_long_running_stream():
    provider = OpenAIProvider()
    # timeout=1 -> total budget 2s (patched mult); 0.4s x 5 chunks exhausts it
    stream = _SlowStream(gap_s=0.4, n=10)
    provider._active_stream = stream

    with pytest.raises(TimeoutError, match="total budget"):
        for _ in provider._guarded_iter(stream, timeout=1):
            pass
    assert stream.closed


def test_pump_exception_propagates():
    provider = OpenAIProvider()

    class _Boom:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("connection reset")

        def close(self):
            pass

    with pytest.raises(RuntimeError, match="connection reset"):
        for _ in provider._guarded_iter(_Boom(), timeout=30):
            pass
