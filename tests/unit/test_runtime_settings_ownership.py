"""Runtime settings changes stay visible to their owner and isolated from peers."""

import pytest
from fastapi.testclient import TestClient

from agentnexus.app.runtime import AppRuntime
from agentnexus.core.config import Settings, get_settings, load_config_yaml, write_config_yaml
from agentnexus.core.modules import ModuleFactories, ModuleHost
from agentnexus.server.app import create_app


def test_http_settings_write_refreshes_only_its_runtime():
    initial = get_settings()
    first = AppRuntime.build(settings=initial.model_copy(update={"llm_model_id": "first-model"}),
                             module_factories=ModuleFactories.disabled())
    second = AppRuntime.build(settings=initial.model_copy(update={"llm_model_id": "second-model"}),
                              module_factories=ModuleFactories.disabled())
    with TestClient(create_app(first)) as first_client, TestClient(create_app(second)) as second_client:
        response = first_client.put("/api/config", json={"key": "llm_model_id", "value": "updated-first"})
        assert response.status_code == 200, response.text
        assert first_client.get("/api/config").json()["llm_model_id"] == "updated-first"
        assert first.settings.llm_model_id == "updated-first"
        assert second_client.get("/api/config").json()["llm_model_id"] == "second-model"
        assert second.settings.llm_model_id == "second-model"


def test_explicit_same_value_http_write_refreshes_each_owner():
    initial = get_settings()
    first = AppRuntime.build(settings=initial.model_copy(update={"llm_model_id": "first-model"}),
                             module_factories=ModuleFactories.disabled())
    second = AppRuntime.build(settings=initial.model_copy(update={"llm_model_id": "second-model"}),
                              module_factories=ModuleFactories.disabled())
    with TestClient(create_app(first)) as first_client, TestClient(create_app(second)) as second_client:
        for client in (first_client, second_client):
            response = client.put("/api/config", json={"key": "llm_model_id", "value": "same-model"})
            assert response.status_code == 200, response.text
            assert client.get("/api/config").json()["llm_model_id"] == "same-model"


def test_settings_write_preserves_startup_module_selection():
    host = ModuleHost(factories=ModuleFactories.disabled())
    selection = host.config
    with host.activate():
        write_config_yaml({"max_memories": 321, "modules": {"agent": {"enabled": False}}})
        assert get_settings().max_memories == 321
        assert get_settings().modules == selection
    assert host.config is selection
    assert host.is_enabled("agent") is False


def test_settings_write_preserves_owner_persona_and_capabilities():
    settings = Settings(
        capabilities={"tools": {"file_read": False}},
        persona={"agent_name": "Owned persona"},
    )
    host = ModuleHost(settings, factories=ModuleFactories.disabled())
    with host.activate():
        write_config_yaml({"max_memories": 321})
        assert get_settings().capabilities.tools == {"file_read": False}
        assert get_settings().persona.agent_name == "Owned persona"


def test_yaml_delta_preserves_unrelated_owner_values():
    write_config_yaml({"llm_model_id": "persisted-model", "max_memories": 100})
    host = ModuleHost(Settings(llm_model_id="owner-model"), factories=ModuleFactories.disabled())
    with host.activate():
        data = load_config_yaml()
        data["max_memories"] = 321
        write_config_yaml(data)
        assert get_settings().max_memories == 321
        assert get_settings().llm_model_id == "owner-model"


def test_capability_reload_uses_owning_settings_outside_activation():
    first = AppRuntime.build(module_factories=ModuleFactories.disabled())
    second = AppRuntime.build(module_factories=ModuleFactories.disabled())
    try:
        assert first.executor.get_meta("file_read").enabled is True
        assert second.executor.get_meta("file_read").enabled is True
        result = first.capability_runtime.disable("tools", "file_read")
        assert result == {"tools": "reloaded"}
        assert first.executor.get_meta("file_read").enabled is False
        with pytest.raises(PermissionError, match="Tool 'file_read' is disabled"):
            first.executor.invoke("file_read", {"path": "missing.txt"})
        assert second.executor.get_meta("file_read").enabled is True
        assert first.modules.settings.capabilities.tools["file_read"] is False
        assert "file_read" not in second.modules.settings.capabilities.tools
        assert first.capability_runtime.enable("tools", "file_read") == {"tools": "reloaded"}
        assert first.executor.get_meta("file_read").enabled is True
    finally:
        first.close()
        second.close()
