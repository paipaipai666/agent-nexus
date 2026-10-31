"""Model control envelopes stay inside the agent execution boundary."""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker
from agentnexus.tools.registry import ToolMeta, ToolRegistry


@dataclass
class ScriptedModel:
    responses: list[str]
    capabilities: ModelCapabilities
    model: str = "protocol-fixture"
    base_url: str = ""
    session_tracker: SessionCapabilityTracker = field(default_factory=SessionCapabilityTracker)
    last_error: str = ""
    last_unsupported_capability: str | None = None
    last_reasoning_content: str = ""
    last_tool_calls: list = field(default_factory=list)
    last_truncated: bool = False
    last_usage: dict = field(default_factory=dict)
    requests: list = field(default_factory=list)

    def think(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        callback = kwargs.get("on_token")
        if callback:
            for token in response:
                callback(token)
        return response


def capabilities_for(protocol):
    return ModelCapabilities(supports_tool_calling=protocol == "native",
                             supports_json_mode=protocol == "json",
                             from_default_fallback=False)


def make_agent(model):
    registry = ToolRegistry()
    inspected = []

    def inspect_source(path):
        inspected.append(path)
        return "source content"

    registry.register(ToolMeta(name="inspect_source", description="Read a source",
                              param_schema={"type": "object", "properties": {"path": {"type": "string"}}}),
                      inspect_source)
    agent = ReActAgent(model, registry, max_steps=5, output=lambda text: None)
    events = []
    agent.event_handler = lambda event, *_states: events.append(event) if event is not None else None
    return agent, inspected, events


@pytest.mark.parametrize("protocol", ["native", "json", "prompt"])
def test_tool_envelope_is_not_published_as_visible_text(protocol):
    model = ScriptedModel([
        '{"thought":"Reading source","tool":"inspect_source","params":{"path":"script.py"}}',
        'Project summary' if protocol == "native" else '{"answer":"Project summary"}',
    ], capabilities_for(protocol))
    agent, inspected, events = make_agent(model)
    result = agent.run("Describe this project")
    visible = "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN")
    thoughts = [event.payload["thought"] for event in events if event.type.name == "TOOLS_REQUESTED"]
    assert result.answer == "Project summary"
    assert inspected == ["script.py"]
    assert visible == "Project summary"
    assert thoughts == ["Reading source"]


@pytest.mark.parametrize("protocol", ["native", "json", "prompt"])
def test_fullwidth_protocol_is_decoded_before_publication(protocol):
    model = ScriptedModel([
        '｛“thought”：“Reading source”，“tool”：“inspect_source”，“params”：｛“path”：“script.py”｝｝',
        '｛“answer”：“Project summary”｝',
    ], capabilities_for(protocol))
    agent, inspected, events = make_agent(model)
    result = agent.run("Describe this project")
    visible = "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN")
    assert inspected == ["script.py"]
    assert result.answer == "Project summary"
    assert visible == "Project summary"


def test_decoded_answer_preserves_business_json_in_a_code_block():
    import json

    expected = 'Example:\n```json\n{"tool":"example","params":{"path":"source.py"}}\n```'
    model = ScriptedModel([json.dumps({"answer": expected})], capabilities_for("native"))
    agent, _, events = make_agent(model)
    assert agent.run("Show an example").answer == expected
    assert "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN") == expected


@pytest.mark.parametrize("text", ['{"count":3,"name":"model"}', 'Here is a code sample:\n```python\nvalues = {"a": 1}\n```'])
def test_native_business_json_and_code_remain_visible(text):
    agent, _, events = make_agent(ScriptedModel([text], capabilities_for("native")))
    result = agent.run("Return this data")
    visible = "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN")
    assert result.answer == text
    assert visible == text


