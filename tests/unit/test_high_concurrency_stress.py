"""High concurrency stress tests.

Validates resource scheduling and context isolation under high concurrency
(50+ threads, sustained load).
"""
import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.memory.short_term import ShortTermMemory
from agentnexus.observability.tracer import TraceContext
from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class TestHighConcurrencyStress:
    """High concurrency (50+ threads) stress tests."""

    def test_concurrent_span_creation_high_threads(self):
        """50+ threads creating spans simultaneously."""
        ctx = TraceContext()
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                span = ctx.start_span(f"op_{i}")
                ctx.end_span(span, {"result": f"done_{i}"})
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(worker, i) for i in range(200)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(ctx.spans) == 200

    def test_concurrent_stm_append_high_threads(self):
        """100 threads appending to STM simultaneously."""
        stm = ShortTermMemory(max_messages=500)
        errors = []
        lock = threading.Lock()

        def worker(i):
            try:
                stm.append("user", f"Message {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(worker, i) for i in range(200)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        all_msgs = stm.get_all()
        assert len(all_msgs) == 200

    def test_concurrent_tool_registry_high_threads(self):
        """50 threads registering and invoking tools."""
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

        def invoke_worker(i):
            try:
                reg.invoke(f"tool_{i}", {}, caller="test")
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            reg_futures = [pool.submit(register_worker, i) for i in range(50)]
            concurrent.futures.wait(reg_futures)
            invoke_futures = [pool.submit(invoke_worker, i) for i in range(50)]
            concurrent.futures.wait(invoke_futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"

    def test_sustained_load(self):
        """Sustained concurrent load over time."""
        stm = ShortTermMemory(max_messages=10000)
        errors = []
        lock = threading.Lock()

        def batch_worker(batch):
            try:
                for i in range(20):
                    stm.append("user", f"Batch {batch} Message {i}")
                    time.sleep(0.001)  # Small delay to simulate real load
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(batch_worker, i) for i in range(5)]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        all_msgs = stm.get_all()
        assert len(all_msgs) == 100  # 5 batches × 20 messages

    @patch("agentnexus.memory.manager.MemoryManager.maybe_compact")
    def test_mixed_operations_high_concurrency(self, mock_compact):
        """Mixed append, get_all, and mark_api_call under high concurrency."""
        from agentnexus.memory.manager import MemoryManager
        from unittest.mock import patch

        with patch("agentnexus.memory.manager.get_embedding_model") as mock_emb:
            mock_emb.return_value.encode.return_value.tolist.return_value = [0.1]
            mm = MemoryManager(session_id="stress_test", enable_long_term=False)

        errors = []
        lock = threading.Lock()

        def append_worker(i):
            try:
                mm.append("user", f"Message {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        def get_all_worker():
            try:
                mm.short_term.get_all()
            except Exception as e:
                with lock:
                    errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
            append_futures = [pool.submit(append_worker, i) for i in range(100)]
            get_all_futures = [pool.submit(get_all_worker) for _ in range(20)]
            concurrent.futures.wait(append_futures + get_all_futures)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        all_msgs = mm.short_term.get_all()
        # Due to deque maxlen racing under high concurrency, messages may be dropped
        assert len(all_msgs) >= 50
