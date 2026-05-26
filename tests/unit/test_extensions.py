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
        assert [item.name for item in report.disabled] == ["demo"]
        assert report.failed == []
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_extension_manager_loads_explicitly_enabled_plugin(temp_agentnexus_home):
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
        (temp_agentnexus_home / "config.yaml").write_text(
            "capabilities:\n  plugins:\n    demo: true\n",
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

        assert [item.name for item in report.loaded] == ["demo"]
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


def test_extension_manager_rejects_unknown_provider(temp_agentnexus_home):
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
        (temp_agentnexus_home / "config.yaml").write_text(
            "capabilities:\n  plugins:\n    bad_provider: true\n",
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


def test_default_builtin_extensions_dir_exists():
    from agentnexus.extensions import ExtensionManager

    settings = SimpleNamespace(
        memory_db_path=str(Path.cwd() / "build" / "test-workspace" / "memory.db"),
        extensions_dirs=[],
        extensions_enabled=True,
    )
    manager = ExtensionManager(settings)

    assert manager.built_in_dir.exists()
    assert manager.built_in_dir.name == "builtin_extensions"


def test_extension_manager_loads_python_provider(temp_agentnexus_home):
    tmp_path = _workspace_tmp()
    plugin_dir = tmp_path / "plugins" / "demo_dynamic"
    plugin_dir.mkdir(parents=True)
    try:
        (plugin_dir / "plugin.yaml").write_text(
            """
name: demo_dynamic
version: "0.1"
api_version: "1"
providers:
  - demo-dynamic
packaging:
  provider_entrypoints:
    - provider.py:DemoProvider
compatibility: {}
""".strip(),
            encoding="utf-8",
        )
        (plugin_dir / "provider.py").write_text(
            """
from agentnexus.tools.providers import ProviderSpec


class DemoProvider:
    def metadata(self):
        return ProviderSpec("demo-dynamic", description="Dynamic demo provider")

    def register(self, executor, context):
        before = set(executor.registry.list_tools())
        executor.registerTool(
            "demo_dynamic_echo",
            "echo",
            lambda text="ok": text,
            param_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            risk_level="low",
        )
        context.mark_registered(executor, before)
""".strip(),
            encoding="utf-8",
        )
        (temp_agentnexus_home / "config.yaml").write_text(
            "capabilities:\n  plugins:\n    demo_dynamic: true\n",
            encoding="utf-8",
        )

        from agentnexus.extensions import ExtensionManager
        from agentnexus.tools.providers import ToolProviderContext, register_tool_providers
        from agentnexus.tools.tool_executor import ToolExecutor

        settings = SimpleNamespace(
            memory_db_path=str(tmp_path / "memory.db"),
            extensions_dirs=[],
            extensions_enabled=True,
        )
        manager = ExtensionManager(settings, built_in_dir=tmp_path / "builtin", user_dir=tmp_path / "plugins")
        manager.discover()
        report = manager.load_enabled()

        assert [item.name for item in report.loaded] == ["demo_dynamic"]
        providers = manager.loaded_providers()
        assert [provider.metadata().name for provider in providers] == ["demo-dynamic"]

        executor = ToolExecutor()
        registered = register_tool_providers(executor, providers, ToolProviderContext())
        assert registered == ["demo_dynamic_echo"]
        assert executor.registry.invoke("demo_dynamic_echo", {"text": "hello"}) == "hello"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
