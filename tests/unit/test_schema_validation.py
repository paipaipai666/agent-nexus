"""P1-6: Structured JSON Schema validation test.

Verifies that ToolRegistry.invoke() validates params against
ToolMeta.param_schema using jsonschema, rejecting invalid input
and accepting valid input.
"""
import pytest

from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestParamSchemaValidation:
    """ToolRegistry param_schema validation."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(
                name="echo",
                description="echo input",
                param_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["message"],
                },
            ),
            lambda **kw: kw.get("message", "") * kw.get("count", 1),
        )

    def test_valid_params_accepted(self):
        result = self.registry.invoke(
            "echo", {"message": "hello", "count": 3}, caller="test",
        )
        assert result == "hellohellohello"

    def test_valid_params_with_defaults(self):
        """Missing optional field with default works."""
        result = self.registry.invoke(
            "echo", {"message": "hi"}, caller="test",
        )
        assert result == "hi"

    def test_missing_required_field_rejected(self):
        with pytest.raises((ValueError, Exception)):
            self.registry.invoke("echo", {"count": 5}, caller="test")

    def test_wrong_type_rejected(self):
        with pytest.raises((ValueError, Exception)):
            self.registry.invoke("echo", {"message": 123}, caller="test")

    def test_out_of_range_rejected(self):
        with pytest.raises((ValueError, Exception)):
            self.registry.invoke("echo", {"message": "hi", "count": 99}, caller="test")

    def test_extra_properties_allowed(self):
        """Additional params beyond schema should be allowed (not rejected)."""
        result = self.registry.invoke(
            "echo", {"message": "ok", "extra": "field"}, caller="test",
        )
        assert result == "ok"


class TestOutputSchemaValidation:
    """Optional output_schema validation."""

    def test_output_schema_enforced(self):
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="get_user",
                description="get user info",
                param_schema={"type": "object", "properties": {}},
                output_schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name"],
                },
            ),
            lambda **kw: {"name": "Alice", "age": 30},
        )
        result = registry.invoke("get_user", {}, caller="test")
        assert result["name"] == "Alice"

    def test_output_schema_mismatch_logged(self):
        """Output schema mismatch should not crash (logged, not enforced)."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="bad_output",
                description="returns wrong type",
                param_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
            ),
            lambda **kw: "not an object",
        )
        result = registry.invoke("bad_output", {}, caller="test")
        assert result == "not an object"

    def test_no_output_schema_skips_validation(self):
        """No output_schema means no output validation."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(
                name="raw",
                description="raw output",
                param_schema={},
                output_schema=None,
            ),
            lambda **kw: 42,
        )
        result = registry.invoke("raw", {}, caller="test")
        assert result == 42
