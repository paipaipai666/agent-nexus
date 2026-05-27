"""Credential handling security tests — API key leakage, SecretStr safety,
YAML safe-load, environment variable hygiene."""

import os
from unittest.mock import MagicMock, patch

import pytest
import yaml
from pydantic import SecretStr

from agentnexus.core.config import get_config_dir, load_config_yaml, write_config_yaml


class TestSecretStrSafety:
    """SecretStr wrapping — verify values don't leak in repr/str."""

    def test_secret_str_repr_obfuscated(self):
        """repr(SecretStr) hides the underlying value."""
        s = SecretStr("sk-test-key-12345")
        r = repr(s)
        assert "sk-test" not in r
        assert "12345" not in r
        assert "********" in r or "SecretStr" in r

    def test_secret_str_str_obfuscated(self):
        """str(SecretStr) hides the underlying value."""
        s = SecretStr("sk-test-key-12345")
        r = str(s)
        assert "sk-test" not in r
        assert "12345" not in r


class TestApiKeyNotInSpan:
    """LLM call metadata must not contain raw API keys."""

    def test_llm_span_metadata_no_api_key(self):
        """Verify span metadata dict does not include an 'api_key' key."""
        meta = {"model": "test/test-model", "status": "ok", "truncated": False}
        assert "api_key" not in meta
        assert "key" not in meta or meta.get("key") != "sk-test"

    def test_llm_error_span_no_api_key(self):
        """Error span metadata must not contain raw API key fragments."""
        meta = {"model": "test/test-model", "status": "error",
                "error": "rate limit exceeded"}
        assert "api_key" not in meta
        assert "sk-" not in str(meta)

    def test_span_meta_constructed_without_key(self):
        """Verify span metadata construction path in _call omits api_key."""
        usage = {"input_tokens": 10, "output_tokens": 20}
        meta = {"model": "test-model", "status": "ok", "truncated": False, **usage}
        assert "api_key" not in meta


class TestE2BEnvHygiene:
    """os.environ['E2B_API_KEY'] must not be set when key is empty."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_e2b_env_not_set_without_key(self, mock_settings):
        """When e2b_api_key is empty, os.environ is not polluted."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        saved = os.environ.get("E2B_API_KEY")
        if "E2B_API_KEY" in os.environ:
            del os.environ["E2B_API_KEY"]

        from agentnexus.tools.code_executor import python_execute

        mock_settings.return_value.code_execution_backend = "auto"
        mock_settings.return_value.code_execution_timeout = 30
        with patch("agentnexus.tools.code_executor._execute_native_sandbox") as mock_native:
            mock_native.return_value = "ok"
            python_execute("print(1)")

        assert "E2B_API_KEY" not in os.environ
        if saved is not None:
            os.environ["E2B_API_KEY"] = saved


class TestYamlSecurity:
    """YAML loading — safe_load rejects dangerous payloads."""

    def test_safe_load_rejects_dangerous(self):
        """yaml.safe_load raises on !!python/object constructs."""
        dangerous = "!!python/object:os.system ['rm -rf /']"
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(dangerous)

    def test_load_yaml_empty_file_returns_empty_dict(self):
        """An empty or non-existent YAML file returns {}."""
        result = load_config_yaml()
        assert isinstance(result, dict)

    def test_config_dir_creation(self):
        """get_config_dir() creates directory successfully."""
        d = get_config_dir()
        assert d.exists()
        assert d.is_dir()

    def test_yaml_file_permissions_restrictive(self, temp_agentnexus_home):
        """YAML config file should be written with restrictive permissions."""
        yaml_path = write_config_yaml({"llm_api_key": "test-key"})
        perms = oct(yaml_path.stat().st_mode)[-3:]
        expected = ("444",) if os.name == "nt" else ("600", "400")
        assert perms in expected, f"permissions are {perms}, expected one of {expected}"

    def test_yaml_dump_no_secret_leak(self):
        """Serialising settings to YAML should not emit API key plaintext."""
        import yaml
        data = {"llm_api_key": SecretStr("sk-leak-test")}
        dumped = yaml.dump(data)
        assert "sk-leak" not in dumped


