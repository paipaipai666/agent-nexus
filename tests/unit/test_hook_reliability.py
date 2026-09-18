"""Reliability tests for the hook system (M4).

Covers:
- per-hook timeout (sync callback exceeding its deadline is skipped, loop unblocked)
- hook journal ({traces_dir}/hooks.jsonl append + read_hook_journal tail)
- plugin code hooks (entrypoint gated by plugins_allow_code)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from agentnexus.core.config import get_settings
from agentnexus.core.hooks import (
    HookManager,
    HookType,
    read_hook_journal,
)


class TestPerHookTimeout:
    def test_slow_sync_hook_times_out_and_loop_continues(self):
        mgr = HookManager()
        finished = {"value": False}

        def slow(ctx):
            time.sleep(3)
            finished["value"] = True

        mgr.register(HookType.AFTER_TOOL_CALL, slow, name="slow", timeout=0.2)
        started = time.perf_counter()
        ctx = mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": ""})
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"fire blocked by slow hook ({elapsed:.1f}s)"
        assert not ctx.aborted

    def test_fast_hook_with_timeout_completes(self):
        mgr = HookManager()
        seen: list[str] = []

        def fast(ctx):
            seen.append(ctx.payload["name"])

        mgr.register(HookType.AFTER_TOOL_CALL, fast, name="fast", timeout=1.0)
        mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": ""})
        assert seen == ["x"]


class TestJournal:
    def test_fire_appends_journal_records(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "traces_dir", str(tmp_path), raising=False)
        monkeypatch.setattr(get_settings(), "hooks_journal", True, raising=False)
        mgr = HookManager()
        mgr.register(HookType.AFTER_TOOL_CALL,
                     lambda ctx: None, name="obs")
        mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": "ok"})

        journal = tmp_path / "hooks.jsonl"
        assert journal.is_file()
        lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        assert any(r["hook"] == "obs" and r["outcome"] == "ok" for r in lines)

        tail = read_hook_journal(10)
        assert any(r["hook"] == "obs" for r in tail)

    def test_journal_disabled_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "traces_dir", str(tmp_path), raising=False)
        monkeypatch.setattr(get_settings(), "hooks_journal", False, raising=False)
        mgr = HookManager()
        mgr.fire(HookType.AFTER_TOOL_CALL, {"name": "x", "params": {}, "result": ""})
        assert not (tmp_path / "hooks.jsonl").exists()


class TestPluginCodeHooks:
    def _make_plugin(self, base: Path, entrypoint: bool = True) -> Path:
        plugin_dir = base / "plugins" / "myplugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            "name: myplugin\nversion: 1.0.0\napi_version: '1'\n"
            + ("entrypoint: hooks.py\n" if entrypoint else ""),
            encoding="utf-8",
        )
        if entrypoint:
            (plugin_dir / "hooks.py").write_text(
                "from agentnexus.core.hooks import HookType\n\n"
                "def register(manager):\n"
                "    manager.register(HookType.AFTER_TOOL_CALL, lambda ctx: None, name='plugin_obs')\n",
                encoding="utf-8",
            )
        return plugin_dir

    def _manager(self, base: Path):
        from agentnexus.extensions.manager import ExtensionManager

        settings = SimpleNamespace(extensions_enabled=True, extensions_dirs=[], capabilities=SimpleNamespace(plugins={}))
        return ExtensionManager(settings, built_in_dir=base / "plugins", user_dir=base / "user_plugins")

    def test_entrypoint_blocked_without_opt_in(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "plugins_allow_code", False, raising=False)
        self._make_plugin(tmp_path)
        manager = self._manager(tmp_path)
        manager.discover()
        report = manager.load_enabled()
        assert len(report.failed) == 1
        assert any("plugins_allow_code" in e for e in report.failed[0].errors)

    def test_entrypoint_loaded_with_opt_in(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "plugins_allow_code", True, raising=False)
        self._make_plugin(tmp_path)
        manager = self._manager(tmp_path)
        manager.discover()
        report = manager.load_enabled()
        assert len(report.loaded) == 1 and not report.failed
        from agentnexus.core.hooks import get_hook_manager

        hook_mgr = get_hook_manager()
        hook_names = {h["name"] for h in hook_mgr.list_hooks()}
        assert "plugin_obs" in hook_names
        hook_mgr.unregister("plugin_obs")

    def test_declarative_plugin_without_entrypoint_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(get_settings(), "plugins_allow_code", False, raising=False)
        self._make_plugin(tmp_path, entrypoint=False)
        manager = self._manager(tmp_path)
        manager.discover()
        report = manager.load_enabled()
        assert len(report.loaded) == 1 and not report.failed
