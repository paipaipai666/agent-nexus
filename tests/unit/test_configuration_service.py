"""Configuration operations are shared by HTTP and terminal clients."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_provider_round_trip_preserves_masked_credentials(temp_agentnexus_home):
    from agentnexus.core.config import load_config_yaml
    from agentnexus.services.configuration import (
        ProviderInput,
        ProvidersUpdateRequest,
        list_llm_providers,
        update_llm_providers,
    )

    runtime = SimpleNamespace(llm=MagicMock())
    provider = {"name": "local", "base_url": "http://localhost:12345/v1", "api_key": "fixture-secret", "models": [{"model_id": "fixture-model"}]}
    update_llm_providers(ProvidersUpdateRequest(providers=[ProviderInput(**provider)]), runtime)
    public = list_llm_providers()["providers"][0]
    assert public["api_key"] == "****"
    update_llm_providers(ProvidersUpdateRequest(providers=[ProviderInput(**public)]), runtime)
    assert load_config_yaml()["llm_providers"][0]["api_key"] == "fixture-secret"


def test_model_selection_updates_the_shared_client(temp_agentnexus_home):
    from agentnexus.services.configuration import (
        ActiveProviderRequest,
        ProviderInput,
        ProvidersUpdateRequest,
        set_active_llm_provider,
        update_llm_providers,
    )

    llm = MagicMock()
    runtime = SimpleNamespace(llm=llm)
    update_llm_providers(ProvidersUpdateRequest(providers=[ProviderInput(name="local", base_url="http://localhost:12345/v1", models=[{"model_id": "fixture-model"}])]), runtime)
    result = set_active_llm_provider(ActiveProviderRequest(name="local/fixture-model"), runtime)
    assert result["model_id"] == "fixture-model"
    assert llm.configure.call_args.kwargs["model"] == "fixture-model"


def test_http_adapter_preserves_configuration_error_status():
    import pytest

    from agentnexus.server.error_handlers import APIError
    from agentnexus.server.routes.config import ActiveProviderRequest, set_active_llm_provider

    with patch("agentnexus.server.app._get_runtime", return_value=SimpleNamespace(llm=MagicMock())):
        with pytest.raises(APIError) as caught:
            set_active_llm_provider(ActiveProviderRequest(name="nonexistent-provider/model"))
    assert caught.value.status_code == 404
