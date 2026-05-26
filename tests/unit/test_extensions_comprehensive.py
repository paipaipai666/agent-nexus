"""Comprehensive tests for ExtensionManager beyond basic discover/load.

Covers:
- Discovery from multiple directories (built_in, user, extra)
- Validation edge cases (missing fields, bad YAML, wrong api_version)
- Provider loading errors (bad entrypoint format, import errors)
- Status reporting
- Loaded_providers with multiple plugins
- Empty/no-plugins scenarios
"""

import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


def _workspace_tmp() -> Path:
    root = Path.cwd() / "build" / "test-workspace" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_settings(tmp_path: Path, **overrides) -> SimpleNamespace:
    base = dict(
        memory_db_path=str(tmp_path / "memory.db"),
        extensions_dirs=[],
        extensions_enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_manager(tmp_path: Path, settings=None, **kwargs):
    from agentnexus.extensions import ExtensionManager
    built_in = tmp_path / "builtin"
    user = tmp_path / "plugins"
    return ExtensionManager(
        settings or _make_settings(tmp_path),
        built_in_dir=built_in,
        user_dir=user,
        **kwargs,
    )


def _write_plugin(plugin_dir: Path, name: str, yaml_content: str):
    d = plugin_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.yaml").write_text(yaml_content.strip(), encoding="utf-8")
    return d


class TestDiscoveryMultipleDirs:

    def test_discovery_from_multiple_dirs(self):
        tmp_path = _workspace_tmp()
        try:
            p1 = _write_plugin(tmp_path / "builtin", "alpha", """
name: alpha
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            p2 = _write_plugin(tmp_path / "plugins", "beta", """
name: beta
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            p3 = _write_plugin(tmp_path / "extra", "gamma", """
name: gamma
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            from agentnexus.extensions import ExtensionManager
            manager = ExtensionManager(
                _make_settings(tmp_path),
                built_in_dir=tmp_path / "builtin",
                user_dir=tmp_path / "plugins",
            )
            manager.extra_dirs = [tmp_path / "extra"]
            discovered = manager.discover()
            names = {d.name for d in discovered}
            assert names == {"alpha", "beta", "gamma"}
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_discovery_skips_missing_dirs_silently(self):
        tmp_path = _workspace_tmp()
        try:
            manager = _make_manager(tmp_path)
            manager.extra_dirs = [tmp_path / "nonexistent"]
            discovered = manager.discover()
            assert discovered == []
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


class TestValidationEdgeCases:

    def test_missing_required_fields_fails(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "bad", "name: only_name\n")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_bad_yaml_syntax(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "broken", "name: {unclosed")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
            assert report.failed[0].errors
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_wrong_api_version(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "old", """
name: old
version: "0.1"
api_version: "0"
providers: []
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
            assert "api_version" in report.failed[0].errors[0]
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_empty_plugin_yaml(self):
        tmp_path = _workspace_tmp()
        try:
            (tmp_path / "plugins" / "empty").mkdir(parents=True)
            (tmp_path / "plugins" / "empty" / "plugin.yaml").write_text("", encoding="utf-8")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_globally_disabled_extensions(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "valid", """
name: valid
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            settings = _make_settings(tmp_path, extensions_enabled=False)
            manager = _make_manager(tmp_path, settings=settings)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.loaded) == 0
            assert len(report.disabled) == 1
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


class TestProviderLoadingErrors:

    def test_bad_entrypoint_format(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "bad_ep", """
name: bad_ep
version: "0.1"
api_version: "1"
providers: []
packaging:
  provider_entrypoints: just_a_string_no_colon
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_nonexistent_provider_module(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "missing_mod", """
name: missing_mod
version: "0.1"
api_version: "1"
providers: []
packaging:
  provider_entrypoints:
    - nonexistent_module.py:MyProvider
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.failed) == 1
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)


class TestStatusAndProviders:

    def test_status_report_after_discover(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "alpha", """
name: alpha
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            status = manager.status()
            assert len(status.discovered) == 1
            assert status.load_report == type(status.load_report)()
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_status_report_after_load(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "alpha", """
name: alpha
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            manager.load_enabled()
            status = manager.status()
            assert len(status.load_report.loaded) == 1
            assert status.load_report.loaded[0].name == "alpha"
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_loaded_providers_empty_with_no_python_providers(self):
        tmp_path = _workspace_tmp()
        try:
            _write_plugin(tmp_path / "plugins", "simple", """
name: simple
version: "0.1"
api_version: "1"
providers: []
compatibility: {}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            manager.load_enabled()
            assert manager.loaded_providers() == []
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

    def test_multiple_plugins_no_providers_loaded(self):
        tmp_path = _workspace_tmp()
        try:
            for name in ["a", "b", "c"]:
                _write_plugin(tmp_path / "plugins", name, f"""
name: {name}
version: "0.1"
api_version: "1"
providers: []
compatibility: {{}}
""")
            manager = _make_manager(tmp_path)
            manager.discover()
            report = manager.load_enabled()
            assert len(report.loaded) == 3
            assert [d.name for d in report.loaded] == ["a", "b", "c"]
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)
