"""Tests for MCP lifecycle: resource cleanup, timeouts, event loop, start/stop."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentnexus.core.config import MCPServerConfig
from agentnexus.tools.mcp_adapter import (
    MCPServerState,
    MCPToolManager,
)

from .conftest import FakeExitStack, _make_descriptor


# ── Resource cleanup tests ──────────────────────────────────────


class TestResourceCleanup:
    @staticmethod
    def _make_loop_for_close():
        loop = SimpleNamespace()
        loop.call_soon_threadsafe = MagicMock()
        loop.stop = MagicMock()
        return loop

    def test_close_multiple_calls_idempotent(self, monkeypatch):
        """Calling close() multiple times must not crash."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()

        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread

        def fake_submit(coro, timeout):
            """Run the coroutine synchronously since no real loop exists."""
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()
        assert manager._loop is None
        assert manager._thread is None
        assert manager._started is False

        manager.close()
        assert manager._started is False

    def test_close_cleans_up_runtimes(self, monkeypatch):
        """close() must disconnect all runtimes and clear descriptors."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread
        manager._server_runtimes["s1"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._server_runtimes["s2"] = SimpleNamespace(exit_stack=FakeExitStack())
        manager._tool_descriptors["t1"] = _make_descriptor()

        calls = []

        async def fake_close_all():
            calls.append("close_all")
            manager._server_runtimes.clear()
            manager._tool_descriptors.clear()

        def fake_submit(coro, timeout):
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_close_all", fake_close_all)
        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()
        assert calls == ["close_all"]
        assert manager._server_runtimes == {}
        assert manager._tool_descriptors == {}

    def test_close_swallows_errors(self, monkeypatch):
        """Errors during close must not propagate."""
        manager = MCPToolManager([])
        manager._started = True
        manager._loop = self._make_loop_for_close()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        manager._thread = thread

        async def failing_close():
            raise RuntimeError("cleanup error")

        def fake_submit(coro, timeout):
            return asyncio.run(coro)

        monkeypatch.setattr(manager, "_close_all", failing_close)
        monkeypatch.setattr(manager, "_submit", fake_submit)
        monkeypatch.setattr(threading.Thread, "join", lambda self, timeout: None)

        manager.close()  # must not raise
        assert manager._started is False

    def test_asyncexitstack_aclose_called_on_disconnect(self):
        """Disconnecting must call aclose on the exit stack."""
        manager = MCPToolManager([])
        closed = False

        async def fake_aclose():
            nonlocal closed
            closed = True

        manager._server_runtimes["api"] = SimpleNamespace(
            tool_names=["mcp_api__tool"],
            exit_stack=SimpleNamespace(aclose=fake_aclose),
        )
        manager._tool_descriptors["mcp_api__tool"] = _make_descriptor()

        asyncio.run(manager._disconnect_server("api"))
        assert closed is True


# ── Timeout tests ───────────────────────────────────────────────


class TestTimeoutBehavior:
    def test_call_tool_async_timeout_propagates(self):
        """_call_tool_async must raise TimeoutError when session.call_tool exceeds timeout."""
        manager = MCPToolManager([])

        async def slow_call_tool(name, arguments=None):
            await asyncio.sleep(100)

        mock_session = SimpleNamespace(call_tool=slow_call_tool)
        manager._server_runtimes["slow"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
            semaphore=asyncio.Semaphore(4),
        )
        descriptor = _make_descriptor(server_name="slow", remote_name="slow_tool", timeout_sec=0.01)

        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            asyncio.run(manager._call_tool_async(descriptor, {}))

    def test_connect_server_timeout_propagates(self, monkeypatch):
        """_connect_server must raise TimeoutError when initialize exceeds startup_timeout."""
        manager = MCPToolManager(
            [MCPServerConfig(name="slow", transport="stdio", command="python")],
            startup_timeout=0.01,
        )
        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)

        async def slow_init():
            await asyncio.sleep(100)

        @asynccontextmanager
        async def fake_stdio_client(params):
            read_stream = SimpleNamespace(read=MagicMock())
            write_stream = SimpleNamespace(write=MagicMock())
            yield read_stream, write_stream

        with (
            patch("mcp.ClientSession") as mock_session_cls,
            patch("mcp.client.stdio.stdio_client", side_effect=fake_stdio_client),
            patch("mcp.StdioServerParameters"),
        ):
            fake_session = MagicMock()
            fake_session.initialize = slow_init
            fake_session.__aenter__.return_value = fake_session
            mock_session_cls.return_value = fake_session

            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                asyncio.run(manager._connect_server(manager._servers[0]))

    def test_call_tool_submit_inherits_timeout(self, monkeypatch):
        """call_tool must pass descriptor.timeout_sec + 5 to _submit."""
        manager = MCPToolManager([])
        manager._tool_descriptors["tool"] = _make_descriptor(timeout_sec=10)
        manager._started = True
        manager._loop = SimpleNamespace()
        captured = []

        def fake_submit(coro, timeout):
            captured.append(timeout)
            return "ok"

        monkeypatch.setattr(manager, "_submit", fake_submit)
        result = manager.call_tool("tool", {})
        assert result == "ok"
        assert captured[0] == 15  # timeout_sec + 5


# ── Event loop lifecycle tests ──────────────────────────────────


class TestEventLoopLifecycle:
    def teardown_method(self):
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_run_loop_executes_submitted_task(self):
        """_run_loop must execute a coroutine submitted via run_coroutine_threadsafe."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def simple_task():
            return 42

        future = asyncio.run_coroutine_threadsafe(simple_task(), manager._loop)
        result = future.result(timeout=5)
        assert result == 42

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)
        assert manager._loop.is_closed()

    def test_run_loop_cancels_pending_tasks_on_stop(self):
        """When loop stops, pending tasks must be cancelled."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        cancelled_flag = []
        started = []

        async def slow_task():
            started.append(True)
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled_flag.append(True)
                raise

        future = asyncio.run_coroutine_threadsafe(slow_task(), manager._loop)
        for _ in range(50):
            if started:
                break
            time.sleep(0.01)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

        assert len(cancelled_flag) == 1
        assert future.cancelled()

    def test_run_loop_clears_event_loop_setting(self):
        """After _run_loop exits, the event loop should be closed."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)
        assert manager._loop.is_closed()

    def test_run_loop_multiple_tasks(self):
        """Multiple tasks submitted to the loop should all complete."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        results = []

        async def task_a():
            results.append("a")

        async def task_b():
            results.append("b")

        f1 = asyncio.run_coroutine_threadsafe(task_a(), manager._loop)
        f2 = asyncio.run_coroutine_threadsafe(task_b(), manager._loop)
        f1.result(timeout=5)
        f2.result(timeout=5)

        assert "a" in results
        assert "b" in results

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── _submit real Event Loop tests ───────────────────────────────


class TestSubmitRealLoop:
    def teardown_method(self):
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_submit_with_real_loop_returns_result(self):
        """_submit must return the coroutine result via the real event loop."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def add(a, b):
            return a + b

        result = manager._submit(add(1, 2), timeout=5)
        assert result == 3

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

    def test_submit_timeout_raises(self):
        """_submit must raise TimeoutError when coroutine exceeds timeout."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def slow():
            await asyncio.sleep(100)

        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            manager._submit(slow(), timeout=0.01)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)

    def test_submit_cancelled_error(self):
        """_submit must raise CancelledError when the future is cancelled."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def wait_forever():
            await asyncio.sleep(100)

        future = asyncio.run_coroutine_threadsafe(wait_forever(), manager._loop)
        future.cancel()

        with pytest.raises((asyncio.CancelledError, Exception)):
            future.result(timeout=5)

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── start() success path tests ─────────────────────────────────


