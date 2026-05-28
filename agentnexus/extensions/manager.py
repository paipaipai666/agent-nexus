"""Extension manager for declarative AgentNexus plugins."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from agentnexus.tools.providers import ToolProvider, default_tool_providers

PLUGIN_API_VERSION = "1"


class PluginCompatibility(BaseModel):
    agentnexus_min_version: str | None = None
    agentnexus_max_major: int | None = None
    feature_flags: list[str] = Field(default_factory=list)


class PluginRequires(BaseModel):
    providers: list[str] = Field(default_factory=list)
    model_capabilities: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)


class PluginManifest(BaseModel):
    name: str
    version: str
    api_version: str
    providers: list[str] = Field(default_factory=list)
    compatibility: PluginCompatibility = Field(default_factory=PluginCompatibility)
    display_name: str | None = None
    description: str | None = None
    workflows: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    requires: PluginRequires = Field(default_factory=PluginRequires)
    packaging: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("plugin name is required")
        return normalized


@dataclass(frozen=True)
class ExtensionDescriptor:
    name: str
    path: Path
    manifest: PluginManifest | None = None
    enabled: bool = False
    errors: list[str] = field(default_factory=list)
    providers: tuple[ToolProvider, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExtensionLoadReport:
    loaded: list[ExtensionDescriptor] = field(default_factory=list)
    disabled: list[ExtensionDescriptor] = field(default_factory=list)
    failed: list[ExtensionDescriptor] = field(default_factory=list)


@dataclass(frozen=True)
class ExtensionStatusReport:
    discovered: list[ExtensionDescriptor] = field(default_factory=list)
    load_report: ExtensionLoadReport = field(default_factory=ExtensionLoadReport)


class ExtensionManager:
    """Discover, validate, and report declarative plugin state."""

    def __init__(self, settings: Any, built_in_dir: Path | None = None, user_dir: Path | None = None):
        self.settings = settings
        package_root = Path(__file__).resolve().parents[1]
        self.built_in_dir = built_in_dir or package_root / "builtin_extensions"
        configured_dirs = getattr(settings, "extensions_dirs", None) or []
        self.extra_dirs = [Path(p).expanduser() for p in configured_dirs]
        if user_dir is None:
            home = Path(getattr(settings, "memory_db_path", "")).expanduser().parent
            user_dir = home / "plugins"
        self.user_dir = user_dir
        self._discovered: list[ExtensionDescriptor] = []
        self._load_report = ExtensionLoadReport()

    def discover(self) -> list[ExtensionDescriptor]:
        descriptors: list[ExtensionDescriptor] = []
        for base_dir in self._extension_dirs():
            if not base_dir.exists():
                continue
            for manifest_path in sorted(base_dir.glob("*/plugin.yaml")):
                descriptors.append(self._read_manifest(manifest_path))
        self._discovered = descriptors
        return descriptors

    def load_enabled(self, runtime: Any = None) -> ExtensionLoadReport:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_PLUGIN_LOAD, {})

        enabled_globally = bool(getattr(self.settings, "extensions_enabled", True))
        plugin_enabled = self._plugin_enabled_map()
        known_providers = {provider.metadata().name for provider in default_tool_providers()}
        loaded: list[ExtensionDescriptor] = []
        disabled: list[ExtensionDescriptor] = []
        failed: list[ExtensionDescriptor] = []

        for descriptor in self._discovered:
            if descriptor.manifest is None or descriptor.errors:
                failed.append(descriptor)
                continue
            if not enabled_globally:
                disabled.append(descriptor)
                continue
            if not self._is_plugin_enabled(descriptor, plugin_enabled):
                disabled.append(descriptor)
                continue
            plugin_providers, provider_errors = self._load_plugin_providers(descriptor)
            provider_names = known_providers | {provider.metadata().name for provider in plugin_providers}
            errors = [*provider_errors, *self._validate_manifest(descriptor.manifest, provider_names)]
            if errors:
                failed.append(
                    ExtensionDescriptor(
                        descriptor.name,
                        descriptor.path,
                        manifest=descriptor.manifest,
                        enabled=False,
                        errors=errors,
                        providers=tuple(plugin_providers),
                    )
                )
            else:
                loaded.append(
                    ExtensionDescriptor(
                        descriptor.name,
                        descriptor.path,
                        manifest=descriptor.manifest,
                        enabled=True,
                        errors=[],
                        providers=tuple(plugin_providers),
                    )
                )

        self._load_report = ExtensionLoadReport(loaded=loaded, disabled=disabled, failed=failed)

        hook_mgr.fire(HookType.AFTER_PLUGIN_LOAD, {
            "loaded_count": len(loaded), "disabled_count": len(disabled),
            "failed_count": len(failed),
            "loaded_names": [d.name for d in loaded],
        })
        return self._load_report

    def status(self) -> ExtensionStatusReport:
        return ExtensionStatusReport(discovered=self._discovered, load_report=self._load_report)

    def loaded_providers(self) -> list[ToolProvider]:
        """Return dynamically loaded providers from enabled plugins."""
        providers: list[ToolProvider] = []
        for descriptor in self._load_report.loaded:
            providers.extend(descriptor.providers)
        return providers

    def _plugin_enabled_map(self) -> dict[str, bool]:
        try:
            from agentnexus.core.config import load_config_yaml

            data = load_config_yaml()
            capabilities = data.get("capabilities") if isinstance(data, dict) else {}
            plugins = capabilities.get("plugins") if isinstance(capabilities, dict) else {}
            return dict(plugins) if isinstance(plugins, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _is_plugin_enabled(descriptor: ExtensionDescriptor, enabled_map: dict[str, bool]) -> bool:
        if descriptor.name in enabled_map:
            return enabled_map[descriptor.name] is True
        manifest = descriptor.manifest
        if manifest is None:
            return False
        return not manifest.providers

    def _extension_dirs(self) -> list[Path]:
        return [self.built_in_dir, *self.extra_dirs, self.user_dir]

    def _read_manifest(self, manifest_path: Path) -> ExtensionDescriptor:
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            manifest = PluginManifest.model_validate(data)
            return ExtensionDescriptor(manifest.name, manifest_path.parent, manifest=manifest)
        except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
            return ExtensionDescriptor(manifest_path.parent.name, manifest_path.parent, errors=[str(exc)])

    def _validate_manifest(self, manifest: PluginManifest, known_providers: set[str]) -> list[str]:
        errors: list[str] = []
        if str(manifest.api_version) != PLUGIN_API_VERSION:
            errors.append(f"unsupported plugin api_version: {manifest.api_version}")
        unknown = sorted(set(manifest.providers) - known_providers)
        if unknown:
            errors.append(f"unknown providers: {', '.join(unknown)}")
        missing_required = sorted(set(manifest.requires.providers) - known_providers)
        if missing_required:
            errors.append(f"missing required providers: {', '.join(missing_required)}")
        return errors

    def _load_plugin_providers(self, descriptor: ExtensionDescriptor) -> tuple[list[ToolProvider], list[str]]:
        manifest = descriptor.manifest
        if manifest is None:
            return [], []
        entrypoints = manifest.packaging.get("provider_entrypoints", [])
        if isinstance(entrypoints, str):
            entrypoints = [entrypoints]
        if not entrypoints:
            return [], []
        if not isinstance(entrypoints, list):
            return [], ["packaging.provider_entrypoints must be a string or list of strings"]

        providers: list[ToolProvider] = []
        errors: list[str] = []
        for entrypoint in entrypoints:
            if not isinstance(entrypoint, str) or not entrypoint.strip():
                errors.append("provider entrypoint must be a non-empty string")
                continue
            try:
                provider = self._load_provider_entrypoint(descriptor.path, entrypoint.strip())
                metadata = provider.metadata()
                if not metadata.name:
                    errors.append(f"provider entrypoint {entrypoint} returned an unnamed provider")
                    continue
                providers.append(provider)
            except Exception as exc:
                errors.append(f"failed to load provider {entrypoint}: {exc}")
        return providers, errors

    def _load_provider_entrypoint(self, plugin_dir: Path, entrypoint: str) -> ToolProvider:
        module_name, sep, attr_path = entrypoint.partition(":")
        if not sep or not module_name or not attr_path:
            raise ValueError("entrypoint must use 'module:attribute' format")

        module = self._load_provider_module(plugin_dir, module_name)
        target: Any = module
        for part in attr_path.split("."):
            target = getattr(target, part)
        provider = target() if isinstance(target, type) else target
        if callable(provider) and not (hasattr(provider, "metadata") and hasattr(provider, "register")):
            provider = provider()
        if not (hasattr(provider, "metadata") and hasattr(provider, "register")):
            raise TypeError("entrypoint must resolve to a ToolProvider object")
        return provider

    def _load_provider_module(self, plugin_dir: Path, module_name: str):
        module_path = Path(module_name)
        candidate = module_path if module_path.is_absolute() else plugin_dir / module_path
        if candidate.suffix == ".py" or candidate.exists():
            if candidate.suffix != ".py":
                candidate = candidate.with_suffix(".py")
            if not candidate.exists():
                raise FileNotFoundError(candidate)
            safe_name = f"agentnexus_plugin_{plugin_dir.name}_{candidate.stem}"
            spec = importlib.util.spec_from_file_location(safe_name, candidate)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot create import spec for {candidate}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[safe_name] = module
            spec.loader.exec_module(module)
            return module

        sys.path.insert(0, str(plugin_dir))
        try:
            return importlib.import_module(module_name)
        finally:
            try:
                sys.path.remove(str(plugin_dir))
            except ValueError:
                pass