def test_explicit_protocol_rejections_degrade_in_order():
    class RejectingModel(ScriptedModel):
        def think(self, **kwargs):
            self.requests.append(kwargs)
            if kwargs.get("tools"):
                self.last_error = "tools are unsupported"
                self.last_unsupported_capability = "tool_calling"
                return ""
            if kwargs.get("response_format"):
                self.last_error = "response_format is unsupported"
                self.last_unsupported_capability = "json_mode"
                return ""
            self.last_error = ""
            self.last_unsupported_capability = None
            return '{"answer":"Recovered by the final protocol"}'

    model = RejectingModel([], ModelCapabilities(supports_tool_calling=True, supports_json_mode=True))
    agent, _, events = make_agent(model)
    result = agent.run("Answer this")
    strategies = [event.payload["strategy"] for event in events if event.type.name == "LLM_CALL"]
    assert result.answer == "Recovered by the final protocol"
    assert strategies == ["NATIVE_TOOLS", "JSON_MODE", "PROMPT_JSON"]


def test_empty_native_response_does_not_disable_tool_calling():
    model = ScriptedModel(["", "Recovered on the same protocol"], capabilities_for("native"))
    agent, _, events = make_agent(model)
    result = agent.run("Answer this")
    strategies = [event.payload["strategy"] for event in events if event.type.name == "LLM_CALL"]
    assert result.answer == "Recovered on the same protocol"
    assert strategies == ["NATIVE_TOOLS", "NATIVE_TOOLS"]
    assert model.session_tracker.disabled_features == set()


@pytest.mark.parametrize("error", ["429 rate limit", "request timed out", "connection reset"])
def test_transport_errors_do_not_degrade_protocol(error):
    model = ScriptedModel([""], capabilities_for("native"), last_error=error)
    agent, _, events = make_agent(model)
    agent.run("Answer this")
    strategies = [event.payload["strategy"] for event in events if event.type.name == "LLM_CALL"]
    assert strategies == ["NATIVE_TOOLS"]
    assert model.session_tracker.disabled_features == set()


def test_native_tool_channel_and_duplicate_envelope_publish_only_human_content():
    class NativeToolModel(ScriptedModel):
        def think(self, **kwargs):
            self.last_tool_calls = ([{"name": "inspect_source", "arguments": {"path": "script.py"}}]
                                    if len(self.responses) == 2 else [])
            return super().think(**kwargs)

    model = NativeToolModel([
        '{"thought":"Reading source","tool":"inspect_source","params":{"path":"script.py"}}',
        "Project summary",
    ], capabilities_for("native"))
    agent, inspected, events = make_agent(model)
    result = agent.run("Describe this project")
    visible = "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN")
    assert result.answer == "Project summary"
    assert inspected == ["script.py"]
    assert visible == "Project summary"
    assert [event.payload["text"] for event in events if event.type.name == "TOOLS_REQUESTED"] == [""]


