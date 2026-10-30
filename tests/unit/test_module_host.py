"""Module selection does not initialize disabled implementations."""

import subprocess
import sys
from pathlib import Path

import pytest

from agentnexus.core.config import Settings
from agentnexus.core.modules import ModuleFactories, ModuleHost, ModuleLoadError, ModuleUnavailable, get_module_host


def test_disabled_modules_do_not_import_implementations():
    root = Path(__file__).resolve().parents[2]
    program = """
import importlib.abc
import sys

class BlockImplementations(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        packages = ('agentnexus.agents.re_act_agent', 'agentnexus.memory.short_term',
                    'agentnexus.memory.long_term', 'agentnexus.wiki',
                    'agentnexus.evaluation', 'agentnexus.eval', 'agentnexus.observability')
        if any(fullname == package or fullname.startswith(package + '.') for package in packages):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, BlockImplementations())
from agentnexus.core.config import Settings
from agentnexus.core.modules import ModuleFactories, ModuleHost

host = ModuleHost(Settings(), factories=ModuleFactories.disabled())
assert host.optional('agent') is None
assert host.optional('short_term') is None
assert host.optional('long_term') is None
assert host.optional('wiki') is None
assert host.optional('evaluation') is None
assert host.optional('observability') is None
host.close()
print('disabled modules remain unloaded')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", program],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "disabled modules remain unloaded"


def test_missing_implementation_leaves_other_modules_usable(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def import_without_wiki(name, *args, **kwargs):
        if name == "agentnexus.wiki.wiki_service":
            raise ModuleNotFoundError(name, name=name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_wiki)
    host = ModuleHost(Settings(wiki_enabled=True))
    assert host.optional("wiki") is None
    assert host.status()["wiki"]["state"] == "unavailable"
    with pytest.raises(ModuleUnavailable, match="wiki"):
        host.require("wiki")
    assert host.status()["evaluation"]["state"] == "uninitialized"


def test_legacy_wiki_environment_selects_wiki(monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_WIKI_ENABLED", "true")
    settings = Settings()
    assert settings.modules.wiki.enabled is True
    assert settings.wiki_enabled is True


def test_explicit_wiki_selection_overrides_legacy_setting():
    settings = Settings(wiki_enabled=True, modules={"wiki": {"enabled": False}})
    assert settings.modules.wiki.enabled is False
    assert settings.wiki_enabled is False


def test_default_wiki_storage_belongs_to_its_runtime(tmp_path):
    from agentnexus.wiki.models import WikiPage

    first = ModuleHost(Settings(wiki_enabled=True, rag_catalog_db_path=str(tmp_path / "first.db")))
    second = ModuleHost(Settings(wiki_enabled=True, rag_catalog_db_path=str(tmp_path / "second.db")))
    try:
        first_wiki = first.require("wiki")
        second_wiki = second.require("wiki")
        first_wiki.store.upsert_page(WikiPage(page_id="owned-page", title="owned by first"))
        assert second_wiki.get_page("owned-page") is None
        second.close()
        assert first_wiki.get_page("owned-page").title == "owned by first"
    finally:
        first.close()
        second.close()


def test_runtime_closing_does_not_close_process_owned_llm_providers(tmp_path, monkeypatch):
    from agentnexus.app.runtime import AppRuntime

    closed = []
    monkeypatch.setattr("agentnexus.core.providers.router.close_providers", lambda: closed.append(True))
    runtime = AppRuntime.build(module_factories=ModuleFactories.disabled(), workspace_path=str(tmp_path))
    runtime.close()
    runtime.close()
    assert closed == []


def test_runtime_context_activates_and_restores_its_owner(tmp_path):
    from agentnexus.app.runtime import AppRuntime

    outer = ModuleHost(factories=ModuleFactories.disabled())
    with outer.activate():
        with AppRuntime.build(module_factories=ModuleFactories.disabled(), workspace_path=str(tmp_path)) as runtime:
            assert get_module_host() is runtime.modules
        assert get_module_host() is outer


def test_concurrent_resolution_creates_one_runtime_provider():
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    created = []

    def factory(context):
        provider = object()
        created.append(provider)
        return provider

    host = ModuleHost(factories=replace(ModuleFactories.disabled(), long_term=factory))
    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = list(pool.map(lambda unused: host.require("long_term"), range(24)))
    assert len(created) == 1
    assert all(provider is created[0] for provider in resolved)
    host.close()


def test_shutdown_attempts_every_owned_resource_once_after_a_close_error():
    from dataclasses import replace

    closed = []

    class Resource:
        def __init__(self, name, broken=False):
            self.name = name
            self.broken = broken

        def close(self):
            closed.append(self.name)
            if self.broken:
                raise OSError("broken close")

    memory = Resource("memory")
    observer = Resource("observer", broken=True)
    host = ModuleHost(factories=replace(ModuleFactories.disabled(), long_term=lambda context: memory,
                                      observability=lambda context: observer))
    host.require("long_term")
    host.require("observability")
    with pytest.raises(ExceptionGroup, match="shutdown"):
        host.close()
    host.close()
    assert closed == ["observer", "memory"]


def test_missing_internal_dependency_is_a_load_error_not_module_absence(monkeypatch):
    from agentnexus.core import module_defaults

    def broken_factory(context):
        raise ModuleNotFoundError("provider_dependency", name="provider_dependency")

    monkeypatch.setattr(module_defaults, "wiki", broken_factory)
    host = ModuleHost(Settings(wiki_enabled=True))
    with pytest.raises(ModuleLoadError, match="provider_dependency"):
        host.require("wiki")
    assert host.status()["wiki"]["state"] == "failed"


def test_replacement_is_owned_once_and_context_is_restored():
    calls = []

    class Memory:
        def close(self):
            calls.append("closed")

    store = Memory()
    host = ModuleHost(Settings(), factories=ModuleFactories(long_term=lambda context: store))
    outer = get_module_host()
    with host.activate():
        assert get_module_host() is host
        assert host.require("long_term") is store
        assert host.require("long_term") is store
    assert get_module_host() is outer
    host.close()
    host.close()
    assert calls == ["closed"]


def test_broken_replacement_does_not_fall_back():
    calls = []

    def broken(context):
        calls.append("attempted")
        raise ModuleNotFoundError("provider dependency missing", name="missing_provider_dependency")

    host = ModuleHost(Settings(), factories=ModuleFactories(long_term=broken))
    with pytest.raises(ModuleLoadError, match="missing"):
        host.optional("long_term")
    with pytest.raises(ModuleLoadError, match="missing"):
        host.optional("long_term")
    assert calls == ["attempted"]
    assert host.status()["long_term"]["state"] == "failed"
    assert host.status()["evaluation"]["state"] == "uninitialized"
