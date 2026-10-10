"""Regressions for bottlenecks reproduced by the local performance baseline."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.capabilities.runtime import CapabilityRuntime
from agentnexus.core import config
from agentnexus.core.llm import AgentLLM
from agentnexus.core.providers.base import StreamResult
from agentnexus.core.providers.openai_provider import OpenAIProvider
from agentnexus.skills.router.parse import extract_intent_signals
from agentnexus.storage import chroma
from agentnexus.tools.registry import ToolMeta, ToolRegistry


def test_tool_only_response_does_not_retry(monkeypatch):
    tool_calls = [{"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}]
    llm = AgentLLM(model="test", api_key="test", base_url="http://localhost/v1")
    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: object())
    call = MagicMock(return_value=StreamResult(tool_calls=tool_calls, finish_reason="tool_calls",
                                             usage={"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(llm, "_call_via_provider", call)
    sleep = MagicMock()
    monkeypatch.setattr("agentnexus.core.llm.time.sleep", sleep)

    assert llm.think([{"role": "user", "content": "Read a.txt"}], silent=True) == ""
    assert llm.last_tool_calls == tool_calls
    call.assert_called_once()
    sleep.assert_not_called()


def test_openai_reuses_client_and_closes_streams(monkeypatch):
    constructor = MagicMock()
    streams = [MagicMock(), MagicMock()]
    for stream in streams:
        stream.__iter__.return_value = iter([])
    constructor.return_value.chat.completions.create.side_effect = streams
    monkeypatch.setattr("agentnexus.core.providers.openai_provider.OpenAI", constructor)
    provider = OpenAIProvider()

    for model, url in [("model_a", "http://localhost/v1/"), ("model_b", "http://localhost/v1")]:
        provider.stream_chat([], model=model, api_key="test", base_url=url)

    constructor.assert_called_once()
    assert [c.kwargs["model"] for c in constructor.return_value.chat.completions.create.call_args_list] == ["model_a", "model_b"]
    for stream in streams:
        stream.close.assert_called_once()
    provider.close()
    constructor.return_value.close.assert_called_once()


def test_openai_client_profiles_are_isolated(monkeypatch):
    clients = [MagicMock() for _ in range(3)]
    for client in clients:
        client.chat.completions.create.return_value = []
    constructor = MagicMock(side_effect=clients)
    monkeypatch.setattr("agentnexus.core.providers.openai_provider.OpenAI", constructor)
    provider = OpenAIProvider()
    profiles = [("key_a", "http://a/v1", 60), ("key_b", "http://a/v1", 60),
                ("key_a", "http://b/v1", 30), ("key_a", "http://a/v1/", 60)]
    for key, url, timeout in profiles:
        provider.stream_chat([], model="test", api_key=key, base_url=url, timeout=timeout)
    assert constructor.call_count == 3
    assert [c.chat.completions.create.call_count for c in clients] == [2, 1, 1]
    provider.close()
    assert all(c.close.call_count == 1 for c in clients)


def test_openai_concurrent_calls_create_one_client(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = []

    def construct(**_):
        time.sleep(0.03)
        return client

    constructor = MagicMock(side_effect=construct)
    monkeypatch.setattr("agentnexus.core.providers.openai_provider.OpenAI", constructor)
    provider = OpenAIProvider()
    barrier = threading.Barrier(8)

    def call(_):
        barrier.wait(timeout=3)
        provider.stream_chat([], model="test", api_key="test", base_url="http://localhost/v1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(call, range(8)))
    constructor.assert_called_once()
    assert client.chat.completions.create.call_count == 8
    provider.close()


def test_openai_closes_stream_when_callback_fails(monkeypatch):
    stream = MagicMock()
    chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(
        delta=SimpleNamespace(content="token", reasoning_content=None, tool_calls=[]), finish_reason="stop")])
    stream.__iter__.return_value = iter([chunk])
    client = MagicMock()
    client.chat.completions.create.return_value = stream
    monkeypatch.setattr("agentnexus.core.providers.openai_provider.OpenAI", lambda **_: client)
    provider = OpenAIProvider()

    def callback(_):
        raise ValueError("callback failed")

    with pytest.raises(ValueError, match="callback failed"):
        provider.stream_chat([], model="test", api_key="test", base_url="http://localhost/v1", on_token=callback)
    stream.close.assert_called_once()
    provider.close()
    provider.close()
    client.close.assert_called_once()


def test_application_close_releases_provider_pools(monkeypatch):
    from agentnexus.app.runtime import AppRuntime
    from agentnexus.core.providers import router

    providers = [MagicMock(), MagicMock()]
    monkeypatch.setattr(router, "_openai_provider", providers[0])
    monkeypatch.setattr(router, "_anthropic_provider", providers[1])
    runtime = SimpleNamespace(mcp_manager=MagicMock())
    AppRuntime.close(runtime)
    runtime.mcp_manager.close.assert_called_once()
    for provider in providers:
        provider.close.assert_called_once()
    router.close_providers()
    assert router._openai_provider is None
    assert router._anthropic_provider is None


def test_cancelling_one_shared_llm_session_preserves_other_stream(monkeypatch):
    class BlockingStream:
        def __init__(self):
            self.started = threading.Event()
            self.released = threading.Event()
            self.closed = False

        def __iter__(self):
            self.started.set()
            assert self.released.wait(timeout=3)
            if self.closed:
                raise RuntimeError("cancelled stream")
            yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
                delta=SimpleNamespace(content="done", reasoning_content=None, tool_calls=[]),
                finish_reason="stop")])

        def close(self):
            self.closed = True
            self.released.set()

    streams = {name: BlockingStream() for name in ("a", "b")}
    client = MagicMock()
    client.chat.completions.create.side_effect = lambda **kw: streams[kw["messages"][0]["content"]]
    monkeypatch.setattr("agentnexus.core.providers.openai_provider.OpenAI", lambda **_: client)
    provider = OpenAIProvider()
    monkeypatch.setattr("agentnexus.core.llm.select_provider", lambda *_: provider)
    monkeypatch.setattr(AgentLLM, "capabilities", property(lambda _: SimpleNamespace(
        supports_thinking=False, max_output_tokens=64)))
    llm = AgentLLM(model="test", api_key="test", base_url="http://localhost/v1")
    cancel = {name: threading.Event() for name in streams}

    def run(name):
        llm.set_cancel_checker(cancel[name].is_set)
        return llm.think([{"role": "user", "content": name}], silent=True, max_attempts=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, "a")
        assert streams["a"].started.wait(timeout=2)
        b = pool.submit(run, "b")
        assert streams["b"].started.wait(timeout=2)
        try:
            cancel["a"].set()
            with pytest.raises(AgentCancelled):
                a.result(timeout=1)
            assert not streams["b"].closed
            assert not b.done()
            streams["b"].released.set()
            assert b.result(timeout=1) == "done"
        finally:
            for stream in streams.values():
                stream.released.set()
    provider.close()


@pytest.mark.parametrize("text", ["x" * 20_000, "先" * 20_000, "如果" * 10_000],
                         ids=["plain", "repeated_sequence_prefix", "repeated_condition_prefix"])
def test_long_input_intent_parser_completes_promptly(text):
    start = time.perf_counter()
    signals = extract_intent_signals(text, set())
    elapsed = time.perf_counter() - start
    assert signals.connectors == ()
    assert signals.priority_mode == "default"
    assert elapsed < 0.5, f"20k-character parsing took {elapsed:.3f}s"


@pytest.mark.parametrize("text,connector,position,mode", [
    ("读取之后总结", "读取之后", 0, "first_action"),
    ("标题\n读取之后总结", "读取之后", 3, "first_action"),
    ("标题\n有内容的话就总结", "有内容的话就", 3, "conditional"),
    ("之后\n读取之后总结", "读取之后", 3, "first_action"),
])
def test_connector_positions_preserve_multiline_semantics(text, connector, position, mode):
    signals = extract_intent_signals(text, set())
    assert signals.connectors == ((connector, position),)
    assert signals.priority_mode == mode


def _register(executor, name, source_type="builtin", source_id="builtin"):
    executor.register(ToolMeta(name=name, description=name, param_schema={"type": "object", "properties": {}},
                               source_type=source_type, source_id=source_id), lambda: name)


def test_concurrent_capability_refresh_loads_tools_once():
    executor = ToolRegistry()
    _register(executor, "old")
    _register(executor, "external", "mcp", "mcp:test")

    def register_tools(registry, **_):
        time.sleep(0.03)
        _register(registry, "new")

    hook = MagicMock(side_effect=register_tools)
    runtime = CapabilityRuntime(settings=SimpleNamespace(), executor=executor, register_tools=hook)
    barrier = threading.Barrier(8)

    def refresh():
        barrier.wait(timeout=3)
        return runtime.refresh_if_stale()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: refresh(), range(8)))
    assert all(not value.startswith("error:") for result in results for value in result.values())
    hook.assert_called_once()
    assert executor.get_tool("old") is None
    assert executor.get_tool("new") is not None
    assert executor.get_tool("external") is not None
    assert runtime.states["tools"].loaded_generation == runtime.states["tools"].generation


def test_mcp_reload_uses_actual_tool_registry():
    executor = ToolRegistry()
    _register(executor, "old_mcp", "mcp", "mcp:test")
    _register(executor, "builtin")
    manager = MagicMock()
    manager.server_names.return_value = []
    manager.register_tools.side_effect = lambda registry: _register(registry, "new_mcp", "mcp", "mcp:test")
    runtime = CapabilityRuntime(settings=SimpleNamespace(), executor=executor, mcp_manager=manager)
    assert runtime.reload("mcp") == {"mcp": "reloaded"}
    assert executor.get_tool("old_mcp") is None
    assert executor.get_tool("new_mcp") is not None
    assert executor.get_tool("builtin") is not None


def test_tool_reload_preserves_session_todo_tools():
    from agentnexus.memory.todo import SessionTodoList
    from agentnexus.tools.providers.base import ToolProviderContext
    from agentnexus.tools.providers.todo_provider import TodoToolProvider

    todo = SessionTodoList(session_id="reload_todo", db_path=config.get_settings().memory_db_path)
    todo.add("keep existing item")
    executor = ToolRegistry()

    def register_tools(registry, **kwargs):
        TodoToolProvider().register(registry, ToolProviderContext(todo_list=kwargs.get("todo_list")))

    runtime = CapabilityRuntime(settings=SimpleNamespace(), executor=executor,
                                agent=SimpleNamespace(_todo_list=todo), register_tools=register_tools)
    assert runtime.reload("tools") == {"tools": "reloaded"}
    add = executor.get_tool("todo_add")
    assert add is not None
    add("new item")
    assert [item.description for item in todo.list_items()] == ["keep existing item", "new item"]


def test_failed_vector_write_can_be_retried_with_identical_content(tmp_path, monkeypatch):
    from agentnexus.rag import kb_service
    from agentnexus.rag.models import ChunkRecord, IngestedDocument, SourceDocument
    from agentnexus.rag.store import KnowledgeBaseCatalog

    catalog = KnowledgeBaseCatalog(db_path=str(tmp_path / "catalog.db"))
    monkeypatch.setattr(kb_service, "get_knowledge_base_catalog", lambda: catalog)
    vectors = set()
    collection = SimpleNamespace(get=lambda **kwargs: {"ids": list(vectors.intersection(kwargs["ids"]))})
    monkeypatch.setattr(kb_service, "get_collection", lambda **_: collection)
    monkeypatch.setattr(kb_service, "delete_documents", lambda **kwargs: vectors.difference_update(kwargs["ids"]))
    doc = SourceDocument(document_id="document", kb_id="", source_id="source", source_uri="doc.md",
                         document_version="v1", content="content")
    chunks = [ChunkRecord(chunk_id=f"chunk_{i}", kb_id="", document_id="document", document_version="v1",
                          chunk_index=i, text=f"text_{i}") for i in range(3)]
    artifacts = IngestedDocument(document=doc, chunks=chunks)

    def partial_write(*_, **kwargs):
        vectors.add(kwargs["ids"][0])
        raise RuntimeError("batch write failed")

    monkeypatch.setattr(kb_service, "upsert_documents", partial_write)
    try:
        with pytest.raises(RuntimeError, match="batch write failed"):
            kb_service.persist_ingested_document(artifacts, "retry")
        monkeypatch.setattr(kb_service, "upsert_documents", lambda *_, **kwargs: vectors.update(kwargs["ids"]))
        result = kb_service.persist_ingested_document(artifacts, "retry")
        assert not result.get("skipped")
        assert result["written_chunks"] == 3
        assert vectors == {"chunk_0", "chunk_1", "chunk_2"}
        assert catalog.count_chunks("document") == 3
    finally:
        catalog.close()


def test_config_writer_waits_for_open_reader(monkeypatch):
    config.write_config_yaml({"llm_model_id": "old"})
    reader_open = threading.Event()
    release_reader = threading.Event()
    original_load = config.yaml.safe_load
    original_replace = Path.replace

    def held_load(stream):
        reader_open.set()
        assert release_reader.wait(timeout=3)
        try:
            return original_load(stream)
        finally:
            reader_open.clear()

    def windows_replace(path, target):
        if reader_open.is_set():
            raise PermissionError("Windows cannot replace a file with an open reader")
        return original_replace(path, target)

    monkeypatch.setattr(config.yaml, "safe_load", held_load)
    monkeypatch.setattr(Path, "replace", windows_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(config.load_config_yaml)
        assert reader_open.wait(timeout=2)
        writer = pool.submit(config.write_config_yaml, {"llm_model_id": "new"})
        try:
            time.sleep(0.1)
        finally:
            release_reader.set()
        assert reader.result(timeout=3)["llm_model_id"] == "old"
        writer.result(timeout=3)
    assert config.get_settings().llm_model_id == "new"


@pytest.mark.parametrize("operation", [chroma.insert_documents, chroma.upsert_documents])
def test_chroma_writes_and_embeds_within_collection_batch_limit(operation):
    collection = MagicMock()
    collection._client.get_max_batch_size.return_value = 2
    texts = ["a", "b", "c", "d", "e"]
    ids = [f"id_{i}" for i in range(5)]
    metadatas = [{"order": i} for i in range(5)]
    embed = MagicMock(side_effect=lambda batch: [[float(ord(t))] for t in batch])
    result = operation(texts, metadatas, ids, collection_provider=lambda **_: collection,
                       embed_texts_provider=embed)
    assert result == ids
    write = collection.add if operation is chroma.insert_documents else collection.upsert
    batches = [call.kwargs for call in write.call_args_list]
    assert [batch["documents"] for batch in batches] == [["a", "b"], ["c", "d"], ["e"]]
    assert [item for batch in batches for item in batch["ids"]] == ids
    assert [item for batch in batches for item in batch["metadatas"]] == metadatas
    assert [item for batch in batches for item in batch["embeddings"]] == [[97.0], [98.0], [99.0], [100.0], [101.0]]
    assert [call.args[0] for call in embed.call_args_list] == [["a", "b"], ["c", "d"], ["e"]]


def test_chroma_bulk_add_and_upsert_store_every_document(tmp_path):
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    raw = client.get_or_create_collection("bulk_regression", embedding_function=None)
    collection = chroma.ThreadSafeChromaCollection(raw)
    count = client.get_max_batch_size() + 1
    ids = [f"id_{i}" for i in range(count)]
    texts = [f"document_{i}" for i in range(count)]
    kwargs = {"ids": ids, "collection_provider": lambda **_: collection,
              "embed_texts_provider": lambda batch: [[1.0, 0.5] for _ in batch]}

    assert chroma.insert_documents(texts, **kwargs) == ids
    assert collection.count() == count
    updated = [f"updated_{i}" for i in range(count)]
    assert chroma.upsert_documents(updated, **kwargs) == ids
    assert collection.count() == count
    rows = collection.get(ids=ids, include=["documents"])
    assert dict(zip(rows["ids"], rows["documents"])) == dict(zip(ids, updated))


def test_chroma_batch_failure_propagates_without_writing_later_batches():
    collection = MagicMock()
    collection._client.get_max_batch_size.return_value = 2
    collection.upsert.side_effect = [None, RuntimeError("write failed")]
    embed = MagicMock(side_effect=lambda batch: [[1.0] for _ in batch])
    with pytest.raises(RuntimeError, match="write failed"):
        chroma.upsert_documents(["a", "b", "c", "d", "e"], collection_provider=lambda **_: collection,
                                embed_texts_provider=embed)
    assert collection.upsert.call_count == 2
    assert embed.call_count == 2
