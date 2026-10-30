"""Subagents use the same selected engine and modules as their owning runtime."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from agentnexus.core.config import Settings
from agentnexus.core.modules import ModuleFactories, ModuleHost, get_module_host
from agentnexus.tools import register_all_tools
from agentnexus.tools.registry import ToolRegistry


def test_selected_subagent_engine_runs_without_default_agent_package():
    program = '''
import importlib.abc
import json
import sys
from dataclasses import dataclass, replace

class MissingDefault(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentnexus.agents" or fullname.startswith("agentnexus.agents."):
            raise ModuleNotFoundError(fullname, name=fullname)

sys.meta_path.insert(0, MissingDefault())
from agentnexus.core.config import Settings
from agentnexus.core.modules import ModuleFactories, ModuleHost, get_module_host
from agentnexus.tools.subagent import make_subagent_run

seen = []
@dataclass(frozen=True, slots=True)
class SelectedEngine:
    def run(self, question, *, memory_manager=None):
        assert get_module_host() is owner
        assert "delegated task" in question
        return "selected child answer"

def engine(context):
    assert context.conversation_mode is False
    assert context.agent_id == "subagent_explorer"
    assert context.llm is None
    assert context.tools.get_tool("file_read") is not None
    seen.append(context.agent_id)
    return SelectedEngine()

owner = ModuleHost(Settings(), factories=replace(ModuleFactories.disabled(), agent=engine))
try:
    tool = make_subagent_run(non_interactive=True, modules=owner)
    result = json.loads(tool("delegated task", allowed_tools=["file_read"]))
    assert result["status"] == "ok", result
    assert result["answer"] == "selected child answer"
    assert result["steps_used"] == 0
    assert seen == ["subagent_explorer"]
finally:
    owner.close()
print("selected child completed without default agent package")
'''
    result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "selected child completed" in result.stdout


def test_disabled_engine_registers_no_delegation_tools():
    host = ModuleHost(Settings(), factories=ModuleFactories.disabled())
    executor = ToolRegistry()
    try:
        register_all_tools(executor, non_interactive=True, modules=host,
                           include_tools={"subagent_run", "subagent_start", "subagent_send", "subagent_wait"})
        assert executor.list_tools() == []
    finally:
        executor.close()
        host.close()


def test_registered_subagent_uses_its_owner_outside_activation():
    from agentnexus.tools.subagent import make_subagent_run

    @dataclass(frozen=True, slots=True)
    class SelectedEngine:
        def run(self, question, *, memory_manager=None):
            assert get_module_host() is host
            return "owner-selected result"

    host = ModuleHost(Settings(), factories=replace(ModuleFactories.disabled(), agent=lambda ctx: SelectedEngine()))
    unrelated = ModuleHost(Settings(), factories=ModuleFactories.disabled())
    try:
        tool = make_subagent_run(non_interactive=True, modules=host)
        with unrelated.activate():
            result = json.loads(tool("inspect", allowed_tools=["file_read"]))
            assert result["answer"] == "owner-selected result"
            assert get_module_host() is unrelated
    finally:
        host.close()
        unrelated.close()


def test_selected_engine_supports_async_collaboration_and_public_events(tmp_path):
    from agentnexus.core.agent_contracts import AgentSignal
    from agentnexus.services.collaboration import CollaborationRun
    from agentnexus.services.subagents import SubagentRegistry, SubagentRunContext
    from agentnexus.storage.timeline import TimelineStore

    events = []
    registry = SubagentRegistry()

    def emit(entry, kind, **fields):
        registry.update(entry, **{key: value for key, value in fields.items()
                                 if key in {"status", "summary", "error", "steps_used"}})
        events.append((kind, fields))

    context = SubagentRunContext(SimpleNamespace(_subagents=registry, _subagent_emit=emit), "s", "parent")

    class EventEngine:
        event_handler = None

        def __init__(self, tools):
            self.tools = tools

        def run(self, question, *, memory_manager=None):
            assert get_module_host() is host
            self.event_handler(AgentSignal("TOOL_START", {"name": "selected_child_tool"}, step_id=1))
            receipt = self.tools.get_tool("subagent_report")("selected progress", "progress")
            assert receipt["delivery"] == "queued"
            return "selected collaboration result"

    host = ModuleHost(Settings(), factories=replace(ModuleFactories.disabled(), agent=lambda ctx: EventEngine(ctx.tools)))
    unrelated = ModuleHost(Settings(), factories=ModuleFactories.disabled())
    executor = ToolRegistry()
    store = TimelineStore(str(tmp_path / "collaboration.db"))
    pool = ThreadPoolExecutor(1)
    run = CollaborationRun(context, store, pool, lambda: False)
    context.collaboration = run
    executor.subagent_bridge.set_context(context)
    try:
        register_all_tools(executor, non_interactive=True, modules=host, include_tools={"subagent_start"})
        with unrelated.activate():
            started = json.loads(executor.get_tool("subagent_start")("inspect files", allowed_tools=["file_read"]))
            assert get_module_host() is unrelated
        with run.condition:
            assert run.condition.wait_for(lambda: not run.active, timeout=3)
        child = run.children[started["subagent_id"]]
        assert child.entry.status == "completed"
        assert child.entry.summary == "selected collaboration result"
        assert [message["content"] for message in store.list_collaboration_messages("s")] == [
            "selected progress", "selected collaboration result",
        ]
        assert any(kind == "tool_call" and fields["tool_name"] == "selected_child_tool"
                   for kind, fields in events)
    finally:
        run.close("finished")
        pool.shutdown()
        executor.close()
        store._conn.close()
        host.close()
        unrelated.close()
