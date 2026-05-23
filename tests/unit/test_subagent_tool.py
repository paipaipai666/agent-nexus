import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.prompts import load_prompt
from agentnexus.tools import register_all_tools
from agentnexus.tools.subagent import make_subagent_run
from agentnexus.tools.tool_executor import ToolExecutor


class TestAgentIdentityAndToolFiltering:
    def test_to_openai_tools_filters_by_agent(self):
        te = ToolExecutor()
        te.registerTool("restricted", "desc", lambda: 1, allowed_agents=["parent_agent"])

        parent_tools = te.registry.to_openai_tools("parent_agent")
        child_tools = te.registry.to_openai_tools("child_agent")

        assert [tool["function"]["name"] for tool in parent_tools] == ["restricted"]
        assert child_tools == []

    def test_execute_tool_uses_agent_id_as_caller(self, monkeypatch):
        mock_llm = MagicMock()
        executor = ToolExecutor()
        captured = {}

        def fake_invoke(name: str, params: dict, caller: str = "unknown", hitl_approver=None):
            captured["caller"] = caller
            return "ok"

        monkeypatch.setattr(executor.registry, "invoke", fake_invoke)
        agent = ReActAgent(mock_llm, executor, conversation_mode=False, agent_id="research_parent")

        result = agent._execute_tool("dummy", {})

        assert result == "ok"
        assert captured["caller"] == "research_parent"


class TestSubagentRegistration:
    def test_register_all_tools_can_register_subagent_tool(self):
        te = ToolExecutor()
        register_all_tools(te, non_interactive=True, llm_client=MagicMock(), include_tools={"subagent_run"})
        assert te.getTool("subagent_run") is not None

    def test_register_all_tools_can_disable_subagent_tool(self):
        te = ToolExecutor()
        register_all_tools(
            te,
            non_interactive=True,
            llm_client=MagicMock(),
            include_tools={"subagent_run"},
            enable_subagent=False,
        )
        assert te.getTool("subagent_run") is None


class TestSubagentPromptGuidance:
    def test_react_prompt_mentions_when_and_how_to_delegate(self):
        prompt = load_prompt("react")
        assert "仅当任务可以拆成一个边界清晰、输入充分、可独立完成的子任务时" in prompt
        assert "向 subagent_run 传递具体的 task、必要约束和合适的 role" in prompt
        assert "subagent 返回后，由你负责检查结果是否足够" in prompt


class TestSubagentRun:
    def test_subagent_run_returns_structured_result_and_filters_tools(self, monkeypatch):
        captured = {}

        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            captured["agent_id"] = self.agent_id
            captured["tools"] = set(self.tool_executor.registry.list_tools())
            captured["question"] = question
            return SimpleNamespace(answer="child answer", steps=[object(), object()])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(
            task="请阅读 README.md 并总结",
            role="reader",
            allowed_tools=["file_read", "python_execute", "subagent_run"],
            max_steps=3,
        ))

        assert payload["status"] == "ok"
        assert payload["answer"] == "child answer"
        assert payload["steps_used"] == 2
        assert payload["allowed_tools"] == ["file_read"]
        assert captured["agent_id"] == "subagent_reader"
        assert "file_read" in captured["tools"]
        assert "python_execute" not in captured["tools"]
        assert "subagent_run" not in captured["tools"]
        assert "子任务：请阅读 README.md 并总结" in captured["question"]
