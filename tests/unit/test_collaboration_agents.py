import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.services.chat import ChatService
from agentnexus.services.collaboration import CollaborationRun
from agentnexus.services.subagents import SubagentRunContext
from agentnexus.storage.timeline import TimelineStore
from agentnexus.tools.providers.base import ToolProviderContext
from agentnexus.tools.providers.subagent_provider import SubagentToolProvider
from agentnexus.tools.registry import ToolRegistry


def model():
    llm = MagicMock()
    llm.model = "test/model"
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 1, "output_tokens": 1}
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_parallel_tool_calls = False
    return llm


def test_real_agents_exchange_question_reply_progress_and_instruction(tmp_path, monkeypatch):
    store = TimelineStore(str(tmp_path / "timeline.db"))
    monkeypatch.setattr("agentnexus.services.chat.get_timeline_store", lambda: store)
    monkeypatch.setattr("agentnexus.storage.timeline.get_timeline_store", lambda: store)
    registry = ToolRegistry()
    parent = model()
    received_instruction = threading.Event()
    service = ChatService(agent_factory=lambda sid: None, memory_factory_builder=lambda sid: lambda: None)
    context = SubagentRunContext(service, "s", "p")
    pool = ThreadPoolExecutor(2)
    run = CollaborationRun(context, store, pool, lambda: False)
    context.collaboration = run
    registry.subagent_bridge.set_context(context)
    SubagentToolProvider().register(registry, ToolProviderContext(llm_client=parent, non_interactive=True))
    child_models = []

    def clone(_parent):
        llm = model()
        child_models.append(llm)
        calls = []

        def think(**kwargs):
            calls.append(kwargs["messages"])
            text = json.dumps(kwargs["messages"], ensure_ascii=False)
            if len(calls) == 1:
                tool = "subagent_ask" if "question task" in text else "subagent_report"
                llm.last_tool_calls = [{"id": "child_call", "name": tool, "arguments": {"content": "Which format?" if tool == "subagent_ask" else "Files checked", "request_id": tool}}]
                return "Working"
            llm.last_tool_calls = []
            if "question task" in text:
                assert "Markdown" in text
                return "Question answered"
            if "Check tests" not in text:
                assert received_instruction.wait(3)
                return "Tentative result"
            return "Tests checked"

        llm.think.side_effect = think
        return llm

    monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", clone)
    parent_rounds = []

    def think(**kwargs):
        messages = kwargs["messages"]
        parent_rounds.append([dict(m) for m in messages])
        rows = store.list_collaboration_messages("s")
        questions = [m for m in rows if m["kind"] == "question" and m["question_state"] == "open"]
        children = list(run.children.values())
        if not children:
            parent.last_tool_calls = [{"id": f"start{i}", "name": "subagent_start", "arguments": {"task": task, "name": name}} for i, (task, name) in enumerate([("question task", "asker"), ("work task", "worker")])]
        elif questions:
            asker = next(c for c in children if c.entry.name == "asker")
            worker = next(c for c in children if c.entry.name == "worker")
            parent.last_tool_calls = [
                {"id": "reply", "name": "subagent_send", "arguments": {"subagent_id": asker.entry.subagent_id, "content": "Markdown", "question_id": questions[0]["id"], "request_id": "answer"}},
                {"id": "instruction", "name": "subagent_send", "arguments": {"subagent_id": worker.entry.subagent_id, "content": "Check tests", "request_id": "extra"}},
            ]
        elif any(m["kind"] == "instruction" for m in rows):
            received_instruction.set()
            parent.last_tool_calls = [{"id": f"wait{len(parent_rounds)}", "name": "subagent_wait", "arguments": {"timeout": 2}}] if run.active else []
        else:
            parent.last_tool_calls = [{"id": "wait", "name": "subagent_wait", "arguments": {"timeout": 2}}]
        return "All tasks complete" if not parent.last_tool_calls else "Coordinate"

    parent.think.side_effect = think
    agent = ReActAgent(parent, registry, max_steps=12, output=lambda *a, **kw: None)
    agent.set_collaboration(run.parent)
    try:
        result = agent.run("Coordinate the two tasks")
        assert result.answer == "All tasks complete"
        assert not run.active
        assert {c.entry.status for c in run.children.values()} == {"completed"}
        records = store.list_collaboration_messages("s")
        assert {m["kind"] for m in records} == {"question", "reply", "instruction", "progress", "result"}
        assert all(m["delivery"] == "applied" for m in records)
        assert next(m for m in records if m["kind"] == "question")["question_state"] == "answered"
        assert any("Question answered" in json.dumps(m) and "Tests checked" in json.dumps(m) for m in parent_rounds)
        batches = [(snapshot, index, message["tool_calls"]) for snapshot in parent_rounds for index, message in enumerate(snapshot) if message.get("tool_calls")]
        assert any(len(calls) == 2 for _, _, calls in batches)
        for snapshot, index, calls in batches:
            received = {message.get("tool_call_id") for message in snapshot[index + 1:] if message["role"] == "tool"}
            assert {call["id"] for call in calls} <= received
    finally:
        received_instruction.set()
        run.close("test_finished")
        pool.shutdown()
        registry.close()
