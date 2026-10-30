"""Independent engines execute through the application's public chat entry."""

from dataclasses import dataclass, replace

import pytest

from agentnexus.app.runtime import AppRuntime
from agentnexus.core.agent_contracts import AgentSignal
from agentnexus.core.config import Settings
from agentnexus.core.modules import MODULE_NAMES, ModuleFactories


@dataclass(frozen=True, slots=True)
class ScriptedEngine:
    def run(self, question, *, memory_manager=None, images=None, attachment_note=None):
        return "answer from the selected engine"


def test_independent_engine_completes_and_persists_a_turn(tmp_path):
    factories = replace(ModuleFactories.disabled(), agent=lambda context: ScriptedEngine())
    with AppRuntime.build(module_factories=factories, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        run = runtime.chat.send_message(session.id, "hello", client_message_id="custom-engine-submission")
        events = list(runtime.chat.stream_events(run.id, timeout=1))
        assert any(event.type == "message_delta" and event.payload["text"] == "answer from the selected engine"
                   for event in events)
        assert runtime.chat.get_run_recovery(run.id)["persisted"] is True


def test_engine_emits_public_signals_without_react_state_arguments(tmp_path):
    class EventEngine:
        event_handler = None

        def run(self, question, *, memory_manager=None):
            self.event_handler(AgentSignal("TOOL_START", {"name": "selected_tool"}))
            return "selected engine done"

    factories = replace(ModuleFactories.disabled(), agent=lambda context: EventEngine())
    with AppRuntime.build(module_factories=factories, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        run = runtime.chat.send_message(session.id, "hello")
        events = list(runtime.chat.stream_events(run.id, timeout=1))
        assert any(event.type == "tool_start" and event.payload["name"] == "selected_tool" for event in events)
        assert runtime.chat.get_run_recovery(run.id)["answer"] == "selected engine done"


@pytest.mark.parametrize("state_args", [(), ("before", "after")])
def test_engine_event_bridge_preserves_callback_arguments(tmp_path, state_args):
    received = []

    class EventEngine:
        def __init__(self):
            if state_args:
                self.event_handler = lambda event, before, after: received.append((getattr(event, "type", None), before, after))
            else:
                self.event_handler = lambda event: received.append((getattr(event, "type", None),))

        def run(self, question, *, memory_manager=None):
            self.event_handler(AgentSignal("ANSWER_READY"), *state_args)
            self.event_handler(None, *state_args)
            return "callback preserved"

    factories = replace(ModuleFactories.disabled(), agent=lambda context: EventEngine())
    with AppRuntime.build(module_factories=factories, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        run = runtime.chat.send_message(session.id, "hello")
        assert runtime.chat.get_run_recovery(run.id)["answer"] == "callback preserved"
        assert received == [("ANSWER_READY", *state_args), (None, *state_args)]


def test_checkpoint_restore_remains_usable_without_memory_modules(tmp_path):
    with AppRuntime.build(module_factories=ModuleFactories.disabled(), workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        version = runtime.chat.session_version(session.id)
        version.commit_with_messages([
            {"role": "user", "content": "saved question"},
            {"role": "assistant", "content": "saved answer"},
        ], question="saved question", answer="saved answer")
        runtime.chat.restore_checkpoint_memory(session.id)
        assert runtime.chat.session_memory(session.id) is None


def test_session_history_uses_the_owning_runtime_database(tmp_path):
    import sqlite3

    database = tmp_path / "selected-runtime.db"
    settings = Settings(memory_db_path=str(database))
    factories = replace(ModuleFactories.disabled(), agent=lambda context: ScriptedEngine())
    with AppRuntime.build(settings=settings, module_factories=factories, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session(workspace=str(tmp_path))
        runtime.chat.send_message(session.id, "selected database question")
        with sqlite3.connect(database) as connection:
            rows = connection.execute("SELECT content FROM conversation_messages WHERE session_id = ?", (session.id,)).fetchall()
            answers = connection.execute("SELECT answer FROM conversation_checkpoints WHERE session_id = ? AND answer != ''", (session.id,)).fetchall()
        assert rows == [("selected database question",)]
        assert answers == [("answer from the selected engine",)]
    with AppRuntime.build(settings=settings, module_factories=factories, workspace_path=str(tmp_path)) as restored:
        assert restored.chat.ensure_session_restored(session.id) is True
        assert restored.chat.session_version(session.id).get_messages_with_answers()[0]["content"] == "selected database question"


def test_configured_factory_path_runs_without_default_implementations(tmp_path, monkeypatch):
    plugin = tmp_path / "configured_engine.py"
    plugin.write_text('''
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class Engine:
    session_id: str

    def run(self, question, *, memory_manager=None):
        return "configured engine: " + self.session_id

def create_engine(context):
    return Engine(context.session_id)
''', encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    selections = {name: {"enabled": False} for name in MODULE_NAMES}
    selections["agent"] = {"enabled": True, "factory": "configured_engine:create_engine"}
    settings = Settings(memory_db_path=str(tmp_path / "configured.db"), modules=selections)
    with AppRuntime.build(settings=settings, workspace_path=str(tmp_path)) as runtime:
        session = runtime.chat.start_session()
        run = runtime.chat.send_message(session.id, "hello")
        assert runtime.chat.get_run_recovery(run.id)["answer"] == "configured engine: " + session.id
        assert runtime.chat.get_run_recovery(run.id)["persisted"] is True
