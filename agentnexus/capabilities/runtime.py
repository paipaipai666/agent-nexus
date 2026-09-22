"""Runtime capability hot-plug state and reload orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CAPABILITY_KINDS = ("tools", "skills", "mcp")


@dataclass
class CapabilityState:
    enabled: bool = True
    generation: int = 0
    loaded_generation: int = -1
    last_error: str = ""


@dataclass
class CapabilitySnapshot:
    states: dict[str, CapabilityState] = field(default_factory=dict)
    skill_enabled: dict[str, bool] = field(default_factory=dict)
    mcp_enabled: dict[str, bool] = field(default_factory=dict)


class CapabilityRuntime:
    """Coordinate runtime reloads for tools, skills, and MCP."""

    def __init__(
        self,
        *,
        settings: Any,
        executor: Any,
        agent: Any = None,
        skill_service: Any = None,
        mcp_manager: Any = None,
        register_tools: Any = None,
        llm_client: Any = None,
        subagent_confirm: Any = None,
    ):
        self.settings = settings
        self.executor = executor
        self.agent = agent
        self.skill_service = skill_service
        self.mcp_manager = mcp_manager
        self.register_tools = register_tools
        self.llm_client = llm_client
        self.subagent_confirm = subagent_confirm
        self.states = self._load_states()

    def snapshot(self) -> CapabilitySnapshot:
        cfg = self._capability_config()
        return CapabilitySnapshot(
            states={key: CapabilityState(**vars(value)) for key, value in self.states.items()},
            skill_enabled=dict(cfg.get("skills", {})),
            mcp_enabled=dict(cfg.get("mcp_servers", {})),
        )

    def refresh_if_stale(self) -> dict[str, str]:
        results: dict[str, str] = {}
        for kind in CAPABILITY_KINDS:
            state = self.states[kind]
            if state.enabled and state.generation != state.loaded_generation:
                results[kind] = self._reload_kind(kind)
            elif not state.enabled and state.loaded_generation != -1:
                results[kind] = self._unload_kind(kind)
        return results

    def reload(self, kind: str | None = None) -> dict[str, str]:
        kinds = CAPABILITY_KINDS if kind is None else (self._normalize_kind(kind),)
        results = {}
        for item in kinds:
            self.states[item].generation += 1
            results[item] = self._reload_kind(item)
        self._persist_states()
        return results

    def enable(self, kind: str, name: str | None = None) -> dict[str, str]:
        kind = self._normalize_kind(kind)
        if name:
            self._set_named_enabled(kind, name, True)
        state = self.states[kind]
        state.enabled = True
        state.generation += 1
        self._persist_states()
        return {kind: self._reload_kind(kind)}

    def disable(self, kind: str, name: str | None = None) -> dict[str, str]:
        kind = self._normalize_kind(kind)
        if name:
            self._set_named_enabled(kind, name, False)
            self.states[kind].generation += 1
            self._persist_states()
            return {kind: self._reload_kind(kind)}
        state = self.states[kind]
        state.enabled = False
        state.generation += 1
        self._persist_states()
        return {kind: self._unload_kind(kind)}

    def _reload_kind(self, kind: str) -> str:
        try:
            if kind == "tools":
                result = self.reload_tools()
            elif kind == "skills":
                result = self.reload_skills()
            elif kind == "mcp":
                result = self.reload_mcp()
            else:
                raise ValueError(f"Unknown capability kind: {kind}")
            state = self.states[kind]
            state.loaded_generation = state.generation
            state.last_error = ""
            self._persist_states()
            return result
        except Exception as exc:
            self.states[kind].last_error = str(exc)
            self._persist_states()
            return f"error: {exc}"

    def _unload_kind(self, kind: str) -> str:
        if kind == "tools":
            self.executor.unregister_source_type("builtin")
        elif kind == "skills" and self.skill_service is not None:
            self.skill_service.reset()
        elif kind == "mcp":
            self.executor.unregister_source_prefix("mcp:", source_type="mcp")
            if self.mcp_manager is not None:
                self.mcp_manager.close()
        self.states[kind].loaded_generation = -1
        self._persist_states()
        return "unloaded"

    def reload_tools(self) -> str:
        if self.register_tools is None:
            return "no register_tools hook"
        self.executor.registry.unregister_source_type("builtin")
        self.register_tools(
            self.executor,
            llm_client=self.llm_client,
            subagent_confirm=self.subagent_confirm,
            mcp_manager=self.mcp_manager,
            extra_providers=[],
        )
        return "reloaded"

    def reload_skills(self) -> str:
        if self.skill_service is None:
            return "no skill service"
        enabled_map = self._capability_config().get("skills", {})
        if hasattr(self.skill_service, "set_enabled_map"):
            self.skill_service.set_enabled_map(enabled_map)
        entries = self.skill_service.refresh()
        current = getattr(self.skill_service, "current", None)
        if current is not None and enabled_map.get(current.qualified_id, True) is False:
            self.skill_service.reset()
        return f"reloaded {len(entries)} skills"

    def reload_mcp(self) -> str:
        if self.mcp_manager is None:
            return "no mcp manager"
        enabled_map = self._capability_config().get("mcp_servers", {})
        for server in getattr(self.mcp_manager, "server_names", lambda: [])():
            if enabled_map.get(server, False):
                self.mcp_manager.enable_server(server)
            else:
                self.mcp_manager.disable_server(server)
        self.executor.registry.unregister_source_prefix("mcp:", source_type="mcp")
        self.mcp_manager.register_tools(self.executor)
        self._refresh_mcp_context()
        return "reloaded"

    def _refresh_mcp_context(self) -> None:
        if self.agent is not None and self.mcp_manager is not None and hasattr(self.agent, "set_mcp_context"):
            self.agent.set_mcp_context(self.mcp_manager.auto_context())

    def _set_named_enabled(self, kind: str, name: str, enabled: bool) -> None:
        cfg = self._capability_config()
        bucket = {
            "skills": "skills",
            "mcp": "mcp_servers",
            "tools": "tools",
        }[kind]
        cfg.setdefault(bucket, {})[name] = enabled
        self._write_capability_config(cfg)

    def _load_states(self) -> dict[str, CapabilityState]:
        cfg = self._capability_config()
        raw_states = cfg.get("states", {})
        states = {}
        for kind in CAPABILITY_KINDS:
            data = raw_states.get(kind, {})
            default_enabled = kind in {"tools", "skills"}
            states[kind] = CapabilityState(
                enabled=bool(data.get("enabled", default_enabled)),
                generation=int(data.get("generation", 0)),
                loaded_generation=int(data.get("loaded_generation", -1)),
                last_error=str(data.get("last_error", "")),
            )
        return states

    def _persist_states(self) -> None:
        cfg = self._capability_config()
        cfg["states"] = {kind: vars(state) for kind, state in self.states.items()}
        self._write_capability_config(cfg)

    @staticmethod
    def _normalize_kind(kind: str) -> str:
        normalized = (kind or "").strip().lower()
        aliases = {"skill": "skills", "tool": "tools", "mcp_server": "mcp"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in CAPABILITY_KINDS:
            raise ValueError(f"Unknown capability kind: {kind}")
        return normalized

    @staticmethod
    def _capability_config() -> dict:
        from agentnexus.core.config import get_settings

        caps = get_settings().capabilities
        return caps.model_dump()

    @staticmethod
    def _write_capability_config(cfg: dict) -> None:
        from agentnexus.core.config import load_config_yaml, write_config_yaml

        data = load_config_yaml()
        data["capabilities"] = cfg
        write_config_yaml(data)