class TestStartSuccess:
    def teardown_method(self):
        if hasattr(self, "_manager") and self._manager is not None:
            try:
                self._manager.close()
            except Exception:
                pass

    def test_start_with_servers_sets_up_loop_and_thread(self, monkeypatch):
        """start() must create an event loop and thread, then mark started."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager

        async def fake_connect_all():
            pass

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", fake_connect_all)

        manager.start()
        assert manager._started is True
        assert manager._loop is not None
        assert manager._thread is not None
        assert manager._thread.is_alive()

    def test_start_calls_connect_all(self, monkeypatch):
        """start() must invoke _connect_all via _submit."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager
        connect_called = []

        async def track_connect_all():
            connect_called.append(True)

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", track_connect_all)

        manager.start()
        assert len(connect_called) == 1

    def test_start_nested_cleanup_on_failure(self, monkeypatch):
        """start() must reset loop and thread when _connect_all raises."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        self._manager = manager

        async def raise_error():
            raise RuntimeError("connect failed")

        monkeypatch.setattr(manager, "_ensure_sdk_available", lambda: None)
        monkeypatch.setattr(manager, "_connect_all", raise_error)

        with pytest.raises(RuntimeError, match="connect failed"):
            manager.start()
        assert manager._started is False
        assert manager._loop is None
        assert manager._thread is None


# ── start() idempotency tests ──────────────────────────────────


class TestStartIdempotency:
    def test_start_when_already_started_returns_immediately(self, monkeypatch):
        """start() must return early when _started is already True."""
        manager = MCPToolManager(
            [MCPServerConfig(name="x", transport="stdio", command="python")],
        )
        manager._started = True
        called = []

        def fake_connect_all():
            called.append(True)

        monkeypatch.setattr(manager, "_connect_all", fake_connect_all)

        manager.start()
        assert manager._loop is None  # no loop created
        assert called == []  # _connect_all not called

    def test_start_no_servers_sets_started_without_loop(self):
        """start() with no servers must set started and not create loop."""
        manager = MCPToolManager([])
        manager.start()
        assert manager._started is True
        assert manager._loop is None
        assert manager._thread is None


# ── _run_loop exception handling ────────────────────────────────


class TestRunLoopExceptionHandling:
    def teardown_method(self):
        if hasattr(self, "_loop") and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:
                pass

    def test_run_loop_recovers_from_task_exception(self):
        """Event loop must continue running after a task raises unhandled exception."""
        manager = MCPToolManager([])
        manager._loop = asyncio.new_event_loop()
        self._loop = manager._loop
        self._loop.set_exception_handler(lambda loop, context: None)
        thread = threading.Thread(target=manager._run_loop, daemon=True)
        thread.start()

        async def failing_task():
            raise ValueError("oops")

        async def succeeding_task():
            return 42

        with pytest.raises(ValueError, match="oops"):
            manager._submit(failing_task(), timeout=5)

        second_result = manager._submit(succeeding_task(), timeout=5)
        assert second_result == 42

        manager._loop.call_soon_threadsafe(manager._loop.stop)
        thread.join(timeout=5)


# ── call_lock concurrency tests ─────────────────────────────────


class TestCallLockConcurrency:
    def test_call_lock_serializes_concurrent_calls(self):
        """Two concurrent tool calls on the same server must be serialized by call_lock."""
        manager = MCPToolManager([])
        call_order = []
        event = asyncio.Event()

        async def slow_call_tool(name, arguments=None):
            call_order.append("enter")
            await event.wait()
            await asyncio.sleep(0.01)
            call_order.append("exit")
            return SimpleNamespace(content=[SimpleNamespace(text="done")], isError=False, is_error=False)

        mock_session = SimpleNamespace(call_tool=slow_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
            semaphore=None,
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="search", timeout_sec=30)

        async def run():
            t1 = asyncio.create_task(manager._call_tool_async(descriptor, {}))
            t2 = asyncio.create_task(manager._call_tool_async(descriptor, {}))
            await asyncio.sleep(0.05)  # let t1 acquire lock
            event.set()
            await asyncio.gather(t1, t2)

        asyncio.run(run())
        # enter/exit pairs must be non-overlapping
        assert call_order == ["enter", "exit", "enter", "exit"]

    def test_call_lock_released_on_error(self):
        """If tool call raises, the lock must still be released so other calls can proceed."""
        manager = MCPToolManager([])
        call_count = []

        async def failing_call_tool(name, arguments=None):
            call_count.append("called")
            raise RuntimeError("tool error")

        mock_session = SimpleNamespace(call_tool=failing_call_tool)
        manager._server_runtimes["docs"] = SimpleNamespace(
            session=mock_session,
            call_lock=asyncio.Lock(),
            semaphore=None,
        )
        descriptor = _make_descriptor(server_name="docs", remote_name="fail", timeout_sec=30)

        async def run():
            with pytest.raises(RuntimeError):
                await manager._call_tool_async(descriptor, {})
            # lock must be free for a second call
            with pytest.raises(RuntimeError):
                await manager._call_tool_async(descriptor, {})
            assert call_count == ["called", "called"]

        asyncio.run(run())
