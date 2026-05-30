from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml

from agentnexus.capabilities.runtime import CapabilityRuntime
from agentnexus.tools.registry import ToolRegistry


def test_refresh_if_stale_skips_matching_generation(temp_agentnexus_home):
    config = temp_agentnexus_home / "config.yaml"
    config.write_text(
        yaml.safe_dump({
            "capabilities": {
                "states": {
                    "tools": {"enabled": True, "generation": 1, "loaded_generation": 1},
                }
            }
        }),
        encoding="utf-8",
    )
    hook = MagicMock()
    runtime = CapabilityRuntime(
        settings=SimpleNamespace(),
        executor=ToolRegistry(),
        register_tools=hook,
    )

    result = runtime.refresh_if_stale()

    assert "tools" not in result
    hook.assert_not_called()


def test_disable_unloads_source_and_persists_generation(temp_agentnexus_home):
    executor = ToolRegistry()
    executor.register_tool("hello", "say hello", lambda: "ok", source_type="builtin", source_id="builtin")
    runtime = CapabilityRuntime(settings=SimpleNamespace(), executor=executor)

    result = runtime.disable("tools")

    assert result["tools"] == "unloaded"
    assert executor.get_tool("hello") is None
    data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8"))
    assert data["capabilities"]["states"]["tools"]["enabled"] is False


def test_enable_skill_updates_named_state(temp_agentnexus_home):
    skill_service = MagicMock()
    skill_service.refresh.return_value = []
    runtime = CapabilityRuntime(
        settings=SimpleNamespace(),
        executor=ToolRegistry(),
        skill_service=skill_service,
    )

    runtime.enable("skills", "default/docx")

    data = yaml.safe_load((temp_agentnexus_home / "config.yaml").read_text(encoding="utf-8"))
    assert data["capabilities"]["skills"]["default/docx"] is True
    skill_service.set_enabled_map.assert_called()
