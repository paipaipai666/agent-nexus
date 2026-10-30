from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.core.modules import ModuleFactories, ModuleHost, ModuleLoadError, ModuleUnavailable


def block_packages(monkeypatch, *packages):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if any(name == package or name.startswith(package + ".") for package in packages):
            raise AssertionError(f"Unexpected optional import: {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def prepare_ingestion(monkeypatch):
    from agentnexus.rag import kb_service

    artifact = SimpleNamespace(document=SimpleNamespace(raw_text="source body", indexed_text="", content=""))
    monkeypatch.setattr(kb_service, "ingest_document", lambda *a, **k: artifact)
    monkeypatch.setattr(kb_service, "persist_ingested_document", lambda *a, **k: {"written_chunks": 1, "replaced_chunks": 0})
    monkeypatch.setattr("agentnexus.rag.model_assets.prepare_embedding", lambda **k: (object(), object()))
    monkeypatch.setattr("agentnexus.rag.kb_index.ensure_embedding_index", lambda *a, **k: None)
    return kb_service


def test_kb_ingestion_disables_wiki_without_importing_it(monkeypatch):
    kb = prepare_ingestion(monkeypatch)
    host = ModuleHost(factories=ModuleFactories(wiki=None))
    block_packages(monkeypatch, "agentnexus.wiki")
    _, run = kb.ingest_one_document("source.txt", namespace="wiki-off", enable_contextual=False, module_host=host)
    assert run.status == "completed"
    assert run.chunks_written == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_kb_ingestion_uses_the_selected_wiki(monkeypatch, explicit):
    kb = prepare_ingestion(monkeypatch)
    sources = []

    class IndependentWiki:
        def ingest_source(self, **source):
            sources.append(source)

    host = ModuleHost(factories=ModuleFactories(wiki=lambda context: IndependentWiki()))
    block_packages(monkeypatch, "agentnexus.wiki")
    with host.activate():
        _, run = kb.ingest_one_document(
            "source.txt", namespace="selected", enable_contextual=False,
            **({"module_host": host} if explicit else {}),
        )
    assert run.status == "completed"
    assert sources == [{"source_text": "source body", "source_uri": "source.txt", "source_namespace": "selected"}]


def test_broken_enabled_wiki_is_reported_before_kb_writes(monkeypatch):
    kb = prepare_ingestion(monkeypatch)
    persisted = []
    monkeypatch.setattr(kb, "persist_ingested_document", lambda *a, **k: persisted.append(True) or {"written_chunks": 1, "replaced_chunks": 0})

    def broken_provider(context):
        raise RuntimeError("Wiki connection failed")

    host = ModuleHost(factories=ModuleFactories(wiki=broken_provider))
    with host.activate(), pytest.raises(ModuleLoadError, match="Wiki connection failed"):
        kb.ingest_one_document("source.txt", namespace="broken", enable_contextual=False)
    assert persisted == []


def test_explicit_wiki_override_controls_ingest_hooks():
    from agentnexus.services.wiki import get_wiki_service
    from agentnexus.wiki.wiki_service import WikiService

    service = WikiService(store=MagicMock(), verifier=MagicMock(), router=MagicMock())
    service.propagation = MagicMock()
    host = ModuleHost(factories=ModuleFactories(wiki=lambda context: service))
    assert host.settings.wiki_enabled is False
    with host.activate():
        get_wiki_service().on_rag_ingest(["new-chunk"])
    service.propagation.on_chunk_update.assert_called_once_with(["new-chunk"])


@pytest.mark.parametrize("owned", [False, True])
def test_wiki_closes_only_its_owned_store_once(owned):
    from agentnexus.wiki.wiki_service import WikiService

    store = MagicMock()
    service = WikiService(store=store, owns_store=owned)
    host = ModuleHost(factories=ModuleFactories(wiki=lambda context: service))
    assert host.require("wiki") is service
    host.close()
    host.close()
    assert store.close.call_count == int(owned)


def test_wiki_management_route_uses_a_service_without_a_store(monkeypatch):
    from agentnexus.server.routes import wiki

    page = SimpleNamespace(page_id="p", title="Selected", page_type="concept", confidence="high", statements=[], source_namespace="n", created_at="", updated_at="")

    class IndependentWiki:
        def list_pages(self, source_namespace="", limit=100):
            assert (source_namespace, limit) == ("n", 10)
            return [page]

        def get_page(self, page_id, include_statements=True):
            assert page_id == "p"
            return page

    block_packages(monkeypatch, "agentnexus.wiki")
    result = wiki.list_wiki_pages(namespace="n", limit=10, service=IndependentWiki())
    assert result["pages"][0]["title"] == "Selected"


def test_eval_routes_use_selected_service_across_both_namespaces(monkeypatch):
    from agentnexus.server.routes import eval_routes

    class IndependentEvaluation:
        def list_datasets(self):
            return [{"name": "replacement", "filename": "replacement.jsonl", "samples": 4}]

        def list_benchmark_suites(self):
            return [{"name": "selected", "description": "replacement", "datasets": []}]

    host = ModuleHost(factories=ModuleFactories(evaluation=lambda context: IndependentEvaluation()))
    block_packages(monkeypatch, "agentnexus.eval", "agentnexus.evaluation")
    with host.activate():
        assert eval_routes.list_datasets() == {"datasets": [{"name": "replacement", "filename": "replacement.jsonl", "samples": 4}]}
        assert eval_routes.list_benchmark_suites() == {"suites": [{"name": "selected", "description": "replacement", "datasets": []}]}


def test_disabled_evaluation_routes_fail_before_loading_implementations(monkeypatch):
    from agentnexus.server.routes import eval_routes

    host = ModuleHost(factories=ModuleFactories(evaluation=None))
    block_packages(monkeypatch, "agentnexus.eval", "agentnexus.evaluation")
    with host.activate(), pytest.raises(ModuleUnavailable, match="evaluation"):
        eval_routes.list_benchmark_suites()


def test_benchmark_cli_uses_selected_provider_without_default_import(monkeypatch, capsys):
    block_packages(monkeypatch, "agentnexus.eval", "agentnexus.evaluation")
    from agentnexus.cli.eval.benchmark import benchmark_list

    class IndependentEvaluation:
        def cli_benchmark_list(self):
            print("selected benchmark command")

    host = ModuleHost(factories=ModuleFactories(evaluation=lambda context: IndependentEvaluation()))
    with host.activate():
        benchmark_list()
    assert "selected benchmark command" in capsys.readouterr().out


def test_disabled_cli_rejects_before_default_command(monkeypatch):
    from agentnexus.cli.eval.memory import eval_memory

    host = ModuleHost(factories=ModuleFactories(evaluation=None))
    block_packages(monkeypatch, "agentnexus.eval", "agentnexus.evaluation")
    with host.activate(), pytest.raises(ModuleUnavailable, match="evaluation"):
        eval_memory()


def test_evaluation_trials_use_the_selected_engine(monkeypatch):
    from agentnexus.evaluation.runner import ReActAgentRunner

    contexts = []

    class IndependentEngine:
        def run(self, question):
            return SimpleNamespace(answer=f"selected: {question}", steps=[])

    def create_engine(context):
        contexts.append(context)
        return IndependentEngine()

    host = ModuleHost(factories=ModuleFactories(agent=create_engine, observability=None))
    monkeypatch.setattr("agentnexus.core.llm.AgentLLM", lambda: object())
    monkeypatch.setattr("agentnexus.tools.registry.ToolRegistry", lambda: object())
    monkeypatch.setattr("agentnexus.tools.register_all_tools", lambda *a, **k: None)
    block_packages(monkeypatch, "agentnexus.agents.re_act_agent", "agentnexus.observability")
    task = SimpleNamespace(id="selected-task", input={"prompt": "trial"}, max_turns=7)
    with host.activate():
        result = ReActAgentRunner()(task, 0)
    assert result.error is None
    assert result.outcome["answer"] == "selected: trial"
    assert contexts[0].max_steps == 7


@pytest.mark.parametrize("arguments,operation", [
    (["agent"], "cli_eval_agent"),
    (["memory"], "cli_eval_memory"),
    (["fg"], "cli_eval_fine_grained"),
    (["humaneval"], "cli_eval_humaneval"),
    (["swe-bench"], "cli_eval_swebench"),
    (["trajectory"], "cli_eval_trajectory"),
    (["component"], "cli_eval_component"),
    (["hallucination"], "cli_eval_hallucination"),
    (["coherence"], "cli_eval_coherence"),
    (["run"], "cli_eval_run"),
    (["benchmark", "run"], "cli_benchmark_run"),
    (["benchmark", "run-rgb"], "cli_benchmark_run_rgb"),
    (["benchmark", "run-multihop"], "cli_benchmark_run_multihop"),
    (["memory-bench", "list"], "cli_memory_bench_list"),
    (["transcript", "list"], "cli_transcript_list"),
])
def test_cli_evaluation_families_share_the_selected_provider(monkeypatch, arguments, operation):
    from typer.testing import CliRunner

    from agentnexus.cli import app

    calls = []

    def selected(*args, **kwargs):
        calls.append((args, kwargs))
        print("replacement evaluation operation")

    service = SimpleNamespace(**{operation: selected})
    host = ModuleHost(factories=ModuleFactories(evaluation=lambda context: service))
    block_packages(monkeypatch, "agentnexus.eval", "agentnexus.evaluation")
    with host.activate():
        result = CliRunner().invoke(app, ["eval", *arguments])
    assert result.exit_code == 0, result.output
    assert "replacement evaluation operation" in result.output
    assert len(calls) == 1
