"""Runtime-owned selection and lifetime of six independent modules."""

from __future__ import annotations

import importlib
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ModuleName = Literal["agent", "short_term", "long_term", "wiki", "evaluation", "observability"]
MODULE_NAMES: tuple[ModuleName, ...] = ("agent", "short_term", "long_term", "wiki", "evaluation", "observability")


class ModuleSelection(BaseModel):
    """Select a module without importing its implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    factory: str = ""


class ModulesConfig(BaseModel):
    """Configuration for the application's fixed module boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    agent: ModuleSelection = Field(default_factory=ModuleSelection)
    short_term: ModuleSelection = Field(default_factory=ModuleSelection)
    long_term: ModuleSelection = Field(default_factory=ModuleSelection)
    wiki: ModuleSelection = Field(default_factory=lambda: ModuleSelection(enabled=False))
    evaluation: ModuleSelection = Field(default_factory=ModuleSelection)
    observability: ModuleSelection = Field(default_factory=ModuleSelection)


@dataclass(frozen=True)
class ModuleContext:
    """Dependencies supplied explicitly when constructing an implementation."""

    settings: object
    llm: object | None = None
    llm_factory: Callable[[], object] | None = None
    tools: object | None = None
    session_id: str | None = None
    workspace_path: str | None = None
    output: Callable[[str], None] | None = None
    confirm_fn: object | None = None
    max_steps: int | None = None
    token_budget: int | None = None
    conversation_mode: bool = True
    agent_id: str = "react_agent"


@dataclass(frozen=True)
class DefaultModule:
    """Use the selected built-in implementation."""


DEFAULT = DefaultModule()
ModuleFactory = Callable[[ModuleContext], object]
FactorySelection = ModuleFactory | DefaultModule | None


@dataclass(frozen=True)
class ModuleFactories:
    """Immutable factory overrides; None explicitly disables a module."""

    agent: FactorySelection = DEFAULT
    short_term: FactorySelection = DEFAULT
    long_term: FactorySelection = DEFAULT
    wiki: FactorySelection = DEFAULT
    evaluation: FactorySelection = DEFAULT
    observability: FactorySelection = DEFAULT

    @classmethod
    def disabled(cls) -> ModuleFactories:
        """Select an application with no optional implementations."""
        return cls(None, None, None, None, None, None)


class ModuleUnavailable(RuntimeError):
    """A requested module is disabled or its implementation is absent."""

    def __init__(self, module: ModuleName, reason: str):
        self.module = module
        self.reason = reason
        super().__init__(f"Module {module} is {reason}")


class ModuleLoadError(RuntimeError):
    """A selected implementation failed; no alternative was constructed."""

    def __init__(self, module: ModuleName, cause: Exception):
        self.module = module
        super().__init__(f"Module {module} failed to initialize: {cause}")


_IMPLEMENTATIONS: dict[ModuleName, tuple[str, ...]] = {
    "agent": ("agentnexus.agents.re_act_agent",),
    "short_term": ("agentnexus.memory.short_term",),
    "long_term": ("agentnexus.memory.long_term",),
    "wiki": ("agentnexus.wiki.wiki_service",),
    "evaluation": ("agentnexus.evaluation", "agentnexus.eval"),
    "observability": ("agentnexus.observability.backend",),
}
_active_host: ContextVar[ModuleHost | None] = ContextVar("agentnexus_module_host", default=None)
_default_host: ModuleHost | None = None
_default_lock = threading.RLock()


