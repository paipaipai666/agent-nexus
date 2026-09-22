"""Unit tests for the unified hook/event system."""


from agentnexus.core.hooks import (
    HookContext,
    HookManager,
    HookType,
    _reset_hook_manager,
    get_hook_manager,
    on,
)


class TestHookContext:
    def test_default_not_aborted(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"x": 1})
        assert not ctx.aborted
        assert ctx.abort_reason == ""

    def test_abort_sets_flag(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort("blocked")
        assert ctx.aborted
        assert ctx.abort_reason == "blocked"

    def test_payload_is_mutable(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"params": {"k": "v"}})
        ctx.payload["params"]["k"] = "modified"
        assert ctx.payload["params"]["k"] == "modified"


class TestHookManager:
    def test_register_and_list(self):
        mgr = HookManager()

        def my_hook(ctx):
            pass

        name = mgr.register(HookType.BEFORE_TOOL_CALL, my_hook)
        assert name == "my_hook"
        assert "my_hook" in [h["name"] for h in mgr.list_hooks()]

    def test_fire_calls_hooks_in_priority_order(self):
        mgr = HookManager()
        order = []
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: order.append("low"), name="low", priority=300)
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: order.append("high"), name="high", priority=100)
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert order == ["high", "low"]

    def test_fire_skips_disabled_hooks(self):
        mgr = HookManager()
        called = []
        mgr.register(
            HookType.BEFORE_TOOL_CALL,
            lambda ctx: called.append(True),
            name="h1",
            enabled=False,
        )
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert called == []

    def test_enable_disable(self):
        mgr = HookManager()
        called = []
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: called.append(True), name="h1")
        mgr.disable("h1")
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert called == []
        mgr.enable("h1")
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert called == [True]

    def test_fire_aborts_on_hook_abort(self):
        mgr = HookManager()
        called = []
        mgr.register(
            HookType.BEFORE_TOOL_CALL,
            lambda ctx: ctx.abort("no"),
            priority=100,
            name="blocker",
        )
        mgr.register(
            HookType.BEFORE_TOOL_CALL,
            lambda ctx: called.append(True),
            priority=200,
            name="after",
        )
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert ctx.aborted
        assert called == []

    def test_fire_returns_modified_payload(self):
        mgr = HookManager()

        def modifier(ctx):
            ctx.payload["params"]["timeout"] = 999

        mgr.register(HookType.BEFORE_TOOL_CALL, modifier)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"params": {"timeout": 30}})
        assert ctx.payload["params"]["timeout"] == 999

    def test_fire_exception_isolation(self):
        mgr = HookManager()
        called = []

        def exploding(ctx):
            raise RuntimeError("boom")

        mgr.register(HookType.BEFORE_TOOL_CALL, exploding, name="bad", priority=100)
        mgr.register(
            HookType.BEFORE_TOOL_CALL,
            lambda ctx: called.append(True),
            name="good",
            priority=200,
        )
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert called == [True]

    def test_fire_async_hook_from_sync(self):
        mgr = HookManager()

        async def async_hook(ctx):
            ctx.payload["result"] = "async_done"

        mgr.register(HookType.BEFORE_TOOL_CALL, async_hook)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert ctx.payload["result"] == "async_done"

    def test_unregister(self):
        mgr = HookManager()
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: None, name="h1")
        mgr.unregister("h1")
        assert "h1" not in [h["name"] for h in mgr.list_hooks()]

    def test_only_matching_type_fires(self):
        mgr = HookManager()
        called = []
        mgr.register(
            HookType.BEFORE_TOOL_CALL,
            lambda ctx: called.append("tool"),
            name="t",
        )
        mgr.register(
            HookType.BEFORE_MODEL_CALL,
            lambda ctx: called.append("model"),
            name="m",
        )
        mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert called == ["tool"]

    def test_custom_name(self):
        mgr = HookManager()
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: None, name="custom_name")
        assert "custom_name" in [h["name"] for h in mgr.list_hooks()]

    def test_clear(self):
        mgr = HookManager()
        mgr.register(HookType.BEFORE_TOOL_CALL, lambda ctx: None, name="h1")
        mgr.register(HookType.AFTER_TOOL_CALL, lambda ctx: None, name="h2")
        mgr.clear()
        assert mgr.list_hooks() == []

    def test_list_hooks_shows_metadata(self):
        mgr = HookManager()

        async def ah(ctx):
            pass

        def sh(ctx):
            pass

        mgr.register(HookType.BEFORE_TOOL_CALL, ah, name="async_one", priority=100)
        mgr.register(HookType.BEFORE_TOOL_CALL, sh, name="sync_one", priority=200, enabled=False)
        info = mgr.list_hooks()
        assert len(info) == 2
        by_name = {h["name"]: h for h in info}
        assert by_name["async_one"]["is_async"] is True
        assert by_name["async_one"]["enabled"] is True
        assert by_name["sync_one"]["is_async"] is False
        assert by_name["sync_one"]["enabled"] is False


