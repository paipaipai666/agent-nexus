"""Behavioral checks for independent conversation and long-term providers."""

import json
from types import SimpleNamespace

from agentnexus.core.modules import ModuleFactories, ModuleHost


class PublicConversation:
    def __init__(self):
        self.rows = []
        self.summary = ""

    def append(self, role, content, metadata=None):
        self.rows.append({"role": role, "content": content, "metadata": metadata or {}})

    def get_all(self):
        return list(self.rows)

    def get_summary(self):
        return self.summary

    def load_snapshot(self, messages, summary=""):
        self.rows = list(messages)
        self.summary = summary

    def replace_messages(self, messages):
        self.rows = list(messages)

    def estimate_tokens(self):
        return sum(len(message["content"]) for message in self.rows)

    @property
    def token_count(self):
        return self.estimate_tokens()

    def clear(self):
        self.rows.clear()
        self.summary = ""


class TextMemories:
    def __init__(self):
        self.records = []
        self.write_counter = 0

    def save_text(self, **record):
        self.records.append({"id": len(self.records) + 1, **record})
        self.write_counter += 1
        return len(self.records)

    def search_text(self, query, **options):
        return [record for record in self.records if query in record["content"]]


def test_manager_uses_public_snapshot_contract(temp_agentnexus_home):
    from agentnexus.memory.manager import MemoryManager

    conversation = PublicConversation()
    modules = ModuleHost(factories=ModuleFactories(short_term=lambda context: conversation, long_term=None))
    manager = MemoryManager("public", llm=object(), modules=modules)
    conversation.append("user", "blue door")
    assert manager.estimate_stm_tokens() == 9
    snapshot = manager.export_snapshot()
    conversation.clear()
    assert manager.restore_snapshot(snapshot) is True
    assert conversation.get_all()[0]["content"] == "blue door"


def test_disabled_conversation_keeps_only_current_turn(temp_agentnexus_home):
    from agentnexus.memory.manager import MemoryManager

    modules = ModuleHost(factories=ModuleFactories.disabled())
    manager = MemoryManager("ephemeral", llm=object(), modules=modules)
    manager.begin_turn()
    manager.append("user", "first question")
    assert manager.short_term.get_all()[0]["content"] == "first question"
    manager.begin_turn()
    assert manager.short_term.get_all() == []
    assert manager.restore_snapshot(json.dumps({"messages": [{"role": "user", "content": "old"}]})) is False
    assert manager.short_term.get_all() == []
    assert manager.long_term is None


def test_tools_use_selected_text_memory_without_default_models(temp_agentnexus_home):
    from agentnexus.tools.memory_save import memory_save
    from agentnexus.tools.memory_search import memory_search

    memories = TextMemories()
    modules = ModuleHost(factories=ModuleFactories(short_term=None, long_term=lambda context: memories))
    assert "已保存" in memory_save("blue door fact", modules=modules)
    assert "blue door fact" in memory_search("blue door", modules=modules)
    assert memories.records[0]["content"] == "blue door fact"


def test_disabled_memory_tools_report_unavailable(temp_agentnexus_home):
    from agentnexus.tools.memory_save import memory_save
    from agentnexus.tools.memory_search import memory_search

    modules = ModuleHost(factories=ModuleFactories.disabled())
    assert "不可用" in memory_save("blue door fact", modules=modules)
    assert "不可用" in memory_search("blue door", modules=modules)


def test_long_term_is_independent_of_conversation_retention(temp_agentnexus_home):
    from agentnexus.memory.manager import MemoryManager

    memories = TextMemories()
    memories.save_text(session_id="seed", content="blue door fact", category="fact")
    modules = ModuleHost(factories=ModuleFactories(short_term=None, long_term=lambda context: memories))
    manager = MemoryManager("independent", llm=object(), modules=modules)
    assert "blue door fact" in manager.init_session("blue door")
    assert manager.short_term.get_all() == []


def test_extraction_uses_text_backend_without_embedding_model(temp_agentnexus_home, monkeypatch):
    from agentnexus.memory.manager import MemoryManager

    class ExtractionLLM:
        def think(self, messages, silent=True):
            return '{"fact": ["remember the blue door"]}'

    memories = TextMemories()
    modules = ModuleHost(factories=ModuleFactories(short_term=None, long_term=lambda context: memories))
    manager = MemoryManager("extract", llm=ExtractionLLM(), modules=modules)
    def forbidden_model():
        raise AssertionError("Replacement memory must not initialize default models")

    monkeypatch.setattr("agentnexus.rag.embeddings.get_embedding_model", forbidden_model)
    manager._should_extract = lambda question, answer: True
    manager.conclude("记住蓝门", "remember the blue door").result(timeout=5)
    assert memories.records[0]["content"] == "remember the blue door"


