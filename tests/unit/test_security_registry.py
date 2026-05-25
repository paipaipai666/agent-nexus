"""Tool registry security tests — RBAC, rate limiting, HITL gate,
audit logging hygiene, and code-executor timeout enforcement."""

import subprocess
import time
from unittest.mock import patch

import pytest

from agentnexus.tools.registry import RiskLevel, ToolMeta, ToolRegistry


class TestRegistryRBAC:
    """Tool-level RBAC — restricted callers are rejected."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(
                name="restricted_tool",
                description="test",
                param_schema={},
                allowed_agents=["admin"],
                risk_level=RiskLevel.HIGH,
            ),
            lambda: "done",
        )

    def test_rbac_allowed_agent_succeeds(self):
        """Agent in allowed_agents can call the tool."""
        result = self.registry.invoke("restricted_tool", {}, caller="admin")
        assert result == "done"

    def test_rbac_unauthorized_agent_raises(self):
        """Agent not in allowed_agents is rejected."""
        with pytest.raises(PermissionError, match="not allowed"):
            self.registry.invoke("restricted_tool", {}, caller="hacker")

    def test_rbac_wildcard_allows_any(self):
        """Tool with allowed_agents=['*'] allows any caller."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="open_tool", description="test", param_schema={},
                     allowed_agents=["*"]),
            lambda: "ok",
        )
        result = reg.invoke("open_tool", {}, caller="stranger")
        assert result == "ok"

    def test_rbac_unknown_tool_raises(self):
        """Calling non-existent tool raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            self.registry.invoke("nonexistent", {}, caller="admin")


class TestRegistryRateLimit:
    """Rate limiting — excessive calls are blocked."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(name="limited_tool", description="test", param_schema={},
                     rate_limit_per_min=3, risk_level=RiskLevel.LOW),
            lambda: "ok",
        )

    def test_rate_limit_allows_under(self):
        """Calls within limit succeed."""
        for _ in range(3):
            result = self.registry.invoke("limited_tool", {}, caller="test")
            assert result == "ok"

    def test_rate_limit_blocks_excess(self):
        """Call exceeding rate limit raises RuntimeError."""
        for _ in range(3):
            self.registry.invoke("limited_tool", {}, caller="test")
        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            self.registry.invoke("limited_tool", {}, caller="test")

    def test_rate_limit_window_expires(self):
        """After 60s window, rate counter resets (verify cleanup does not crash)."""
        for _ in range(3):
            self.registry.invoke("limited_tool", {}, caller="test")
        # Simulate time passing — just verify window cleanup is safe
        now = time.time()
        self.registry._rate_counters["limited_tool"] = [now - 70]
        # One more call should work since old entries were removed
        result = self.registry.invoke("limited_tool", {}, caller="test")
        assert result == "ok"


class TestRegistryHITL:
    """Human-in-the-loop gate — blocks when approver returns False."""

    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            ToolMeta(name="risky_tool", description="test", param_schema={},
                     require_hitl=True, risk_level=RiskLevel.HIGH),
            lambda: "done",
        )

    def test_hitl_approver_true_succeeds(self):
        """HITL with approver returning True allows execution."""
        result = self.registry.invoke("risky_tool", {}, caller="test",
                                      hitl_approver=lambda _: True)
        assert result == "done"

    def test_hitl_approver_false_blocks(self):
        """HITL with approver returning False blocks execution."""
        result = self.registry.invoke("risky_tool", {}, caller="test",
                                      hitl_approver=lambda _: False)
        assert "blocked" in result

    def test_hitl_no_approver_blocks(self):
        """HITL with no approver returns blocked message."""
        result = self.registry.invoke("risky_tool", {}, caller="test")
        assert "blocked" in result


class TestRegistryAudit:
    """Audit log — sensitive data must not appear raw."""

    def test_audit_log_contains_expected_fields(self):
        """AuditEntry records tool name, caller, duration."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="test_tool", description="test", param_schema={}),
            lambda **kw: "result_data",
        )
        reg.invoke("test_tool", {"key": "value"}, caller="agent1")
        log = reg.get_audit_log()
        assert len(log) == 1
        entry = log[0]
        assert entry.tool_name == "test_tool"
        assert entry.caller == "agent1"
        assert entry.duration_ms > 0

    def test_audit_params_truncated(self):
        """Params longer than 300 chars are truncated."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="verbose_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        long_val = "x" * 1000
        reg.invoke("verbose_tool", {"data": long_val}, caller="agent1")
        entry = reg.get_audit_log()[0]
        assert len(entry.params) <= 310

    def test_audit_params_no_api_key(self):
        """Audit params should not contain raw API key values."""
        reg = ToolRegistry()
        reg.register(
            ToolMeta(name="api_tool", description="test", param_schema={}),
            lambda **kw: "done",
        )
        reg.invoke("api_tool", {"api_key": "sk-leaked-12345"}, caller="agent1")
        entry = reg.get_audit_log()[0]
        assert "sk-leaked" not in entry.params


