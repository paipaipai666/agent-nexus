"""Behavioral tests for the command hook system (M1).

Covers:
- HookConfig schema validation (event names, on_failure semantics, matcher regex)
- Subprocess executor contract (exit 0/2/non-zero, timeout, env scrub, update merge)
- Source layering + hash trust review (project hooks skipped until approved)
- HookManager.fire integration (command hooks run after in-process chain,
  in-process abort short-circuits them)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from agentnexus.core import hook_sources
from agentnexus.core.config import get_settings
from agentnexus.core.hook_executor import execute_command_hook, run_command_hooks_for
from agentnexus.core.hook_schemas import HookConfig
from agentnexus.core.hooks import HookContext, HookManager, HookType
from agentnexus.tools.workspace import current_workspace


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("AGENTNEXUS_HOME", str(home_dir))
    return home_dir


@pytest.fixture
def workspace(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    token = current_workspace.set(str(proj))
    yield proj
    current_workspace.reset(token)


def make_hook_script(tmp_path: Path, code: str) -> str:
    script = tmp_path / f"hook_{abs(hash(code)) % 10_000}.py"
    script.write_text(code, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def set_config_hooks(monkeypatch, hooks: list[HookConfig]) -> None:
    from agentnexus.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "hooks", hooks, raising=False)
    monkeypatch.setattr(settings, "hooks_enabled", True, raising=False)
    monkeypatch.setattr(settings, "hook_trust", "strict", raising=False)


class TestHookConfigSchema:
    def test_valid_config(self):
        cfg = HookConfig(event="before_tool_call", command="echo hi")
        assert cfg.timeout == 10.0 and cfg.on_failure == "warn" and not cfg.async_

    def test_unknown_event_rejected(self):
        with pytest.raises(ValueError, match="unknown hook event"):
            HookConfig(event="before_everything", command="x")

    def test_block_on_observer_event_rejected(self):
        with pytest.raises(ValueError, match="observer events cannot block"):
            HookConfig(event="after_tool_call", command="x", on_failure="block")

    def test_block_allowed_on_before_event(self):
        cfg = HookConfig(event="before_tool_call", command="x", on_failure="block")
        assert cfg.on_failure == "block"

    def test_invalid_matcher_rejected(self):
        with pytest.raises(ValueError, match="invalid matcher"):
            HookConfig(event="before_tool_call", command="x", matcher="([unclosed")

    def test_async_alias_from_yaml_key(self):
        cfg = HookConfig.model_validate(
            {"event": "after_tool_call", "command": "x", "async": True}
        )
        assert cfg.async_ is True

    def test_matcher_against_payload_name(self):
        cfg = HookConfig(event="before_tool_call", command="x", matcher=r"^shell")
        assert cfg.matches("before_tool_call", {"name": "shell_exec"})
        assert not cfg.matches("before_tool_call", {"name": "file_read"})
        assert not cfg.matches("after_tool_call", {"name": "shell_exec"})


class TestExecutorContract:
    def test_exit_zero_update_merges_into_payload(self, home, workspace, monkeypatch):
        cfg = HookConfig(
            event="before_tool_call",
            command=make_hook_script(
                workspace, "import json; print(json.dumps({'update': {'approved': True}}))"
            ),
        )
        set_config_hooks(monkeypatch, [cfg])
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"name": "shell_exec"})
        run_command_hooks_for(ctx)
        assert ctx.payload["approved"] is True
        assert not ctx.aborted

    def test_exit_two_blocks_with_stderr_reason(self, home, workspace, monkeypatch):
        cfg = HookConfig(
            event="before_tool_call",
            command=make_hook_script(workspace, "import sys; sys.stderr.write('no .env'); sys.exit(2)"),
        )
        set_config_hooks(monkeypatch, [cfg])
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"name": "file_write"})
        run_command_hooks_for(ctx)
        assert ctx.aborted
        assert ".env" in ctx.abort_reason
        assert ctx.abort_code == "BLOCKED"

    def test_nonzero_exit_warn_continues_by_default(self, home, workspace, monkeypatch):
        cfg = HookConfig(
            event="before_tool_call",
            command=make_hook_script(workspace, "import sys; sys.stderr.write('boom'); sys.exit(1)"),
        )
        set_config_hooks(monkeypatch, [cfg])
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"name": "x"})
        run_command_hooks_for(ctx)
        assert not ctx.aborted

    def test_nonzero_exit_block_policy_aborts(self, home, workspace, monkeypatch):
        cfg = HookConfig(
            event="before_tool_call",
            command=make_hook_script(workspace, "import sys; sys.exit(1)"),
            on_failure="block",
        )
        set_config_hooks(monkeypatch, [cfg])
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"name": "x"})
        run_command_hooks_for(ctx)
        assert ctx.aborted and "failed" in ctx.abort_reason

    def test_timeout_kills_and_continues(self, home, workspace, monkeypatch):
        cfg = HookConfig(
            event="before_tool_call",
            command=make_hook_script(workspace, "import time; time.sleep(30)"),
            timeout=0.4,
        )
        set_config_hooks(monkeypatch, [cfg])
        ctx = HookContext(HookType.BEFORE_TOOL_CALL, {"name": "x"})
        started = time.perf_counter()
        run_command_hooks_for(ctx)
        elapsed = time.perf_counter() - started
        assert elapsed < 10, f"hook was not killed promptly ({elapsed:.1f}s)"
        assert not ctx.aborted

    def test_secrets_scrubbed_from_hook_env(self, workspace, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
        monkeypatch.setenv("MY_CUSTOM_TOKEN", "t0k3n")
        result = execute_command_hook(
            HookConfig(
                event="before_tool_call",
                command=make_hook_script(workspace, "import os; print(sorted(os.environ))"),
            ),
            hook_event="before_tool_call", payload={}, abort_supported=True,
        )
        assert result.exit_code == 0
        assert "OPENAI_API_KEY" not in result.stdout
        assert "MY_CUSTOM_TOKEN" not in result.stdout

    def test_plain_text_stdout_tolerated(self, workspace):
        result = execute_command_hook(
            HookConfig(event="before_tool_call", command=make_hook_script(workspace, "print('hello')")),
            hook_event="before_tool_call", payload={}, abort_supported=True,
        )
        assert result.exit_code == 0 and result.update == {}


class TestSourcesAndTrust:
    def _write_hooks_yaml(self, path: Path, entries: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        import yaml

        path.write_text(yaml.safe_dump({"hooks": entries}), encoding="utf-8")

    def test_config_layer_trusted(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [HookConfig(event="before_tool_call", command="echo 1")])
        loaded, errors = hook_sources.discover_hooks(include_untrusted=True)
        assert not errors
        assert len(loaded) == 1 and loaded[0].source == "config" and loaded[0].trusted

    def test_user_layer_trusted(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [])
        self._write_hooks_yaml(
            home / "hooks.yaml", [{"event": "after_tool_call", "command": "echo 2"}]
        )
        loaded, _ = hook_sources.discover_hooks(include_untrusted=True)
        assert len(loaded) == 1 and loaded[0].source == "user" and loaded[0].trusted

    def test_project_layer_requires_trust(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [])
        self._write_hooks_yaml(
            workspace / ".agentnexus" / "hooks.yaml",
            [{"event": "before_tool_call", "command": "echo pwned"}],
        )
        # hot path: untrusted project hook filtered out
        assert hook_sources.discover_hook_configs() == []
        # inspection path: visible as untrusted
        loaded, _ = hook_sources.discover_hooks(include_untrusted=True)
        assert len(loaded) == 1 and loaded[0].source == "project" and not loaded[0].trusted

    def test_approve_then_trusted(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [])
        self._write_hooks_yaml(
            workspace / ".agentnexus" / "hooks.yaml",
            [{"event": "before_tool_call", "command": "echo ok"}],
        )
        loaded, _ = hook_sources.discover_hooks(include_untrusted=True)
        fingerprint = loaded[0].fingerprint
        hook_sources.approve_fingerprint(fingerprint)
        assert hook_sources.discover_hook_configs()[0].fingerprint == fingerprint
        # and it persists across re-discovery
        hook_sources._warned_untrusted.clear()
        loaded2, _ = hook_sources.discover_hooks(include_untrusted=True)
        assert loaded2[0].trusted
        hook_sources.revoke_fingerprint(fingerprint)
        assert hook_sources.discover_hook_configs() == []

    def test_bypass_mode_trusts_everything(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [])
        monkeypatch.setattr(get_settings(), "hook_trust", "bypass", raising=False)
        self._write_hooks_yaml(
            workspace / ".agentnexus" / "hooks.yaml",
            [{"event": "before_tool_call", "command": "echo x"}],
        )
        loaded, _ = hook_sources.discover_hooks(include_untrusted=True)
        assert loaded[0].trusted

    def test_invalid_yaml_entries_collected_as_errors(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [])
        self._write_hooks_yaml(
            home / "hooks.yaml",
            [{"event": "nope", "command": "x"}, {"command": "missing event"}],
        )
        loaded, errors = hook_sources.discover_hooks(include_untrusted=True)
        assert loaded == [] and len(errors) == 2

    def test_hooks_enabled_false_disables_all(self, home, workspace, monkeypatch):
        set_config_hooks(monkeypatch, [HookConfig(event="before_tool_call", command="echo 1")])
        monkeypatch.setattr(get_settings(), "hooks_enabled", False, raising=False)
        assert hook_sources.discover_hooks() == ([], [])


class TestManagerIntegration:
    def test_command_hooks_run_after_in_process_chain(self, home, workspace, monkeypatch, tmp_path):
        marker = tmp_path / "ran.txt"
        set_config_hooks(monkeypatch, [
            HookConfig(
                event="before_tool_call",
                command=make_hook_script(workspace, f"open(r'{marker}', 'w').write('yes')"),
            ),
        ])
        mgr = HookManager()
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "shell_exec"})
        assert not ctx.aborted
        assert marker.read_text() == "yes"

    def test_in_process_abort_skips_command_hooks(self, home, workspace, monkeypatch):
        marker = workspace / "should_not_exist.txt"
        set_config_hooks(monkeypatch, [
            HookConfig(
                event="before_tool_call",
                command=make_hook_script(workspace, f"open(r'{marker}', 'w').write('yes')"),
            ),
        ])
        mgr = HookManager()

        def blocker(ctx):
            ctx.abort("already blocked")

        mgr.register(HookType.BEFORE_TOOL_CALL, blocker, name="blocker", priority=1)
        ctx = mgr.fire(HookType.BEFORE_TOOL_CALL, {"name": "x"})
        assert ctx.aborted
        assert not marker.exists()

    def test_observer_events_run_command_hooks_concurrently(self, home, workspace, monkeypatch, tmp_path):
        marker = tmp_path / "obs.txt"
        set_config_hooks(monkeypatch, [
            HookConfig(
                event="after_tool_call",
                command=make_hook_script(workspace, f"open(r'{marker}', 'a').write('x')"),
            ),
        ])
        mgr = HookManager()
        ctx = mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "result": "ok"})
        assert not ctx.aborted  # observer hooks never block
        assert marker.exists()