def test_management_routes_do_not_create_models_for_replacements(temp_agentnexus_home, monkeypatch):
    from agentnexus.server.routes.memory import approve_pending_memory, run_reflection

    class ManagedMemory(TextMemories):
        def reflect(self, days=7, max_memories=50):
            return {"days": days, "pending": 1}

        def approve_pending(self, pending_id):
            return {"status": "approved", "pending_id": pending_id}

    def forbidden_model(*args, **kwargs):
        raise AssertionError("Selected memory backend owns its model dependencies")

    monkeypatch.setattr("agentnexus.core.llm.AgentLLM", forbidden_model)
    monkeypatch.setattr("agentnexus.rag.embeddings.get_embedding_model", forbidden_model)
    memory = ManagedMemory()
    assert run_reflection(days=3, max_memories=20, ltm=memory) == {"days": 3, "pending": 1}
    assert approve_pending_memory(7, ltm=memory) == {"status": "approved", "pending_id": 7}


def test_terminal_memory_command_uses_runtime_provider(temp_agentnexus_home):
    from agentnexus.tui.commands.memory import run

    class ListedMemory(TextMemories):
        def list_recent(self, limit):
            return self.records[-limit:]

    memory = ListedMemory()
    memory.save_text(session_id="seed", content="blue door fact", category="fact")
    modules = ModuleHost(factories=ModuleFactories(long_term=lambda context: memory))
    context = SimpleNamespace(runtime=SimpleNamespace(modules=modules))
    assert run(context, "/memory", "list 3", "session")[0]["content"] == "blue door fact"


def test_default_vector_stores_follow_each_runtime_settings(tmp_path, monkeypatch):
    from dataclasses import replace

    import chromadb

    from agentnexus.core.config import Settings
    from agentnexus.core.modules import DEFAULT

    first_settings = Settings(memory_db_path=str(tmp_path / "first.db"),
                              chroma_persist_dir=str(tmp_path / "first-vectors"))
    second_settings = Settings(memory_db_path=str(tmp_path / "second.db"),
                               chroma_persist_dir=str(tmp_path / "second-vectors"))
    monkeypatch.setattr("agentnexus.core.config.get_settings", lambda: first_settings)
    monkeypatch.setattr("agentnexus.memory.long_term.get_settings", lambda: first_settings)
    factories = replace(ModuleFactories.disabled(), long_term=DEFAULT)
    first, second = ModuleHost(first_settings, factories=factories), ModuleHost(second_settings, factories=factories)
    try:
        with first.activate():
            first.require("long_term").save("first", "first runtime fact", embedding=[1.0, 0.0, 0.0])
        with second.activate():
            second.require("long_term").save("second", "second runtime fact", embedding=[0.0, 1.0, 0.0])
        for settings in (first_settings, second_settings):
            client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
            assert client.get_collection("long_term_memories").count() == 1
        first.close()
        with second.activate():
            rows = second.require("long_term").search([0.0, 1.0, 0.0])
            assert [row["content"] for row in rows] == ["second runtime fact"]
    finally:
        first.close()
        second.close()


def test_background_context_resolution_uses_owning_settings(tmp_path, monkeypatch):
    import threading
    from dataclasses import replace

    from agentnexus.core.config import Settings
    from agentnexus.memory.manager import MemoryManager

    owner = Settings(llm_model_id="owner-model", llm_base_url="https://owner.invalid",
                     chroma_persist_dir=str(tmp_path / "owner"))
    other = Settings(llm_model_id="other-model", llm_base_url="https://other.invalid")
    monkeypatch.setattr("agentnexus.core.config.get_settings", lambda: other)
    calls = []

    def resolve_context(model, base_url):
        calls.append((model, base_url))
        return 32000 if model == "owner-model" else 16000

    start = threading.Thread.start

    def complete_worker(thread):
        start(thread)
        thread.join(timeout=5)
        assert not thread.is_alive()

    monkeypatch.setattr("agentnexus.core.capabilities.resolve_ctx_max", resolve_context)
    monkeypatch.setattr(threading.Thread, "start", complete_worker)
    modules = ModuleHost(owner, factories=replace(ModuleFactories.disabled(), short_term=lambda context: PublicConversation()))
    try:
        manager = MemoryManager("owned-background", llm=object(), modules=modules)
        assert manager.ctx_max == 32000
        assert manager.compact_threshold == 24000
        assert calls == [("owner-model", "https://owner.invalid")]
    finally:
        modules.close()
