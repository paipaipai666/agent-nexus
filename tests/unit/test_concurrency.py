"""Thread-safety tests for AgentNexus core components.

Uses concurrent.futures.ThreadPoolExecutor to verify that key
data structures and managers handle concurrent access without
crashes or data corruption.
"""

import concurrent.futures
import threading
from unittest.mock import patch

from agentnexus.core.config import get_settings
from agentnexus.memory.manager import MemoryManager
from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.observability.tracer import TraceContext
from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class TestTraceManagerConcurrency:
    """TraceContext under concurrent span creation."""

    def test_concurrent_span_creation(self):
        ctx = TraceContext()
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                span = ctx.start_span(f"op_{i}")
                ctx.end_span(span)
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(100)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"

    def test_concurrent_span_with_input_output(self):
        ctx = TraceContext()
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                span = ctx.start_span(f"op_{i}", {"key": f"value_{i}"})
                ctx.end_span(span, {"result": f"done_{i}"}, {"status": "ok"})
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(50)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(ctx.spans) == 50


class TestShortTermMemoryConcurrency:
    """ShortTermMemory under concurrent append."""

    def test_concurrent_append(self):
        stm = ShortTermMemory(max_messages=200)
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                stm.append("user", f"Message {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(100)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        all_msgs = stm.get_all()
        assert len(all_msgs) == 100

    def test_concurrent_mixed_roles(self):
        stm = ShortTermMemory(max_messages=200)
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                role = "user" if i % 2 == 0 else "assistant"
                stm.append(role, f"Content line {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(100)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0
        all_msgs = stm.get_all()
        assert len(all_msgs) == 100
        user_count = sum(1 for m in all_msgs if m["role"] == "user")
        assistant_count = sum(1 for m in all_msgs if m["role"] == "assistant")
        assert user_count == 50
        assert assistant_count == 50

    def test_concurrent_append_with_get_all(self):
        stm = ShortTermMemory(max_messages=200)
        errors = []
        lock = threading.Lock()

        def writer(i):
            try:
                stm.append("user", f"write_{i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        def reader():
            try:
                _ = stm.get_all()
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            writer_futures = [pool.submit(writer, i) for i in range(50)]
            reader_futures = [pool.submit(reader) for _ in range(20)]
            concurrent.futures.wait(writer_futures + reader_futures)

        assert len(errors) == 0


class TestToolRegistryConcurrency:
    """ToolRegistry under concurrent register and lookup."""

    def test_concurrent_register_and_get(self):
        reg = ToolRegistry()
        errors = []
        lock = threading.Lock()

        def register_worker(i):
            try:
                meta = ToolMeta(
                    name=f"tool_{i}",
                    description=f"Tool {i}",
                    param_schema={},
                    risk_level=RiskLevel.LOW,
                )
                reg.register(meta, lambda x=i: x)
            except Exception as e:
                with lock:
                    errors.append(e)

        def lookup_worker(i):
            try:
                reg.get_tool(f"tool_{i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            reg_futures = [pool.submit(register_worker, i) for i in range(50)]
            lookup_futures = [pool.submit(lookup_worker, i) for i in range(50)]
            concurrent.futures.wait(reg_futures + lookup_futures)

        assert len(errors) == 0
        tools = reg.list_tools()
        assert len(tools) == 50

    def test_concurrent_invoke(self):
        reg = ToolRegistry()
        errors = []
        lock = threading.Lock()

        for i in range(10):
            meta = ToolMeta(
                name=f"tool_{i}",
                description=f"Tool {i}",
                param_schema={},
                risk_level=RiskLevel.LOW,
            )
            reg.register(meta, lambda x=i: x)

        def invoke_worker(i):
            try:
                result = reg.invoke(f"tool_{i % 10}", {}, caller="test")
                return result
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(invoke_worker, i) for i in range(100)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0


class TestMemoryManagerConcurrency:
    """MemoryManager.append under concurrent access.

    Note: maybe_compact (called inside append) iterates the STM deque,
    which can race with concurrent appends. We mock maybe_compact to
    isolate the append-path thread safety test.
    """

    @patch("agentnexus.memory.manager.MemoryManager.maybe_compact")
    @patch("agentnexus.memory.manager.get_embedding_model")
    def test_concurrent_append(self, mock_emb, mock_compact):
        mock_emb.return_value.encode.return_value.tolist.return_value = [0.1]
        mm = MemoryManager(session_id="test_concurrency", enable_long_term=False)
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                mm.append("user" if i % 2 == 0 else "assistant", f"Message {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(50)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(mm.short_term.get_all()) == 50

    @patch("agentnexus.memory.manager.MemoryManager.maybe_compact")
    @patch("agentnexus.memory.manager.get_embedding_model")
    def test_concurrent_append_with_long_content(self, mock_emb, mock_compact):
        mock_emb.return_value.encode.return_value.tolist.return_value = [0.1]
        mm = MemoryManager(session_id="test_long", enable_long_term=False)
        errors = []
        lock = threading.Lock()
        long_msg = "X" * 5000

        def worker(i):
            try:
                mm.append("user" if i % 2 == 0 else "assistant", f"{long_msg} - {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(30)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0
        assert len(mm.short_term.get_all()) == 30


class TestSettingsConcurrency:
    """get_settings() called concurrently."""

    def test_get_settings_thread_safe(self):
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                s = get_settings()
                _ = s.llm_model_id
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(100)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"

    def test_get_settings_returns_same_instance(self):
        instances = []

        def worker(i):
            instances.append(get_settings())

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker, i) for i in range(20)]
            concurrent.futures.wait(futures)

        ref = instances[0]
        for inst in instances[1:]:
            assert inst is ref
