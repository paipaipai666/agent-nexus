"""HTTP adapter for shared configuration operations."""

from fastapi import APIRouter, Depends

from agentnexus.server.deps import get_runtime
from agentnexus.server.error_handlers import APIError
from agentnexus.services import configuration
from agentnexus.services.configuration import (
    _SECURITY_BLOCKED_KEYS as _SECURITY_BLOCKED_KEYS,
)
from agentnexus.services.configuration import (
    SETTABLE_KEYS as SETTABLE_KEYS,
)
from agentnexus.services.configuration import (
    ActiveProviderRequest as ActiveProviderRequest,
)
from agentnexus.services.configuration import (
    ConfigUpdateRequest as ConfigUpdateRequest,
)
from agentnexus.services.configuration import (
    DiscoverRequest as DiscoverRequest,
)
from agentnexus.services.configuration import (
    ModelEntryInput as ModelEntryInput,
)
from agentnexus.services.configuration import (
    ModelOverrideInput as ModelOverrideInput,
)
from agentnexus.services.configuration import (
    PersonaProjectUpdate as PersonaProjectUpdate,
)
from agentnexus.services.configuration import (
    PersonaUpdateRequest as PersonaUpdateRequest,
)
from agentnexus.services.configuration import (
    ProviderInput as ProviderInput,
)
from agentnexus.services.configuration import (
    ProvidersUpdateRequest as ProvidersUpdateRequest,
)
from agentnexus.services.configuration import (
    _apply_active_llm as _apply_active_llm,
)
from agentnexus.services.configuration import (
    _mask_provider as _mask_provider,
)
from agentnexus.services.configuration import (
    _reset_settings_cache as _reset_settings_cache,
)
from agentnexus.services.configuration import (
    _validate_providers as _validate_providers,
)

router = APIRouter(tags=["config"])


def _call(operation, *args):
    try:
        return operation(*args)
    except configuration.ConfigurationError as exc:
        raise APIError(exc.status_code, exc.code, str(exc)) from exc

@router.get('')
def get_config():
    return _call(configuration.get_config)


@router.get('/extensions')
def get_extensions():
    return _call(configuration.get_extensions)


@router.get('/llm/providers')
def list_llm_providers():
    return _call(configuration.list_llm_providers)


@router.put('/llm/providers')
def update_llm_providers(req: ProvidersUpdateRequest):
    from agentnexus.server.app import _get_runtime
    return _call(configuration.update_llm_providers, req, _get_runtime())


@router.post('/llm/active')
def set_active_llm_provider(req: ActiveProviderRequest):
    from agentnexus.server.app import _get_runtime
    return _call(configuration.set_active_llm_provider, req, _get_runtime())


@router.post('/llm/discover')
def discover_models(req: DiscoverRequest):
    return _call(configuration.discover_models, req)


@router.get('/llm/capabilities')
def get_llm_capabilities(runtime=Depends(get_runtime)):
    return _call(configuration.get_llm_capabilities, runtime)


@router.post('/llm/capabilities/refresh')
def refresh_llm_capabilities(runtime=Depends(get_runtime)):
    return _call(configuration.refresh_llm_capabilities, runtime)


@router.put('')
def update_config(req: ConfigUpdateRequest):
    from agentnexus.server.app import _get_runtime
    return _call(configuration.update_config, req, _get_runtime())


@router.put('/persona')
def update_persona(req: PersonaUpdateRequest):
    return _call(configuration.update_persona, req)
