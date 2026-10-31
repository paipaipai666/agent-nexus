"""Optional memory extraction completes independently of the foreground turn."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import httpx

from agentnexus.core.config import get_settings
from agentnexus.core.exceptions import AgentCancelled
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult
from agentnexus.memory.manager import MemoryManager


class MemoryStore:
    write_counter = 0

    def __init__(self):
        self.entries = []

    def search_text(self, *_args, **_kwargs):
        return []

    def save_text(self, **entry):
        self.entries.append(entry)
        self.write_counter += 1


def test_conclusion_returns_before_the_extraction_request_completes(caplog):
    entered, release = threading.Event(), threading.Event()

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return "{}"

    get_settings().memory_llm_gate = False
    memory = MemoryManager("background-extraction", llm=ExtractModel())
    with ThreadPoolExecutor(max_workers=1) as caller:
        submitted = caller.submit(memory.conclude, "记住我喜欢蓝色", "已记住蓝色偏好")
        try:
            assert entered.wait(timeout=3)
            completion = submitted.result(timeout=0.5)
            assert completion.done() is False
        finally:
            release.set()
        completion.result(timeout=3)
    assert not [record for record in caplog.records if record.levelno >= 40]


def test_conclusions_run_in_submission_order_without_parallel_requests():
    entered, release = threading.Event(), threading.Event()
    prompts = []

    class ExtractModel:
        def think(self, messages, **_kwargs):
            prompts.append(messages[0]["content"])
            if len(prompts) == 1:
                entered.set()
                assert release.wait(timeout=5)
            return "{}"

    get_settings().memory_llm_gate = False
    memory = MemoryManager("ordered-extraction", llm=ExtractModel())
    with ThreadPoolExecutor(max_workers=1) as caller:
        submitting = caller.submit(memory.conclude, "记住第一个偏好", "已记住第一个")
        try:
            assert entered.wait(timeout=3)
            first = submitting.result(timeout=0.5)
            second = memory.conclude("记住第二个偏好", "已记住第二个")
            assert len(prompts) == 1
        finally:
            release.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert len(prompts) == 2
    assert "记住第一个偏好" in prompts[0]
    assert "记住第二个偏好" in prompts[1]


def test_background_request_does_not_inherit_the_foreground_cancel_checker(monkeypatch):
    get_settings().memory_llm_gate = False
    calls = []

    class Provider:
        def stream_chat(self, **payload):
            calls.append(payload)
            if len(calls) == 1:
                raise httpx.ReadError("socket interrupted")
            return StreamResult(text='{"preference":["uses blue theme"]}')

    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: Provider())
    monkeypatch.setattr("agentnexus.core.llm.time.sleep", lambda _: None)
    llm = AgentLLM(model="memory-isolation", api_key="fixture", base_url="https://example.invalid")
    llm.set_cancel_checker(lambda: True)
    store = MemoryStore()
    memory = MemoryManager("isolated-extraction", llm=llm, long_term=store)
    memory.conclude("记住我喜欢蓝色主题", "已记住").result(timeout=3)
    assert len(calls) == 2
    assert [entry["content"] for entry in store.entries] == ["uses blue theme"]


def test_cancelled_optional_extraction_does_not_log_failure_or_block_the_next_job(caplog):
    get_settings().memory_llm_gate = False
    calls = []

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            calls.append("request")
            if len(calls) == 1:
                raise AgentCancelled("cancelled")
            return '{"preference":["uses blue theme"]}'

    store = MemoryStore()
    memory = MemoryManager("cancelled-extraction", llm=ExtractModel(), long_term=store)
    first = memory.conclude("记住主题偏好", "已记住")
    second = memory.conclude("记住蓝色主题", "已记住")
    first.result(timeout=3)
    second.result(timeout=3)
    assert [entry["content"] for entry in store.entries] == ["uses blue theme"]
    assert not [record for record in caplog.records if record.levelno >= 30 and "cancelled" in record.getMessage()]


def test_closing_memory_cancels_queued_jobs_and_prevents_late_writes():
    get_settings().memory_llm_gate = False
    entered, release = threading.Event(), threading.Event()

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(timeout=5)
            return '{"preference":["uses blue theme"]}'

    store = MemoryStore()
    memory = MemoryManager("closing-extraction", llm=ExtractModel(), long_term=store)
    first = memory.conclude("记住第一个偏好", "已记住")
    try:
        assert entered.wait(timeout=3)
        queued = memory.conclude("记住第二个偏好", "已记住")
        memory.close(timeout=0)
        assert queued.cancelled() is True
        assert memory.conclude("记住第三个偏好", "已记住").cancelled() is True
    finally:
        release.set()
    first.result(timeout=3)
    assert store.entries == []


def test_each_job_keeps_its_own_submission_context():
    get_settings().memory_llm_gate = False
    origin = ContextVar("extraction_origin", default="missing")
    observed = []

    class ExtractModel:
        def think(self, *_args, **_kwargs):
            observed.append(origin.get())
            return "{}"

    memory = MemoryManager("context-extraction", llm=ExtractModel(), long_term=MemoryStore())
    token = origin.set("first workspace")
    try:
        first = memory.conclude("记住第一个偏好", "已记住")
        origin.set("second workspace")
        second = memory.conclude("记住第二个偏好", "已记住")
    finally:
        origin.reset(token)
    first.result(timeout=3)
    second.result(timeout=3)
    assert observed == ["first workspace", "second workspace"]