class TestDecoratorAPI:
    def test_on_decorator_registers(self):
        mgr = HookManager()

        @on(HookType.BEFORE_TOOL_CALL, _manager=mgr)
        def my_hook(ctx):
            pass

        assert "my_hook" in [h["name"] for h in mgr.list_hooks()]


class TestSingleton:
    def test_get_hook_manager_returns_same_instance(self):
        _reset_hook_manager()
        try:
            m1 = get_hook_manager()
            m2 = get_hook_manager()
            assert m1 is m2
        finally:
            _reset_hook_manager()


class TestStructuredAbort:
    def test_abort_string_backward_compat(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort("blocked by policy")
        assert ctx.aborted
        assert ctx.abort_reason == "blocked by policy"
        assert ctx.abort_code == "BLOCKED"

    def test_abort_structured_code_and_message(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort(code="PERMISSION_DENIED", message="rm -rf is forbidden")
        assert ctx.aborted
        assert ctx.abort_code == "PERMISSION_DENIED"
        assert ctx.abort_reason == "rm -rf is forbidden"

    def test_abort_structured_with_details(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort(code="RATE_LIMITED", message="too many calls", details={"limit": 10, "window": "1m"})
        assert ctx.abort_code == "RATE_LIMITED"
        assert ctx.abort_details == {"limit": 10, "window": "1m"}

    def test_abort_code_default_empty(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        assert ctx.abort_code == ""
        assert ctx.abort_reason == ""
        assert ctx.abort_details == {}

    def test_abort_empty_string_gives_blocked_code(self):
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {})
        ctx.abort()
        assert ctx.abort_code == "BLOCKED"
        assert ctx.abort_reason == ""


class TestHookTiming:
    def test_fire_records_elapsed_ms(self):
        import time

        mgr = HookManager()

        def slow_hook(ctx):
            time.sleep(0.02)

        mgr.register(HookType.BEFORE_TOOL_CALL, slow_hook, name="slow")
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert ctx.elapsed_ms > 0

    def test_slow_hook_triggers_warning(self, caplog):
        import logging
        import time

        mgr = HookManager()
        mgr._slow_threshold_ms = 1  # 1ms threshold for test

        def slow_hook(ctx):
            time.sleep(0.01)

        mgr.register(HookType.BEFORE_TOOL_CALL, slow_hook, name="slow_one")
        with caplog.at_level(logging.WARNING, logger="agentnexus.core.hooks"):
            mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert any("Slow hook chain" in r.message for r in caplog.records)

    def test_fast_hook_no_warning(self, caplog):
        import logging

        mgr = HookManager()

        def fast_hook(ctx):
            pass

        mgr.register(HookType.BEFORE_TOOL_CALL, fast_hook, name="fast_one")
        with caplog.at_level(logging.WARNING, logger="agentnexus.core.hooks"):
            mgr.fire(HookType.BEFORE_TOOL_CALL, {})
        assert not any("slow" in r.message.lower() for r in caplog.records)


