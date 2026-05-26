"""End-to-end CLI tests using Typer CliRunner.

Covers:
- Basic command execution (version, config, stats)
- CLI error handling and edge cases
- Command output format validation
"""

import os

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app

runner = CliRunner()


class TestCliBasicCommands:

    def test_version_command(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "AgentNexus" in result.stdout
        assert "v0.1.0" in result.stdout

    def test_help_top_level(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "AgentNexus" in result.stdout or "Usage" in result.stdout

    def test_help_kb(self):
        result = runner.invoke(app, ["kb", "--help"])
        assert result.exit_code == 0
        assert "add" in result.stdout.lower() or "list" in result.stdout.lower()

    def test_help_memory(self):
        result = runner.invoke(app, ["memory", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout.lower() or "clear" in result.stdout.lower()

    def test_help_skill(self):
        result = runner.invoke(app, ["skill", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout.lower() or "use" in result.stdout.lower()

    def test_help_eval(self):
        result = runner.invoke(app, ["eval", "--help"])
        assert result.exit_code == 0

    def test_help_logs(self):
        result = runner.invoke(app, ["logs", "--help"])
        assert result.exit_code == 0


class TestCliWithEnvironment:

    def test_config_show_uses_env_home(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            import agentnexus.core.config as cfg
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["config"])
                assert result.exit_code == 0
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_stats_without_data(self):
        with runner.isolated_filesystem():
            os.makedirs("traces")
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            import agentnexus.core.config as cfg
            cfg._settings_cache = None
            try:
                result = runner.invoke(app, ["stats", "--days", "1"])
                assert result.exit_code == 0
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_memory_list_empty(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            import agentnexus.core.config as cfg
            from agentnexus.memory.long_term import _reset_long_term_memory
            cfg._settings_cache = None
            _reset_long_term_memory()
            try:
                result = runner.invoke(app, ["memory", "list"])
                assert result.exit_code == 0
            finally:
                _reset_long_term_memory()
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]


class TestCliUnknownCommands:

    def test_unknown_command(self):
        result = runner.invoke(app, ["nonexistent_command"])
        assert result.exit_code != 0

    def test_unknown_subcommand(self):
        result = runner.invoke(app, ["kb", "nonexistent"])
        assert result.exit_code != 0


class TestCliContinueFlag:
    """--continue is handled by cli.main() pre-processor, not by Typer.

    CliRunner invokes the Typer app directly, so --continue is unrecognized.
    These tests verify the main() function path instead.
    """

    def test_continue_without_session_through_main(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            import agentnexus.core.config as cfg
            cfg._settings_cache = None
            try:
                from agentnexus.cli import main
                with pytest.raises(SystemExit) as exc:
                    main(["--continue"])
                assert exc.value.code != 0
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]

    def test_continue_with_extra_args_through_main(self):
        with runner.isolated_filesystem():
            os.environ["AGENTNEXUS_HOME"] = os.getcwd()
            import agentnexus.core.config as cfg
            cfg._settings_cache = None
            try:
                from agentnexus.cli import main
                sys_exit = False
                try:
                    main(["--continue", "sid", "extra"])
                except SystemExit:
                    sys_exit = True
                assert sys_exit, "Expected SystemExit for bad --continue args"
            finally:
                cfg._settings_cache = None
                del os.environ["AGENTNEXUS_HOME"]