@pytest.mark.parametrize("protocol", ["json", "prompt"])
def test_exhausted_malformed_tool_envelope_is_not_a_final_answer(protocol):
    broken = '{"thought":"Reading source","tool":"inspect_source","params":'
    model = ScriptedModel([broken] * 5, capabilities_for(protocol))
    agent, inspected, events = make_agent(model)
    result = agent.run("Describe this project")
    visible = "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN")
    assert "\"tool\"" not in (result.answer or "")
    assert "\"tool\"" not in visible
    assert inspected == []
    assert model.session_tracker.disabled_features == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(("protocol", "malformed"), [
    ("native", False), ("json", False), ("prompt", False), ("json", True), ("prompt", True),
])
async def test_tui_live_and_restored_history_keep_control_json_out_of_messages(tmp_path, protocol, malformed):
    from agentnexus.core.config import get_settings
    from agentnexus.memory.manager import MemoryManager
    from agentnexus.memory.short_term import ShortTermMemory
    from agentnexus.memory.versioned import ConversationVersionManager
    from agentnexus.services.chat import ChatService, SessionHandle
    from agentnexus.tui.app import AgentNexusTUI
    from agentnexus.tui.widgets.input_bar import Composer
    from agentnexus.tui.widgets.message import ChatMessage, ReasoningBlock, ToolCall

    get_settings().skill_auto_route = False
    responses = [
        '{"thought":"Reading source","tool":"inspect_source","params":{"path":"script.py"}}',
        "Project summary" if protocol == "native" else '{"answer":"Project summary"}',
    ]
    if malformed:
        responses = ['{"thought":"Reading source","tool":"inspect_source","params":'] * 5
    expected_answer = "模型未返回可用的最终答案，请重试。" if malformed else "Project summary"
    model = ScriptedModel(responses, capabilities_for(protocol))
    agent, inspected, _ = make_agent(model)
    agent.conversation_mode = True
    sid = "protocol_tui"
    memory = MemoryManager(sid, llm=model, short_term=ShortTermMemory(), long_term=None)
    version = ConversationVersionManager(sid, get_settings().memory_db_path, workspace_path=str(tmp_path))
    chat = ChatService(lambda _sid: agent, lambda _sid: lambda: memory, version_manager=version)
    chat.attach_session(SessionHandle(sid, workspace=str(tmp_path)), agent=agent, memory=memory, version=version)
    runtime = SimpleNamespace(agent=agent, memory_manager=memory, version_manager=version, chat=chat,
                              session_id=sid, mcp_manager=None, skill=None, capability_runtime=None, llm=model)

    app = AgentNexusTUI(runtime=runtime)
    async with app.run_test(size=(100, 30)) as pilot:
        app.screen.query_one("#chat-input", Composer).value = "Describe this project"
        await pilot.press("enter")
        for _ in range(150):
            await pilot.pause(0.02)
            if chat.get_session_recovery(sid) and not app.screen._running_sessions:
                break
        assert inspected == ([] if malformed else ["script.py"])
        assert [widget.content for widget in app.screen.query(ChatMessage) if "assistant" in widget.classes] == [expected_answer]
        assert [widget.content for widget in app.screen.query(ReasoningBlock)] == ([] if malformed else ["Reading source"])
        assert [widget.tool_name for widget in app.screen.query(ToolCall)] == ([] if malformed else ["inspect_source"])
        recovery = chat.get_session_recovery(sid)
        assert recovery["answer"] == expected_answer
        assert recovery["persisted"] is True

    restored = AgentNexusTUI(runtime=runtime)
    async with restored.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        content = [widget.content for widget in restored.screen.query(ChatMessage)]
        assert expected_answer in content
        assert all('"tool"' not in text and '"answer"' not in text for text in content)


def test_truncated_protocol_answer_is_joined_without_control_fields():
    class TruncatedModel(ScriptedModel):
        def think(self, **kwargs):
            self.last_truncated = len(self.responses) == 2
            return super().think(**kwargs)

    model = TruncatedModel(['{"answer":"First part ', '{"answer":"Second part"}'], capabilities_for("json"))
    agent, _, events = make_agent(model)
    result = agent.run("Write a report")
    assert result.answer == "First part Second part"
    assert "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN") == "First part Second part"


def test_native_malformed_control_response_retries_without_exposing_json():
    model = ScriptedModel(['{"thought":"Read source","tool":"inspect_source","params":', "Recovered answer"],
                          capabilities_for("native"))
    agent, _, events = make_agent(model)
    result = agent.run("Describe this project")
    assert result.answer == "Recovered answer"
    assert "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN") == "Recovered answer"
    assert model.session_tracker.disabled_features == set()


def test_truncated_native_code_is_published_without_losing_buffered_text():
    class TruncatedModel(ScriptedModel):
        def think(self, **kwargs):
            self.last_truncated = len(self.responses) == 2
            return super().think(**kwargs)

    expected = 'Example:\n```json\n{"count":3}\n```'
    model = TruncatedModel(['Example:\n```json\n{"count":3', '}\n```'], capabilities_for("native"))
    agent, _, events = make_agent(model)
    result = agent.run("Show example code")
    assert result.answer == expected
    assert "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN") == expected


def test_ordinary_native_text_is_published_before_model_response_finishes():
    events = []

    class StreamingModel(ScriptedModel):
        def think(self, **kwargs):
            response = super().think(**kwargs)
            assert "".join(event.payload["token"] for event in events if event.type.name == "STREAM_TOKEN") == "Live answer"
            return response

    agent, _, events = make_agent(StreamingModel(["Live answer"], capabilities_for("native")))
    assert agent.run("Answer this").answer == "Live answer"
