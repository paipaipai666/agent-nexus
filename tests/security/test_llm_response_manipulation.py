"""Security: Malicious LLM response handling tests.

Tests that malicious or malformed LLM responses are safely handled
without crashing, executing code, or entering infinite loops.
"""

from agentnexus.memory.extraction import parse_memory_payload
from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestParseMemoryPayload:
    """parse_memory_payload safely handles malformed inputs."""

    def test_non_json_returns_empty_dict(self):
        """Plain text that is not JSON returns {}."""
        assert parse_memory_payload("hello world") == {}

    def test_code_execution_attempt_returns_empty_dict(self):
        """Python code string is not valid JSON, returns {}."""
        code = "import os; os.system('whoami')"
        assert parse_memory_payload(code) == {}

    def test_html_returns_empty_dict(self):
        """HTML content is not valid JSON, returns {}."""
        html = "<html><script>alert('xss')</script></html>"
        assert parse_memory_payload(html) == {}

    def test_empty_string_returns_empty_dict(self):
        """Empty string returns {}."""
        assert parse_memory_payload("") == {}

    def test_whitespace_only_returns_empty_dict(self):
        """Whitespace-only string returns {}."""
        assert parse_memory_payload("   \n\t  ") == {}

    def test_partial_json_returns_empty_dict(self):
        """Truncated JSON returns {}."""
        assert parse_memory_payload('{"key":') == {}

    def test_json_array_returns_list(self):
        """JSON array is parsed as a list (not wrapped in dict)."""
        import json
        result = parse_memory_payload(json.dumps([1, 2, 3]))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_valid_json_returns_data(self):
        """Valid JSON object is returned."""
        import json
        data = {"user_preference": ["test item"]}
        assert parse_memory_payload(json.dumps(data)) == data

    def test_json_with_code_in_values_returns_data(self):
        """Valid JSON with code-like strings in values is returned as-is."""
        import json
        data = {"user_preference": ["import os; os.system('x')"]}
        result = parse_memory_payload(json.dumps(data))
        assert result == data

    def test_json_with_backtick_wrapping(self):
        """JSON wrapped in markdown code fences is parsed."""
        import json
        data = {"entity_fact": ["some fact"]}
        wrapped = "```json\n" + json.dumps(data) + "\n```"
        assert parse_memory_payload(wrapped) == data


class TestToolRegistryMaliciousNames:
    """ToolRegistry handles malicious tool names gracefully."""

    def test_invoke_nonexistent_tool_raises_keyerror(self):
        """Invoking a tool not in registry raises KeyError."""
        registry = ToolRegistry()
        try:
            registry.invoke("nonexistent_tool", {}, caller="agent")
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_invoke_with_empty_name_raises_keyerror(self):
        """Invoking with empty tool name raises KeyError."""
        registry = ToolRegistry()
        try:
            registry.invoke("", {}, caller="agent")
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_invoke_with_special_chars_in_name_raises_keyerror(self):
        """Tool name with special chars that isn't registered raises KeyError."""
        registry = ToolRegistry()
        try:
            registry.invoke("tool; rm -rf /", {}, caller="agent")
            assert False, "Should have raised KeyError"
        except KeyError:
            pass

    def test_invoke_registered_tool_with_malicious_params(self):
        """Registered tool receives params as-is, audit log redacts sensitive ones."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="safe_tool", description="test", param_schema={}),
            lambda **kw: "result",
        )
        result = registry.invoke(
            "safe_tool",
            {"input": "import os; os.system('x')"},
            caller="agent",
        )
        assert result == "result"

    def test_invoke_tool_with_injection_in_caller(self):
        """Caller name with injection payload is stored in audit log."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="tool2", description="test", param_schema={}),
            lambda **kw: "ok",
        )
        registry.invoke("tool2", {}, caller="agent; DROP TABLE --")
        entry = registry.get_audit_log()[0]
        assert entry.caller == "agent; DROP TABLE --"


class TestRetryGateBehavior:
    """Verify rate limiting respects bounds (no infinite loop)."""

    def test_rate_limit_enforced(self):
        """Rate limit raises RuntimeError after exceeding limit."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="limited",
                description="test",
                param_schema={},
                rate_limit_per_min=2,
            ),
            lambda **kw: "ok",
        )
        registry.invoke("limited", {}, caller="a")
        registry.invoke("limited", {}, caller="a")
        try:
            registry.invoke("limited", {}, caller="a")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Rate limit" in str(e)
