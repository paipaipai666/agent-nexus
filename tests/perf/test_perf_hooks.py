"""Performance tests for HookManager — fire latency with varying hook counts, concurrent fire."""

from __future__ import annotations

import threading

import pytest

from agentnexus.core.hooks import HookContext, HookManager, HookType


def _noop_hook(ctx: HookContext) -> None:
    pass


def _lightweight_hook(ctx: HookContext) -> None:
    _ = ctx.payload.get("key")


# ── HookManager.fire() with N hooks ──────────────────────────


class TestHookManagerFire:
    @pytest.mark.parametrize("hook_count", [0, 10, 50])
    def test_fire_scaling(self, benchmark, hook_count):
        mgr = HookManager()
        for i in range(hook_count):
            mgr.register(
                HookType.BEFORE_TOOL_CALL,
                _noop_hook,
                name=f"hook_{i}",
                priority=200 + i,
            )

        result = benchmark(mgr.fire, HookType.BEFORE_TOOL_CALL, {"key": "value"})
        assert isinstance(result, HookContext)
        assert not result.aborted

    def test_fire_with_payload_work(self, benchmark):
        mgr = HookManager()
        for i in range(20):
            mgr.register(
                HookType.BEFORE_TOOL_CALL,
                _lightweight_hook,
                name=f"worker_{i}",
            )

        payload = {"name": "bash", "command": "ls", "agent": "test"}
        result = benchmark(mgr.fire, HookType.BEFORE_TOOL_CALL, payload)
        assert result.elapsed_ms >= 0

    def test_fire_early_abort(self, benchmark):
        mgr = HookManager()

        def _abort_hook(ctx: HookContext) -> None:
            ctx.abort(reason="blocked")

        mgr.register(HookType.BEFORE_TOOL_CALL, _abort_hook, name="blocker", priority=100)
        for i in range(49):
            mgr.register(
                HookType.BEFORE_TOOL_CALL,
                _noop_hook,
                name=f"after_{i}",
                priority=200 + i,
            )

        result = benchmark(mgr.fire, HookType.BEFORE_TOOL_CALL, {"key": "value"})
        assert result.aborted


# ── Concurrent fire ───────────────────────────────────────────


class TestConcurrentFire:
    def test_concurrent_fire_from_threads(self, benchmark):
        mgr = HookManager()
        for i in range(10):
            mgr.register(
                HookType.BEFORE_TOOL_CALL,
                _noop_hook,
                name=f"hook_{i}",
            )

        thread_count = 4
        errors: list[Exception] = []

        def _fire_in_thread():
            try:
                for _ in range(10):
                    result = mgr.fire(HookType.BEFORE_TOOL_CALL, {"thread": "test"})
                    assert isinstance(result, HookContext)
            except Exception as e:
                errors.append(e)

        def _run_all():
            threads = [threading.Thread(target=_fire_in_thread) for _ in range(thread_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not errors

        benchmark(_run_all)

    def test_concurrent_different_hook_types(self, benchmark):
        mgr = HookManager()
        for i in range(5):
            mgr.register(HookType.BEFORE_TOOL_CALL, _noop_hook, name=f"tool_{i}")
            mgr.register(HookType.AFTER_MODEL_CALL, _noop_hook, name=f"model_{i}")

        def _fire_both():
            mgr.fire(HookType.BEFORE_TOOL_CALL, {"key": "value"})
            mgr.fire(HookType.AFTER_MODEL_CALL, {"key": "value"})

        benchmark(_fire_both)