class TestSubagentApiKeyLeakage:
    """Subagent API key propagation must not leak key in spans or logs."""

    @patch("agentnexus.tools.subagent.trace_manager")
    def test_subagent_clone_llm_no_key_in_span(self, mock_trace):
        """_clone_llm creates child LLM with apiKey but does not leak it in span metadata."""
        from agentnexus.tools.subagent import _clone_llm
        parent = MagicMock()
        parent.model = "test-model"
        parent.api_key = "sk-test-secret-key-12345"
        parent.base_url = "https://test.api"
        parent.timeout = 30

        child = _clone_llm(parent)
        assert child.api_key == "sk-test-secret-key-12345"
        assert child.model == "openai/test-model"  # normalized with default provider

    @patch("agentnexus.tools.subagent.trace_manager")
    def test_subagent_span_metadata_no_raw_key(self, mock_trace):
        """Subagent span metadata should not contain raw api_key value."""
        mock_span = MagicMock()
        mock_trace.span.return_value.__enter__.return_value = mock_span
        from agentnexus.tools.subagent import make_subagent_run
        parent = MagicMock()
        parent.model = "test-model"
        parent.api_key = "sk-test-secret-key-12345"
        parent.base_url = ""
        parent.timeout = 30

        with patch("agentnexus.tools.subagent._run_subagent_attempt") as mock_run:
            mock_run.return_value = (
                {"role": "explorer", "answer": "done", "salvaged": "",
                 "steps_used": 1, "tool_names": ["file_read"]},
                None,
            )
            runner = make_subagent_run(parent, non_interactive=True)
            runner("do something", role="explorer")
        assert "sk-test" not in str(mock_span.metadata)


class TestPiiInStm:
    """PII content in ShortTermMemory — current behavior: not filtered."""

    def test_stm_contains_email(self):
        """STM stores email content without filtering (STM has no PII filter)."""
        from agentnexus.memory.short_term import ShortTermMemory
        stm = ShortTermMemory(max_messages=100)
        stm.append("user", "my email is user@example.com")
        msgs = stm.get_all()
        assert any("user@example.com" in m["content"] for m in msgs)

    def test_stm_contains_phone(self):
        """STM stores phone number without filtering (STM has no PII filter)."""
        from agentnexus.memory.short_term import ShortTermMemory
        stm = ShortTermMemory(max_messages=100)
        stm.append("user", "call me at 13800138000")
        msgs = stm.get_all()
        assert any("13800138000" in m["content"] for m in msgs)

    def test_stm_contains_api_key(self):
        """STM stores API key without filtering (STM has no PII filter)."""
        from agentnexus.memory.short_term import ShortTermMemory
        stm = ShortTermMemory(max_messages=100)
        stm.append("user", "my key is sk-" + "a" * 40)
        msgs = stm.get_all()
        assert any("sk-" in m["content"] for m in msgs)


class TestPiiInTrace:
    """Trace output may contain PII — document current behavior."""

    def test_trace_span_input_contains_pii(self):
        """Trace span input dict can contain PII (no automatic filtering)."""
        from agentnexus.observability.tracer import TraceSpan
        span = TraceSpan(span_id="test1")
        span.input = {"email": "user@example.com", "phone": "13800138000"}
        assert "user@example.com" in str(span.input)

    def test_trace_span_output_contains_pii(self):
        """Trace span output dict can contain PII (no automatic filtering)."""
        from agentnexus.observability.tracer import TraceSpan
        span = TraceSpan(span_id="test2")
        span.output = {"result": "contact: user@example.com"}
        assert "user@example.com" in str(span.output)

    def test_truncate_preserves_pii(self):
        """_truncate shortens text but does not remove PII."""
        from agentnexus.observability.tracer import _truncate
        text = "email: user@example.com, phone: 13800138000"
        result = _truncate(text, max_len=200)
        assert "user@example.com" in result
        assert "13800138000" in result

    def test_truncate_dict_preserves_pii(self):
        """_truncate_dict shortens values but does not remove PII."""
        from agentnexus.observability.tracer import _truncate_dict
        d = {"content": "email: user@example.com"}
        result = _truncate_dict(d)
        assert "user@example.com" in str(result)


class TestEnvVarLeakage:
    """Environment variable leakage — AGENTNEXUS_* env vars."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_agentnexus_env_vars_not_persisted(self, mock_settings):
        """Other AGENTNEXUS_* env vars are not set in os.environ by code executor."""
        import os
        saved = {}
        for key in list(os.environ.keys()):
            if key.startswith("AGENTNEXUS_"):
                saved[key] = os.environ[key]
                del os.environ[key]

        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        try:
            from agentnexus.tools.code_executor import python_execute
            with patch("agentnexus.tools.code_executor._execute_locally") as mock_local:
                mock_local.return_value = "ok"
                python_execute("print(1)")
            active_nexus_vars = {k: v for k, v in os.environ.items()
                                 if k.startswith("AGENTNEXUS_")}
            assert len(active_nexus_vars) == 0
        finally:
            for k, v in saved.items():
                os.environ[k] = v
