"""Security: API key leak prevention in traces, logs, and config output.

Tests that sensitive values (API keys, passwords, tokens, secrets) are
properly masked or truncated in trace spans, audit logs, YAML config,
and console output.
"""

import os

import yaml
from pydantic import SecretStr

from agentnexus.core import config as agentnexus_config
from agentnexus.core.config import (
    AgentNexusDumper,
    _dump_secret_str,
    _write_yaml_config,
    get_settings,
)
from agentnexus.observability.tracer import _truncate, _truncate_dict
from agentnexus.tools.registry import ToolMeta, ToolRegistry


class TestTraceTruncation:
    """_truncate and _truncate_dict prevent long/secret data leakage."""

    def test_truncate_limits_string_length(self):
        """_truncate limits strings to default max_len (5000 chars)."""
        short = "hello"
        assert _truncate(short) == short

        long_str = "x" * 10000
        result = _truncate(long_str)
        assert len(result) < len(long_str)
        assert "...[截断" in result

    def test_truncate_preserves_short_strings(self):
        """_truncate returns short strings unchanged."""
        text = "short string"
        assert _truncate(text) == text

    def test_truncate_dict_truncates_long_values(self):
        """_truncate_dict truncates values that exceed max_len."""
        d = {"key": "short", "long": "x" * 10000}
        result = _truncate_dict(d, max_len=100)
        assert result["key"] == "short"
        assert len(result["long"]) < 10000
        assert "...[截断" in result["long"]

    def test_truncate_dict_handles_empty_dict(self):
        """_truncate_dict handles empty dict."""
        assert _truncate_dict({}) == {}

    def test_truncate_dict_handles_numeric_values(self):
        """_truncate_dict converts non-string values to string before truncation."""
        d = {"number": 42, "list": [1, 2, 3], "none": None}
        result = _truncate_dict(d, max_len=5000)
        assert result["number"] == "42"
        assert result["list"] == "[1, 2, 3]"

    def test_truncate_with_custom_max_len(self):
        """_truncate respects custom max_len parameter."""
        text = "x" * 200
        result = _truncate(text, max_len=50)
        assert len(result) < 100
        assert "...[截断" in result


class TestSecretMasking:
    """SecretStr and _dump_secret_str mask sensitive values."""

    def test_secret_str_masks_value(self):
        """SecretStr displays as masked value (******)."""
        secret = SecretStr("sk-test-api-key-12345")
        display = str(secret)
        assert "sk-test-api-key-12345" not in display
        # Pydantic SecretStr displays with asterisks
        assert "*" in display

    def test_secret_str_get_secret_value_returns_actual(self):
        """SecretStr.get_secret_value() returns the actual value."""
        secret = SecretStr("sk-test-api-key-12345")
        assert secret.get_secret_value() == "sk-test-api-key-12345"

    def test_dump_secret_str_masks_output(self):
        """_dump_secret_str outputs masked value for SecretStr."""
        from io import StringIO

        secret = SecretStr("sk-very-secret-key")
        stream = StringIO()
        dumper = AgentNexusDumper(stream)
        scalar = _dump_secret_str(dumper, secret)
        assert "sk-very-secret-key" not in scalar.value


