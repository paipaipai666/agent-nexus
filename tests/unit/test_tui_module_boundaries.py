"""Terminal commands bind their runtime before nested services resolve modules."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from agentnexus.core.modules import ModuleFactories, ModuleHost, get_module_host
from agentnexus.tui.commands import TerminalCommands


@pytest.mark.parametrize("wiki_enabled", [True, False])
def test_terminal_kb_ingest_uses_its_runtime_wiki_selection(monkeypatch, tmp_path, wiki_enabled):
    from agentnexus.rag import kb_service

    sources = []

    class Wiki:
        def __init__(self, label):
            self.label = label

        def ingest_source(self, **source):
            sources.append((self.label, source["source_text"]))

    owner = ModuleHost(factories=replace(ModuleFactories.disabled(),
                                        wiki=(lambda ctx: Wiki("owner")) if wiki_enabled else None))
    ambient = ModuleHost(factories=replace(ModuleFactories.disabled(), wiki=lambda ctx: Wiki("ambient")))
    document = tmp_path / "document.txt"
    document.write_text("source body", encoding="utf-8")
    artifact = SimpleNamespace(document=SimpleNamespace(raw_text="source body", indexed_text="", content=""))
    monkeypatch.setattr(kb_service, "ingest_document", lambda *args, **options: artifact)
    monkeypatch.setattr(kb_service, "persist_ingested_document", lambda *args, **options: {"written_chunks": 1, "replaced_chunks": 0})
    monkeypatch.setattr("agentnexus.rag.model_assets.prepare_embedding", lambda **options: (object(), object()))
    monkeypatch.setattr("agentnexus.rag.kb_index.ensure_embedding_index", lambda *args, **options: None)
    commands = TerminalCommands(SimpleNamespace(modules=owner, llm=None))
    try:
        with ambient.activate():
            result = commands.run("/kb", f'ingest "{document}"', "terminal-session")
            assert result.value["status"] == "completed"
            assert get_module_host() is ambient
        assert sources == ([("owner", "source body")] if wiki_enabled else [])
    finally:
        owner.close()
        ambient.close()


def test_terminal_evaluation_nested_resolution_uses_its_runtime_owner():
    class Wiki:
        label = "owner"

    class Evaluation:
        def list_suites(self):
            return [{"name": get_module_host().require("wiki").label}]

    owner = ModuleHost(factories=replace(ModuleFactories.disabled(),
                                        wiki=lambda ctx: Wiki(), evaluation=lambda ctx: Evaluation()))
    ambient = ModuleHost(factories=ModuleFactories.disabled())
    commands = TerminalCommands(SimpleNamespace(modules=owner))
    try:
        with ambient.activate():
            assert commands.run("/eval", "suites", "terminal-session").value == {"suites": [{"name": "owner"}]}
            assert get_module_host() is ambient
    finally:
        owner.close()
        ambient.close()
