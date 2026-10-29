"""Configuration commands backed by the shared desktop configuration service."""

import json

from agentnexus.services import configuration as config

from . import COMMAND_HELP, argument_path, split_action


def run(context, command, args, sid):
    """Read and update model and application settings."""
    runtime = context.runtime
    action, rest = split_action(args, "show" if command in {"/settings", "/persona"} else "list")
    if command == "/settings":
        if action == "show":
            return config.get_config()
        if action == "set":
            key, value = split_action(rest, "")
            if not value:
                raise ValueError("请输入设置项和值")
            return config.update_config(config.ConfigUpdateRequest(key=key, value=value), runtime)
    elif command == "/providers":
        if action == "list":
            return config.list_llm_providers()
        if action == "save":
            data = json.loads(argument_path(rest).read_text(encoding="utf-8"))
            return config.update_llm_providers(config.ProvidersUpdateRequest.model_validate(data), runtime)
    elif command == "/model":
        if action == "list":
            return config.list_llm_providers()
        if action == "select":
            return config.set_active_llm_provider(config.ActiveProviderRequest(name=rest), runtime)
        if action == "discover":
            from agentnexus.core.config import get_settings

            provider = next((p for p in get_settings().llm_providers if p.name == rest), None)
            if provider is None:
                raise ValueError(f"提供商不存在: {rest}")
            return config.discover_models(config.DiscoverRequest(base_url=provider.base_url, provider=provider.name))
    elif command == "/persona":
        if action == "show":
            return config.get_config()["persona"]
        if action == "save":
            data = json.loads(argument_path(rest).read_text(encoding="utf-8"))
            return config.update_persona(config.PersonaUpdateRequest.model_validate(data))
    elif command == "/plugin" and action in {"list", "status"}:
        return config.get_extensions()
    raise ValueError(f"用法: {command} {COMMAND_HELP[command]}")
