"""Default-memory probes isolate resources and respect module selections."""

import os
from dataclasses import replace

import pytest

from agentnexus.core.config import get_settings
from agentnexus.core.modules import ModuleFactories, ModuleHost


def test_settings_follow_nested_owners_without_replacing_the_default_cache():
    import agentnexus.core.config as config

    default = get_settings()
    first_settings = default.model_copy(update={"max_memories": 123})
    second_settings = default.model_copy(update={"max_memories": 456})
    first, second = ModuleHost(first_settings), ModuleHost(second_settings)
    with first.activate():
        assert get_settings() is first_settings
        with second.activate():
            assert get_settings() is second_settings
        assert get_settings() is first_settings
    assert get_settings() is default
    assert config._settings_cache is default


def test_memory_sandbox_does_not_mutate_process_settings_or_home():
    import agentnexus.core.config as config
    from agentnexus.evaluation.memory_eval import MemorySandbox

    default = get_settings()
    home = os.environ.get("AGENTNEXUS_HOME")
    sandbox = MemorySandbox()
    try:
        assert os.environ.get("AGENTNEXUS_HOME") == home
        assert config._settings_cache is default
        assert get_settings().memory_db_path == str(sandbox.root / "memory.db")
        sandbox.ltm.save("evaluation", "only in sandbox")
    finally:
        sandbox.close()
    assert get_settings() is default
    assert config._settings_cache is default
    assert os.environ.get("AGENTNEXUS_HOME") == home


@pytest.mark.parametrize("name", ["short_term", "long_term"])
@pytest.mark.parametrize("replacement", [False, True])
def test_default_memory_probes_reject_disabled_or_replaced_implementations(name, replacement):
    from agentnexus.evaluation.memory_eval import MemorySandbox

    calls = []
    factories = replace(ModuleFactories(), **{name: (lambda context: calls.append(True) or object()) if replacement else None})
    host = ModuleHost(factories=factories)
    with host.activate():
        sandbox = MemorySandbox()
        try:
            with pytest.raises(RuntimeError, match=f"requires default {name} implementation"):
                sandbox.stm() if name == "short_term" else sandbox.ltm
        finally:
            sandbox.close()
    assert calls == []


def test_sandbox_close_does_not_close_an_existing_default_memory_provider():
    from agentnexus.evaluation.memory_eval import MemorySandbox
    from agentnexus.memory.long_term import get_long_term_memory

    existing = get_long_term_memory()
    existing.save("outside", "preserved outside evaluation")
    sandbox = MemorySandbox()
    sandbox.close()
    assert existing.list_recent()[0]["content"] == "preserved outside evaluation"