class TestConfigSecretLeakage:
    """Config YAML write and settings display don't leak secrets."""

    def test_config_yaml_write_masks_api_keys(self, temp_agentnexus_home):
        """YAML output doesn't contain raw API keys when stored as SecretStr."""
        config_data = {
            "llm_api_key": SecretStr("sk-real-key-12345"),
            "judge_api_key": SecretStr("judge-key-67890"),
            "tavily_api_key": SecretStr("tavily-key-abcde"),
            "e2b_api_key": SecretStr("e2b-key-fghij"),
        }
        config_path = _write_yaml_config(config_data)

        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()

        assert "sk-real-key-12345" not in raw
        assert "judge-key-67890" not in raw
        assert "tavily-key-abcde" not in raw
        assert "e2b-key-fghij" not in raw

    def test_settings_repr_masks_secrets(self, temp_agentnexus_home):
        """Settings __repr__ shows masked values for SecretStr fields."""
        os.environ["AGENTNEXUS_LLM_API_KEY"] = "sk-env-key-99999"
        os.environ["AGENTNEXUS_TAVILY_API_KEY"] = "tavily-env-key"
        agentnexus_config._settings_cache = None
        settings = get_settings()

        repr_str = repr(settings)
        assert "sk-env-key-99999" not in repr_str
        assert "tavily-env-key" not in repr_str
        assert "******" in repr_str or "SecretStr(" in repr_str

    def test_settings_str_masks_secrets(self, temp_agentnexus_home):
        """Settings __str__ shows masked values for SecretStr fields."""
        os.environ["AGENTNEXUS_LLM_API_KEY"] = "sk-str-leak-test"
        agentnexus_config._settings_cache = None
        settings = get_settings()

        str_val = str(settings)
        assert "sk-str-leak-test" not in str_val

    def test_yaml_dump_with_secret_str_is_safe(self, tmp_path):
        """yaml.dump with AgentNexusDumper masks SecretStr values."""
        data = {"api_key": SecretStr("raw-secret-value")}
        output_path = tmp_path / "test_config.yaml"
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, Dumper=AgentNexusDumper, allow_unicode=True)

        raw = output_path.read_text(encoding="utf-8")
        assert "raw-secret-value" not in raw
        assert "******" in raw


class TestRegistrySecretRedaction:
    """ToolRegistry redacts sensitive params from audit log and span input."""

    def test_audit_log_strips_api_key(self):
        """Audit log params don't contain raw API key values."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="test_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("test_tool", {"api_key": "sk-leaked-12345"}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "sk-leaked" not in entry.params
        assert "[REDACTED]" in entry.params

    def test_audit_log_strips_password(self):
        """Audit log strips password fields."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="login_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("login_tool", {"password": "p@ssw0rd!"}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "p@ssw0rd" not in entry.params

    def test_audit_log_strips_token(self):
        """Audit log strips token fields."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="auth_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("auth_tool", {"token": "eyJhbGciOiJIUzI1NiJ9"}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "eyJhbGci" not in entry.params

    def test_audit_log_strips_secret(self):
        """Audit log strips arbitrary secret fields."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="config_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("config_tool", {"client_secret": "super-secret-value"}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "super-secret-value" not in entry.params

    def test_audit_log_strips_authorization_header(self):
        """Audit log strips authorization header values."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="http_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("http_tool", {"authorization": "Bearer sk-test-token"}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "Bearer" not in entry.params

    def test_audit_log_preserves_non_sensitive_params(self):
        """Audit log preserves non-sensitive parameter values."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="safe_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("safe_tool", {"query": "hello world", "limit": 10}, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "hello world" in entry.params
        assert "10" in entry.params

    def test_sensitive_params_in_nested_dict_redacted(self):
        """Nested dicts with sensitive keys are redacted."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="nested_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("nested_tool", {
            "credentials": {"api_key": "nested-secret", "username": "user1"},
            "data": {"value": 42},
        }, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "nested-secret" not in entry.params
        assert "user1" in entry.params

    def test_sensitive_params_in_list_redacted(self):
        """Lists with sensitive dict entries are redacted."""
        registry = ToolRegistry()
        registry.register(
            ToolMeta(name="list_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        registry.invoke("list_tool", {
            "items": [{"api_key": "secret1"}, {"name": "public"}],
        }, caller="agent1")
        entry = registry.get_audit_log()[0]
        assert "secret1" not in entry.params
        assert "public" in entry.params


class TestRedactSensitiveParams:
    """_redact_sensitive_params core function behavior."""

    def test_redact_sensitive_params_detects_various_key_formats(self):
        """_redact_sensitive_params normalizes key casing and separators."""
        from agentnexus.tools.registry import _redact_sensitive_params

        params = {
            "API_KEY": "value1",
            "api-key": "value2",
            "ApiKey": "value3",
            "apikey": "value4",
        }
        result = _redact_sensitive_params(params)
        for v in ["value1", "value2", "value3", "value4"]:
            assert v not in str(result)
        for k in params:
            assert result[k] == "[REDACTED]"

    def test_redact_sensitive_params_preserves_normal_keys(self):
        """Normal keys are preserved unchanged."""
        from agentnexus.tools.registry import _redact_sensitive_params

        params = {"query": "search term", "limit": 10, "temperature": 0.7}
        result = _redact_sensitive_params(params)
        assert result["query"] == "search term"
        assert result["limit"] == 10
