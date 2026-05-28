"""Security: Rate limiting bypass tests.

Tests that rate limiting in ToolRegistry cannot be bypassed
through concurrent calls or parameter manipulation.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestToolRegistryRateLimiting:
    """ToolRegistry rate limiting is enforced correctly."""

    def test_rate_limit_blocks_after_threshold(self):
        """Rate limit blocks calls exceeding the per-minute limit."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="limited_tool",
                description="test",
                param_schema={},
                rate_limit_per_min=3,
            ),
            lambda **kw: "ok",
        )
        for _ in range(3):
            registry.invoke("limited_tool", {}, caller="agent")

        try:
            registry.invoke("limited_tool", {}, caller="agent")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Rate limit" in str(e)

    def test_rate_limit_is_per_tool(self):
        """Rate limits are independent per tool name."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="tool_a",
                description="test",
                param_schema={},
                rate_limit_per_min=1,
            ),
            lambda **kw: "a",
        )
        registry.register(
            ToolMeta(
                name="tool_b",
                description="test",
                param_schema={},
                rate_limit_per_min=1,
            ),
            lambda **kw: "b",
        )
        assert registry.invoke("tool_a", {}, caller="agent") == "a"
        assert registry.invoke("tool_b", {}, caller="agent") == "b"

    def test_rate_limit_with_unlimited(self):
        """Tools with rate_limit_per_min=0 have no limit."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="unlimited_tool",
                description="test",
                param_schema={},
                rate_limit_per_min=0,
            ),
            lambda **kw: "ok",
        )
        for _ in range(100):
            registry.invoke("unlimited_tool", {}, caller="agent")

    def test_concurrent_calls_respect_rate_limit(self):
        """Concurrent calls from multiple threads respect rate limit."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="concurrent_tool",
                description="test",
                param_schema={},
                rate_limit_per_min=5,
            ),
            lambda **kw: "ok",
        )

        errors = []
        successes = []

        def call_tool():
            try:
                registry.invoke("concurrent_tool", {}, caller="agent")
                return True
            except RuntimeError as e:
                if "Rate limit" in str(e):
                    return False
                raise

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(call_tool) for _ in range(10)]
            for f in as_completed(futures):
                result = f.result()
                if result:
                    successes.append(True)
                else:
                    errors.append(True)

        assert len(successes) <= 5


class TestToolInvokeCaller:
    """verify tool invoke respects caller parameter."""

    def test_rbac_blocks_unauthorized_caller(self):
        """Tool with restricted allowed_agents blocks unauthorized callers."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="restricted",
                description="test",
                param_schema={},
                allowed_agents=["admin"],
            ),
            lambda **kw: "secret",
        )
        try:
            registry.invoke("restricted", {}, caller="user")
            assert False, "Should have raised PermissionError"
        except PermissionError as e:
            assert "not allowed" in str(e)

    def test_rbac_allows_authorized_caller(self):
        """Tool with restricted allowed_agents allows authorized callers."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="restricted2",
                description="test",
                param_schema={},
                allowed_agents=["admin"],
            ),
            lambda **kw: "secret",
        )
        result = registry.invoke("restricted2", {}, caller="admin")
        assert result == "secret"

    def test_wildcard_allows_all_callers(self):
        """Tool with allowed_agents=['*'] allows any caller."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="open_tool",
                description="test",
                param_schema={},
                allowed_agents=["*"],
            ),
            lambda **kw: "ok",
        )
        assert registry.invoke("open_tool", {}, caller="anyone") == "ok"

    def test_disabled_tool_blocks_invoke(self):
        """Disabled tool raises PermissionError."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="disabled_tool",
                description="test",
                param_schema={},
                enabled=False,
            ),
            lambda **kw: "ok",
        )
        try:
            registry.invoke("disabled_tool", {}, caller="agent")
            assert False, "Should have raised PermissionError"
        except PermissionError as e:
            assert "disabled" in str(e)
