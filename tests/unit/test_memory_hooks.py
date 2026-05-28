"""Unit tests for memory operation hooks."""

from unittest.mock import MagicMock, patch

import pytest

from agentnexus.core.hooks import HookType, _reset_hook_manager, get_hook_manager


@pytest.fixture(autouse=True)
def clean_hooks():
    _reset_hook_manager()
    yield
    _reset_hook_manager()


def _make_memory_manager():
    with patch("agentnexus.memory.manager.ShortTermMemory"), \
         patch("agentnexus.memory.manager.get_long_term_memory", return_value=None), \
         patch("agentnexus.memory.manager.AgentLLM"):
        from agentnexus.memory.manager import MemoryManager

        mm = MemoryManager(session_id="test", enable_long_term=False)
        mm.short_term = MagicMock()
        mm.short_term.estimate_tokens.return_value = 0
        return mm


class TestMemoryHooks:
    def test_before_memory_op_fires_on_append(self):
        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.BEFORE_MEMORY_OP, lambda ctx: fired.append(ctx.payload)
        )
        mm = _make_memory_manager()
        mm.append("user", "hello")
        assert len(fired) == 1
        assert fired[0]["op"] == "append"
        assert fired[0]["role"] == "user"
        assert fired[0]["content"] == "hello"

    def test_after_memory_op_fires_on_append(self):
        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AFTER_MEMORY_OP, lambda ctx: fired.append(ctx.payload)
        )
        mm = _make_memory_manager()
        mm.append("assistant", "world")
        assert len(fired) == 1
        assert fired[0]["role"] == "assistant"
        assert fired[0]["content"] == "world"

    def test_both_hooks_fire_in_order(self):
        mgr = get_hook_manager()
        order = []
        mgr.register(
            HookType.BEFORE_MEMORY_OP,
            lambda ctx: order.append("before"),
            name="b",
        )
        mgr.register(
            HookType.AFTER_MEMORY_OP,
            lambda ctx: order.append("after"),
            name="a",
        )
        mm = _make_memory_manager()
        mm.append("user", "msg")
        assert order == ["before", "after"]

    def test_no_hooks_works_normally(self):
        mm = _make_memory_manager()
        mm.append("user", "msg")
        mm.short_term.append.assert_called_once_with("user", "msg")
