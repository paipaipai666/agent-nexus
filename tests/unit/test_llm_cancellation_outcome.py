"""Intentional stream cancellation does not count as a provider outage."""

import threading

import httpx
import pytest

from agentnexus.core.exceptions import AgentCancelled
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult


def test_repeated_stream_cancellations_do_not_trip_provider_health(monkeypatch, caplog):
    cancelled = threading.Event()
    calls = []

    class Provider:
        def stream_chat(self, **payload):
            calls.append(payload)
            if len(calls) <= 3:
                cancelled.set()
                raise httpx.ReadError("[WinError 10038] socket closed")
            return StreamResult(text="Task completed")

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    client = AgentLLM(model="cancel-outcome", api_key="fixture", base_url="https://example.invalid")
    client.set_cancel_checker(cancelled.is_set)
    for _ in range(3):
        cancelled.clear()
        with pytest.raises(AgentCancelled):
            client.think([{"role": "user", "content": "Work"}], silent=True, max_attempts=1)
    cancelled.clear()
    assert client.think([{"role": "user", "content": "Work"}], silent=True, max_attempts=1) == "Task completed"
    assert len(calls) == 4
    assert not [record for record in caplog.records if record.name == "agentnexus.core.llm" and record.levelno >= 30]


def test_a_read_error_without_cancellation_retries_as_a_transport_error(monkeypatch):
    calls = []

    class Provider:
        def stream_chat(self, **payload):
            calls.append(payload)
            if len(calls) == 1:
                raise httpx.ReadError("[WinError 10038] socket read failed")
            return StreamResult(text="Recovered")

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    monkeypatch.setattr("agentnexus.core.llm.time.sleep", lambda _: None)
    client = AgentLLM(model="read-error-outcome", api_key="fixture", base_url="https://example.invalid")
    assert client.think([{"role": "user", "content": "Work"}], silent=True, max_attempts=2) == "Recovered"
    assert len(calls) == 2
