"""Unit tests for LLM call hooks."""

from unittest.mock import MagicMock

import pytest

from agentnexus.core.hooks import HookType, _reset_hook_manager, get_hook_manager


@pytest.fixture(autouse=True)
def clean_hooks():
    _reset_hook_manager()
    yield
    _reset_hook_manager()


def _make_ctx(messages=None):
    ctx = MagicMock()
    ctx.run_state.strategy.name = "PROMPT_JSON"
    ctx.run_state.thinking_enabled = False
    ctx.messages = messages or [{"role": "user", "content": "hi"}]
    ctx.memory_state.memory_manager = None
    ctx.tool_state.tools = []
    return ctx


def _make_llm(response="response"):
    llm = MagicMock()
    llm.think.return_value = response
    llm.capabilities = MagicMock(supports_thinking=False)
    return llm


class TestLLMHooks:
    def test_before_model_call_fires(self):
        from agentnexus.agents.llm_strategy import call_llm

        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.BEFORE_MODEL_CALL, lambda ctx: fired.append(ctx.payload)
        )
        call_llm(_make_llm(), _make_ctx())
        assert len(fired) == 1
        assert fired[0]["strategy"] == "PROMPT_JSON"

    def test_before_model_call_can_modify_messages(self):
        from agentnexus.agents.llm_strategy import call_llm

        mgr = get_hook_manager()

        def inject(ctx):
            ctx.payload["messages"].insert(
                0, {"role": "system", "content": "injected"}
            )

        mgr.register(HookType.BEFORE_MODEL_CALL, inject)
        llm = _make_llm()
        call_llm(llm, _make_ctx())
        call_args = llm.think.call_args
        messages = call_args.kwargs.get("messages")
        assert messages[0]["content"] == "injected"

    def test_before_model_call_abort_returns_empty(self):
        from agentnexus.agents.llm_strategy import call_llm

        mgr = get_hook_manager()
        mgr.register(HookType.BEFORE_MODEL_CALL, lambda ctx: ctx.abort("skip"))
        llm = _make_llm()
        result = call_llm(llm, _make_ctx())
        llm.think.assert_not_called()
        assert result == ""

    def test_after_model_call_fires(self):
        from agentnexus.agents.llm_strategy import call_llm

        mgr = get_hook_manager()
        fired = []
        mgr.register(
            HookType.AFTER_MODEL_CALL, lambda ctx: fired.append(ctx.payload)
        )
        call_llm(_make_llm("hello"), _make_ctx())
        assert len(fired) == 1
        assert fired[0]["response_text"] == "hello"

    def test_after_model_call_can_modify_response(self):
        from agentnexus.agents.llm_strategy import call_llm

        mgr = get_hook_manager()

        def modify(ctx):
            ctx.payload["response_text"] = "modified"

        mgr.register(HookType.AFTER_MODEL_CALL, modify)
        result = call_llm(_make_llm("original"), _make_ctx())
        assert result == "modified"

    def test_no_hooks_works_normally(self):
        from agentnexus.agents.llm_strategy import call_llm

        result = call_llm(_make_llm("normal"), _make_ctx())
        assert result == "normal"
