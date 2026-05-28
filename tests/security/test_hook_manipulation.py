"""Security: Hook manipulation and privilege escalation tests.

Tests that hooks can't be used to escalate privileges and that the
hook system behaves correctly under adversarial conditions.
"""

from agentnexus.core.hooks import (
    HookContext,
    HookManager,
    HookType,
    _reset_hook_manager,
)


class TestHookAbortExecution:
    """BEFORE_TOOL_CALL hook can abort execution."""

    def setup_method(self):
        _reset_hook_manager()

    def teardown_method(self):
        _reset_hook_manager()

    def test_hook_can_abort_execution(self):
        """A BEFORE_TOOL_CALL hook that aborts prevents tool execution."""
        mgr = HookManager()

        def blocker(ctx: HookContext):
            ctx.abort("blocked by policy")

        mgr.register(HookType.BEFORE_TOOL_CALL, blocker)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "dangerous_tool"})
        assert ctx.aborted
        assert ctx.abort_reason == "blocked by policy"
        assert ctx.abort_code == "BLOCKED"

    def test_hook_abort_with_structured_code(self):
        """Hook can abort with structured code and details."""
        mgr = HookManager()

        def blocker(ctx: HookContext):
            ctx.abort(
                code="PERMISSION_DENIED",
                message="insufficient privileges",
                details={"required_role": "admin"},
            )

        mgr.register(HookType.BEFORE_TOOL_CALL, blocker)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "admin_tool"})
        assert ctx.aborted
        assert ctx.abort_code == "PERMISSION_DENIED"
        assert ctx.abort_reason == "insufficient privileges"
        assert ctx.abort_details == {"required_role": "admin"}

    def test_hook_no_abort_passes_through(self):
        """Hook that doesn't abort allows execution to continue."""
        mgr = HookManager()

        def auditor(ctx: HookContext):
            pass

        mgr.register(HookType.BEFORE_TOOL_CALL, auditor)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "safe_tool"})
        assert not ctx.aborted


class TestHookParamModification:
    """Hook modifying params is respected (by design)."""

    def setup_method(self):
        _reset_hook_manager()

    def teardown_method(self):
        _reset_hook_manager()

    def test_hook_can_modify_payload(self):
        """A hook can modify the payload dict (mutable by design)."""
        mgr = HookManager()

        def modifier(ctx: HookContext):
            ctx.payload["injected_key"] = "injected_value"

        mgr.register(HookType.BEFORE_TOOL_CALL, modifier)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool", "original": True})
        assert ctx.payload["injected_key"] == "injected_value"
        assert ctx.payload["original"] is True

    def test_hook_can_overwrite_payload_values(self):
        """A hook can overwrite existing payload values."""
        mgr = HookManager()

        def rewriter(ctx: HookContext):
            ctx.payload["name"] = "different_tool"

        mgr.register(HookType.BEFORE_TOOL_CALL, rewriter)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "original_tool"})
        assert ctx.payload["name"] == "different_tool"


class TestHookChain:
    """Multiple hooks fire in priority order."""

    def setup_method(self):
        _reset_hook_manager()

    def teardown_method(self):
        _reset_hook_manager()

    def test_multiple_hooks_fire_in_priority_order(self):
        """Hooks with different priorities fire in ascending priority order."""
        mgr = HookManager()
        call_order = []

        def hook_a(ctx: HookContext):
            call_order.append("a")

        def hook_b(ctx: HookContext):
            call_order.append("b")

        def hook_c(ctx: HookContext):
            call_order.append("c")

        mgr.register(HookType.BEFORE_TOOL_CALL, hook_a, name="a", priority=300)
        mgr.register(HookType.BEFORE_TOOL_CALL, hook_b, name="b", priority=100)
        mgr.register(HookType.BEFORE_TOOL_CALL, hook_c, name="c", priority=200)

        mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool"})
        assert call_order == ["b", "c", "a"]

    def test_abort_stops_hook_chain(self):
        """Aborting hook prevents subsequent hooks from firing."""
        mgr = HookManager()
        call_order = []

        def blocker(ctx: HookContext):
            call_order.append("blocker")
            ctx.abort("stop")

        def after_blocker(ctx: HookContext):
            call_order.append("after")

        mgr.register(HookType.BEFORE_TOOL_CALL, blocker, name="blocker", priority=100)
        mgr.register(HookType.BEFORE_TOOL_CALL, after_blocker, name="after", priority=200)

        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool"})
        assert call_order == ["blocker"]
        assert ctx.aborted


class TestFireWithNoHooks:
    """fire() with no registered hooks works fine."""

    def setup_method(self):
        _reset_hook_manager()

    def teardown_method(self):
        _reset_hook_manager()

    def test_fire_empty_returns_context(self):
        """fire() with no hooks returns a non-aborted context."""
        mgr = HookManager()
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool"})
        assert not ctx.aborted
        assert ctx.elapsed_ms >= 0

    def test_fire_wrong_hook_type_returns_context(self):
        """fire() for a hook type with no registered hooks returns fine."""
        mgr = HookManager()
        mgr.register(
            HookType.AFTER_TOOL_CALL,
            lambda ctx: None,
            name="after_hook",
        )
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool"})
        assert not ctx.aborted

    def test_disabled_hook_is_skipped(self):
        """Disabled hooks are not fired."""
        mgr = HookManager()
        called = []

        def hook(ctx: HookContext):
            called.append(True)

        name = mgr.register(HookType.BEFORE_TOOL_CALL, hook, name="h1")
        mgr.disable(name)
        mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "tool"})
        assert called == []
