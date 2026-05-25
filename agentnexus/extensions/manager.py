"""Extension manager for declarative AgentNexus plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from agentnexus.tools.providers import default_tool_providers

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
        enabled_globally = bool(getattr(self.settings, "extensions_enabled", True))
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
            errors = self._validate_manifest(descriptor.manifest, known_providers)
            if errors:
                failed.append(
                    ExtensionDescriptor(
                        descriptor.name,
                        descriptor.path,
                        manifest=descriptor.manifest,
                        enabled=False,
                        errors=errors,
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
                    )
                )

        self._load_report = ExtensionLoadReport(loaded=loaded, disabled=disabled, failed=failed)
        return self._load_report

    def status(self) -> ExtensionStatusReport:
        return ExtensionStatusReport(discovered=self._discovered, load_report=self._load_report)

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
