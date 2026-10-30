"""Every startup selection leaves the other selected providers usable."""

from contextlib import contextmanager
from itertools import product
from types import SimpleNamespace

import pytest

from agentnexus.app.runtime import AppRuntime
from agentnexus.core.config import Settings
from agentnexus.core.memory_contracts import TurnMemory
from agentnexus.core.modules import MODULE_NAMES, ModuleFactories, ModuleUnavailable


class Conversation(TurnMemory):
    pass


class Engine:
    def run(self, question, *, memory_manager=None):
        if memory_manager is not None:
            memory_manager.short_term.append("user", question)
            memory_manager.short_term.append("assistant", "matrix answer")
        return "matrix answer"


class Memories:
    write_counter = 0

    def search_text(self, query, **options):
        return [{"content": "matrix fact", "category": "fact"}]


class Wiki:
    def list_pages(self):
        return ["matrix page"]


class Evaluation:
    def list_tasks(self):
        return ["matrix task"]


class Observer:
    active = None
    audit_log = ()

    def __init__(self):
        self.events = []

    @contextmanager
    def span(self, name, input_data=None):
        yield SimpleNamespace(output={}, metadata={})

    def start_trace(self, task, metadata=None):
        self.events.append(task)

    def end_trace(self):
        pass

    def stats(self, days=7):
        return {}

    def evaluate_stats(self, stats):
        return []


@pytest.mark.parametrize("enabled", list(product((False, True), repeat=6)))
def test_all_module_selections_preserve_other_selected_operations(enabled, tmp_path, monkeypatch):
    from agentnexus.core import module_defaults
    from agentnexus.services.eval import get_eval_service
    from agentnexus.services.wiki import get_wiki_service

    def forbidden_default(context):
        pytest.fail("A disabled or replaced provider constructed a default implementation")

    for name in MODULE_NAMES:
        monkeypatch.setattr(module_defaults, name, forbidden_default)
    monkeypatch.setattr("agentnexus.core.capabilities.resolve_ctx_max", lambda *args: 128000)
    observer = Observer()
    constructors = (Engine, Conversation, Memories, Wiki, Evaluation, lambda: observer)
    factories = ModuleFactories(**{
        name: (lambda context, construct=constructor: construct()) if selected else None
        for name, constructor, selected in zip(MODULE_NAMES, constructors, enabled, strict=True)
    })
    settings = Settings(memory_db_path=str(tmp_path / "history.db"),
                        rag_catalog_db_path=str(tmp_path / "catalog.db"))
    with AppRuntime.build(settings=settings, module_factories=factories, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        if enabled[0]:
            run = runtime.chat.send_message(session.id, "matrix question")
            assert runtime.chat.get_run_recovery(run.id)["answer"] == "matrix answer"
            assert runtime.chat.get_run_recovery(run.id)["persisted"] is True
        else:
            with pytest.raises(ModuleUnavailable, match="agent"):
                runtime.chat.send_message(session.id, "matrix question")
        memory = runtime.chat.session_memory(session.id)
        if enabled[1] or enabled[2]:
            assert memory.retention_enabled is enabled[1]
            if not enabled[1]:
                memory.begin_turn()
                assert memory.short_term.get_all() == []
        else:
            assert memory is None
        if enabled[2]:
            assert runtime.modules.require("long_term").search_text("matrix")[0]["content"] == "matrix fact"
        if enabled[3]:
            assert get_wiki_service().list_pages() == ["matrix page"]
        if enabled[4]:
            assert get_eval_service().list_tasks() == ["matrix task"]
        if enabled[5] and enabled[0]:
            assert observer.events == ["matrix question"]
        for name, selected in zip(MODULE_NAMES, enabled, strict=True):
            if not selected:
                with pytest.raises(ModuleUnavailable, match=name):
                    runtime.modules.require(name)
