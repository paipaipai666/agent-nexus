import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace


def _workspace_tmp() -> Path:
    root = Path.cwd() / "build" / "test-workspace" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_extension_manager_discovers_valid_plugin():
    tmp_path = _workspace_tmp()
    plugin_dir = tmp_path / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    try:
        (plugin_dir / "plugin.yaml").write_text(
            """
name: demo
version: "0.1"
api_version: "1"
providers:
  - filesystem
compatibility: {}
""".strip(),
            encoding="utf-8",
        )

        from agentnexus.extensions import ExtensionManager

        settings = SimpleNamespace(
            memory_db_path=str(tmp_path / "memory.db"),
            extensions_dirs=[],
            extensions_enabled=True,
        )
        manager = ExtensionManager(settings, built_in_dir=tmp_path / "builtin", user_dir=tmp_path / "plugins")
        discovered = manager.discover()
        report = manager.load_enabled()

        assert [item.name for item in discovered] == ["demo"]
        assert [item.name for item in report.loaded] == ["demo"]
        assert report.failed == []
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_extension_manager_reports_invalid_manifest():
    tmp_path = _workspace_tmp()
    plugin_dir = tmp_path / "plugins" / "bad"
    plugin_dir.mkdir(parents=True)
    try:
        (plugin_dir / "plugin.yaml").write_text("name: bad\n", encoding="utf-8")

        from agentnexus.extensions import ExtensionManager

        settings = SimpleNamespace(
            memory_db_path=str(tmp_path / "memory.db"),
            extensions_dirs=[],
            extensions_enabled=True,
        )
        manager = ExtensionManager(settings, built_in_dir=tmp_path / "builtin", user_dir=tmp_path / "plugins")
        manager.discover()
        report = manager.load_enabled()

        assert len(report.failed) == 1
        assert report.failed[0].errors
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_extension_manager_rejects_unknown_provider():
    tmp_path = _workspace_tmp()
    plugin_dir = tmp_path / "plugins" / "bad_provider"
    plugin_dir.mkdir(parents=True)
    try:
        (plugin_dir / "plugin.yaml").write_text(
            """
name: bad_provider
version: "0.1"
api_version: "1"
providers:
  - unknown-provider
compatibility: {}
""".strip(),
            encoding="utf-8",
        )

        from agentnexus.extensions import ExtensionManager

        settings = SimpleNamespace(
            memory_db_path=str(tmp_path / "memory.db"),
            extensions_dirs=[],
            extensions_enabled=True,
        )
        manager = ExtensionManager(settings, built_in_dir=tmp_path / "builtin", user_dir=tmp_path / "plugins")
        manager.discover()
        report = manager.load_enabled()

        assert len(report.failed) == 1
        assert "unknown providers" in report.failed[0].errors[0]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