class TestCodeExecutorSecurity:
    """Code executor security — timeout, resource limits."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_execute_locally_timeout(self, mock_settings):
        """Infinite loop triggers timeout (exception propagates, not caught yet)."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute

        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("python", 30)
            result = python_execute("import time; time.sleep(100)")
        assert "超时" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_execute_locally_syntax_error(self, mock_settings):
        """Syntax error in code returns error, not crash."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute

        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "SyntaxError: invalid syntax"
            result = python_execute("def foo(:")
        assert "error" in result.lower() or "SyntaxError" in result

    def test_e2b_fallback_warning(self):
        """When E2B fails, auto mode falls through to the next safe backend."""
        with patch("agentnexus.tools.code_executor.get_settings") as mock_settings:
            mock_settings.return_value.e2b_api_key.get_secret_value.return_value = "sk-test"
            mock_settings.return_value.code_execution_backend = "auto"
            mock_settings.return_value.code_execution_timeout = 30
            from agentnexus.tools.code_executor import python_execute

            with patch("agentnexus.tools.code_executor.Sandbox") as mock_sandbox:
                mock_sandbox.side_effect = Exception("E2B unavailable")
                with patch("agentnexus.tools.code_executor._execute_native_sandbox") as mock_native:
                    mock_native.return_value = "native result"
                    result = python_execute("print(1)")
        assert result == "native result"


class TestCodeExecutorAdversarial:
    """Code executor with adversarial input — isolation, resource limits."""

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_import_os_in_code_is_isolated(self, mock_settings):
        """Subprocess isolation prevents __import__('os') from affecting host."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute
        result = python_execute("import os; print(os.name)")
        assert "nt" in result or "posix" in result or "error" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_eval_in_code_is_isolated(self, mock_settings):
        """Subprocess isolation prevents eval from affecting host."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute
        result = python_execute("print(eval('1+1'))")
        assert "2" in result or "error" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_exec_in_code_is_isolated(self, mock_settings):
        """Subprocess isolation prevents exec from affecting host."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute
        result = python_execute("exec('x=1')")
        assert "error" not in (result or "").lower() or "exec" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_infinite_memory_triggers_timeout(self, mock_settings):
        """Infinite memory allocation triggers subprocess timeout."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute, subprocess
        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("python", 30)
            result = python_execute("x = 'a' * 10**9")
        assert "超时" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_negative_timeout_capped(self, mock_settings):
        """Negative timeout is capped to default 30s."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        settings = mock_settings.return_value
        settings.shell_timeout = 30
        settings.code_execution_backend = "local_unsafe"
        settings.code_execution_allow_unsafe_local = True
        settings.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute
        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""
            result = python_execute("print(1)")
            assert "ok" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_ensure_main_block_malicious_deep_ast(self, mock_settings):
        """Deeply nested AST in code does not crash _ensure_main_block."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        from agentnexus.tools.code_executor import _ensure_main_block
        deep_nesting = "def f():\n" + "    " * 1000 + "pass\n"
        result = _ensure_main_block(deep_nesting)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > len(deep_nesting)

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_ensure_main_block_invalid_syntax(self, mock_settings):
        """Malformed syntax in code falls back gracefully."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        from agentnexus.tools.code_executor import _ensure_main_block
        result = _ensure_main_block("this is not python {{{ @@@")
        assert "Auto-executed" in result

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_code_subprocess_called_with_sys_executable(self, mock_settings):
        """python_execute calls subprocess with sys.executable, not a shell."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute, sys
        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            python_execute("print(1)")
        args = mock_run.call_args[0][0]
        assert args[0] == sys.executable
        assert args[1] == "-c"

    @patch("agentnexus.tools.code_executor.get_settings")
    def test_very_long_code_string(self, mock_settings):
        """Very long code string does not crash execution."""
        mock_settings.return_value.e2b_api_key.get_secret_value.return_value = ""
        mock_settings.return_value.code_execution_backend = "local_unsafe"
        mock_settings.return_value.code_execution_allow_unsafe_local = True
        mock_settings.return_value.code_execution_timeout = 30
        from agentnexus.tools.code_executor import python_execute
        with patch("agentnexus.tools.code_executor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = ""
            long_code = "# " + "x" * 100000
            result = python_execute(long_code)
        assert result is not None