class ModuleHost:
    """Own lazy providers and close only resources created by this runtime."""

    def __init__(self, settings=None, *, factories: ModuleFactories | None = None):
        if settings is None:
            from agentnexus.core.config import get_settings

            settings = get_settings()
        self.settings = settings
        self.factories = factories or ModuleFactories()
        configured = getattr(settings, "modules", None)
        self.config = configured if isinstance(configured, ModulesConfig) else ModulesConfig()
        self._instances: dict[ModuleName, object] = {}
        self._owned: list[object] = []
        self._failures: dict[ModuleName, Exception] = {}
        self._states = {name: "uninitialized" if self.is_enabled(name) else "disabled" for name in MODULE_NAMES}
        self._lock = threading.RLock()
        self._closed = False

    def is_enabled(self, name: ModuleName) -> bool:
        """Read selection without loading code or creating a provider."""
        override = getattr(self.factories, name)
        if override is None:
            return False
        return not isinstance(override, DefaultModule) or getattr(self.config, name).enabled

    def is_default(self, name: ModuleName) -> bool:
        """Whether the enabled selection uses the built-in implementation."""
        return (self.is_enabled(name) and isinstance(getattr(self.factories, name), DefaultModule)
                and not getattr(self.config, name).factory)

    def status(self) -> dict[str, dict[str, object]]:
        """Inspect module availability without causing initialization."""
        with self._lock:
            return {
                name: {"enabled": self.is_enabled(name), "state": self._states[name],
                       "error": str(self._failures[name]) if name in self._failures else ""}
                for name in MODULE_NAMES
            }

    def optional(self, name: ModuleName, context: ModuleContext | None = None):
        """Resolve an optional integration without recreating absent modules."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Module host is closed")
            if not self.is_enabled(name):
                return None
            if name in self._failures:
                raise self._failures[name]
            if self._states[name] == "unavailable":
                return None
            scoped = name in {"agent", "short_term"}
            if not scoped and name in self._instances:
                return self._instances[name]
            override = getattr(self.factories, name)
            selection = getattr(self.config, name)
            factory_path = selection.factory
            builtin = isinstance(override, DefaultModule) and not factory_path
            try:
                if isinstance(override, DefaultModule):
                    if factory_path:
                        module_name, separator, attribute = factory_path.partition(":")
                        if not separator or not module_name or not attribute:
                            raise ValueError("Module factory must use 'package.module:factory'")
                        factory = getattr(importlib.import_module(module_name), attribute)
                    else:
                        from agentnexus.core import module_defaults

                        factory = getattr(module_defaults, name)
                else:
                    factory = override
                instance = factory(context or ModuleContext(settings=self.settings))
                if instance is None:
                    raise TypeError("An enabled module factory must return an implementation")
            except ModuleNotFoundError as exc:
                missing = exc.name or ""
                if builtin and missing and any(
                    package == missing or package.startswith(missing + ".")
                    for package in _IMPLEMENTATIONS[name]
                ):
                    self._states[name] = "unavailable"
                    return None
                error = ModuleLoadError(name, exc)
                self._failures[name] = error
                self._states[name] = "failed"
                raise error from exc
            except Exception as exc:
                error = ModuleLoadError(name, exc)
                self._failures[name] = error
                self._states[name] = "failed"
                raise error from exc
            if not scoped:
                self._instances[name] = instance
            self._owned.append(instance)
            self._states[name] = "ready"
            return instance

    def require(self, name: ModuleName, context: ModuleContext | None = None):
        """Resolve a direct operation or report its unavailable capability."""
        instance = self.optional(name, context)
        if instance is None:
            raise ModuleUnavailable(name, self._states[name])
        return instance

    @contextmanager
    def activate(self) -> Iterator[ModuleHost]:
        """Bind this owner's selection for one application entry scope."""
        token = _active_host.set(self)
        try:
            yield self
        finally:
            _active_host.reset(token)

    def close(self) -> None:
        """Close successfully acquired resources once in reverse order."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            owned, self._owned = self._owned, []
        errors = []
        seen = set()
        for instance in reversed(owned):
            if id(instance) in seen:
                continue
            seen.add(id(instance))
            close = getattr(instance, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    errors.append(exc)
        with self._lock:
            self._states = {name: "closed" for name in MODULE_NAMES}
        if errors:
            raise ExceptionGroup("Module shutdown failed", errors)


def active_module_host() -> ModuleHost | None:
    """Read the explicit entry owner without creating a standalone host."""
    return _active_host.get()


def get_module_host() -> ModuleHost:
    """Resolve explicit entry context or a settings-scoped standalone owner."""
    active = _active_host.get()
    if active is not None:
        return active
    from agentnexus.core.config import get_settings

    settings = get_settings()
    global _default_host
    with _default_lock:
        if _default_host is None or _default_host.settings is not settings or _default_host._closed:
            _default_host = ModuleHost(settings)
        return _default_host
