import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.prompts import load_prompt
from agentnexus.tools import register_all_tools
from agentnexus.tools.confirm_bridge import ConfirmBridge
from agentnexus.tools.subagent import make_subagent_run
from agentnexus.tools.tool_executor import ToolExecutor


class FakeMCPManager:
    def list_subagent_tool_names(self):
        return ["mcp_demo__echo"]

    def register_tools(self, executor, include_tools=None):
        if include_tools is not None and "mcp_demo__echo" not in include_tools:
            return []
        executor.registerTool(
            "mcp_demo__echo",
            "[MCP:demo] echo",
            lambda message: message,
            param_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            allowed_agents=["react_agent", "subagent_explorer"],
            risk_level="medium",
            require_hitl=False,
            timeout_sec=30,
            rate_limit_per_min=5,
        )
        return ["mcp_demo__echo"]


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

        def fake_invoke(name: str, params: dict, caller: str = "unknown", hitl_approver=None, tool_policy=None):
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
    def test_react_prompt_mentions_explorer_and_executor_contract(self):
        prompt = load_prompt("react")
        assert "默认提供 Explorer 子代理" in prompt
        assert "当需要在受控环境中实际运行 Python 片段、验证执行结果时，可改用 executor" in prompt
        assert "reader/researcher/analyst 仅为兼容字段" in prompt


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
        assert payload["role"] == "explorer"
        assert payload["answer"] == "child answer"
        assert payload["steps_used"] == 2
        assert payload["allowed_tools"] == ["file_read"]
        assert captured["agent_id"] == "subagent_explorer"
        assert "file_read" in captured["tools"]
        assert "python_execute" not in captured["tools"]
        assert "subagent_run" not in captured["tools"]
        assert "子任务：请阅读 README.md 并总结" in captured["question"]

    def test_subagent_run_maps_legacy_role_to_explorer(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            captured["agent_id"] = self.agent_id
            captured["tools"] = set(self.tool_executor.registry.list_tools())
            return SimpleNamespace(answer="explorer answer", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(task="请总结 README", role="researcher", max_steps=3))

        assert payload["status"] == "ok"
        assert payload["role"] == "explorer"
        assert payload["answer"] == "explorer answer"
        assert "recovery" not in payload
        assert captured["agent_id"] == "subagent_explorer"
        assert "memory_search" in captured["tools"]

    def test_subagent_run_supports_executor_role_with_python_execute(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            captured["agent_id"] = self.agent_id
            captured["tools"] = set(self.tool_executor.registry.list_tools())
            captured["confirm"] = self._confirm
            return SimpleNamespace(answer="executor answer", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        bridge = ConfirmBridge()
        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=False, subagent_confirm=bridge)
        payload = json.loads(tool(task="请执行这段 Python 并验证输出", role="executor", max_steps=3))

        assert payload["status"] == "ok"
        assert payload["role"] == "executor"
        assert payload["answer"] == "executor answer"
        assert captured["agent_id"] == "subagent_executor"
        assert "python_execute" in captured["tools"]
        assert "web_search" not in captured["tools"]
        assert captured["confirm"] is bridge

    def test_subagent_run_falls_back_to_explorer_default_tools_when_requested_tools_are_unsafe(self, monkeypatch):
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())
        monkeypatch.setattr(
            "agentnexus.tools.subagent.ReActAgent.run",
            lambda self, question, memory_manager=None: SimpleNamespace(answer="fallback answer", steps=[]),
        )

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(
            task="请总结 README",
            role="reader",
            allowed_tools=["python_execute", "subagent_run"],
            max_steps=3,
        ))

        assert payload["status"] == "fallback"
        assert payload["role"] == "explorer"
        assert payload["answer"] == "fallback answer"
        assert payload["allowed_tools"] == [
            "grep_search", "web_search", "kb_search", "file_read", "file_list", "memory_search"
        ]
        assert payload["recovery"]["reason"] == "requested_tools_filtered"

    def test_subagent_run_retries_when_first_attempt_returns_empty_answer(self, monkeypatch):
        calls = []
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            calls.append((self.agent_id, question))
            if len(calls) == 1:
                return SimpleNamespace(answer="", steps=[])
            return SimpleNamespace(answer="retry answer", steps=[object()])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(task="请执行并验证", role="executor", max_steps=2))

        assert payload["status"] == "fallback"
        assert payload["role"] == "explorer"
        assert payload["answer"] == "retry answer"
        assert payload["recovery"]["attempted"] is True
        assert payload["recovery"]["attempts"] == 2
        assert calls[0][0] == "subagent_executor"
        assert calls[1][0] == "subagent_explorer"

    def test_subagent_run_salvages_step_content_after_failed_attempts(self, monkeypatch):
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            return SimpleNamespace(
                answer="",
                steps=[SimpleNamespace(content='{"answer"： "从步骤中提取"}', reasoning_content="")],
            )

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(parent_llm=MagicMock(), non_interactive=True)
        payload = json.loads(tool(task="请执行并验证", role="executor", max_steps=2))

        assert payload["status"] == "fallback"
        assert payload["role"] == "executor"
        assert payload["answer"] == "从步骤中提取"
        assert payload["recovery"]["attempted"] is True

    def test_subagent_run_accepts_explicit_safe_mcp_tool(self, monkeypatch):
        captured = {}
        monkeypatch.setattr("agentnexus.tools.subagent._clone_llm", lambda _parent: MagicMock())

        def fake_run(self, question, memory_manager=None):
            captured["tools"] = set(self.tool_executor.registry.list_tools())
            return SimpleNamespace(answer="mcp answer", steps=[])

        monkeypatch.setattr("agentnexus.tools.subagent.ReActAgent.run", fake_run)

        tool = make_subagent_run(
            parent_llm=MagicMock(),
            non_interactive=True,
            mcp_manager=FakeMCPManager(),
        )
        payload = json.loads(tool(
            task="请调用外部 MCP echo 工具",
            role="reader",
            allowed_tools=["mcp_demo__echo"],
            max_steps=3,
        ))

        assert payload["status"] == "ok"
        assert payload["allowed_tools"] == ["mcp_demo__echo"]
        assert "mcp_demo__echo" in captured["tools"]
