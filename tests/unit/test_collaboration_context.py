import contextvars
from concurrent.futures import ThreadPoolExecutor

from agentnexus.tools.confirm_bridge import CancelBridge, SubagentBridge
from agentnexus.tools.registry import ToolRegistry
from agentnexus.tools.providers.base import ToolProviderContext
from agentnexus.tools.providers.subagent_provider import SubagentToolProvider


def test_run_bridges_keep_inherited_identity_after_another_run_starts():
    cancellation = CancelBridge()
    subagents = SubagentBridge()
    first = object()
    second = object()
    cancellation.set_checker(lambda: True)
    subagents.set_context(first)
    inherited = contextvars.copy_context()
    cancellation.set_checker(lambda: False)
    subagents.set_context(second)
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(inherited.run, lambda: (cancellation.check(), subagents.get_context())).result() == (True, first)
    assert (cancellation.check(), subagents.get_context()) == (False, second)
    subagents.set_context(None)
    assert inherited.run(subagents.get_context) is first


def test_lane_pool_is_created_once_for_overlapping_sessions(monkeypatch):
    import threading
    import time

    original = ThreadPoolExecutor
    barrier = threading.Barrier(4)

    def delayed_pool(*args, **kwargs):
        time.sleep(.02)
        return original(*args, **kwargs)

    registry = ToolRegistry()
    monkeypatch.setattr("agentnexus.tools.registry.ThreadPoolExecutor", delayed_pool)

    def get_pool():
        barrier.wait()
        return registry.get_lane_pool("subagent")

    try:
        with original(4) as threads:
            pools = list(threads.map(lambda _: get_pool(), range(4)))
        assert len({id(pool) for pool in pools}) == 1
    finally:
        registry.close()


def test_async_tools_can_be_selected_without_registering_legacy_sync_tool():
    registry = ToolRegistry()
    try:
        SubagentToolProvider().register(registry, ToolProviderContext(include_tools={"subagent_start", "subagent_send", "subagent_wait"}))
        assert set(registry.list_tools()) == {"subagent_start", "subagent_send", "subagent_wait"}
        assert registry.to_openai_tools() == []
    finally:
        registry.close()


def test_cancelled_child_cannot_return_to_thinking_before_worker_exit():
    from agentnexus.services.subagents import SubagentRegistry

    registry = SubagentRegistry()
    child = registry.register("s", "p", "child", "explorer", "task")
    registry.cancel(child.subagent_id)
    registry.update(child, status="thinking")
    assert child.status == "cancelling"
    registry.update(child, status="interrupted")
    assert child.status == "interrupted"
