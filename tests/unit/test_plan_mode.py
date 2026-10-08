"""Tests for plan mode: manager, tool gating, exit review, MCP readOnlyHint, routes.

Regression coverage mirrors known failure modes of other agents' plan modes:
rejected exit corrupting the gate, subagent tool-set bypass, prompt-only
enforcement, error text being read as approval, untruncated plan delivery.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentnexus.agents.plan_mode import (
    EXIT_PLAN_MODE_TOOL,
    PLAN_REJECTED_ANSWER,
    PLAN_REVIEW_MARKER,
    PlanModeBinding,
    PlanModeManager,
)
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.confirm_bridge import ConfirmBridge
from agentnexus.tools.errors import ToolError
from agentnexus.tools.registry import ToolMeta, ToolRegistry

EXPECTED_READ_ONLY = {
    "file_read", "file_list",
    "grep_search", "web_search", "kb_search", "web_fetch",
    "history_search", "memory_search", "memory_project_status",
    "todo_list",
    "express_reaction",
}


# ── helpers ──────────────────────────────────────────────────────


def _meta(name: str, **kwargs) -> ToolMeta:
    return ToolMeta(
        name=name,
        description=f"stub {name}",
        param_schema={"type": "object", "properties": {}},
        **kwargs,
    )


def _make_agent(registry: ToolRegistry, confirm=None) -> ReActAgent:
    agent = ReActAgent(
        llm_client=MagicMock(),
        tool_executor=registry,
        output=lambda _msg: None,
        confirm_fn=confirm or (lambda _s: False),
    )
    return agent


@pytest.fixture()
def gate():
    """Registry with stub tools + agent bound to a fresh manager."""
    reg = ToolRegistry()
    calls: list[tuple[str, dict]] = []

    def _record(name: str):
        def _fn(**kwargs):
            calls.append((name, kwargs))
            return f"{name} executed"
        return _fn

    reg.register(_meta("read_tool", read_only=True), _record("read_tool"))
    reg.register(_meta("write_tool"), _record("write_tool"))
    reg.register(_meta(EXIT_PLAN_MODE_TOOL), _record(EXIT_PLAN_MODE_TOOL))
    reg.register(_meta("subagent_run"), _record("subagent_run"))

    manager = PlanModeManager()
    agent = _make_agent(reg)
    agent.set_plan_mode(PlanModeBinding(manager, "s1"))
    return SimpleNamespace(reg=reg, manager=manager, agent=agent, calls=calls)


# ── PlanModeManager ──────────────────────────────────────────────


class TestPlanModeManager:
    def test_unknown_session_is_inactive(self):
        m = PlanModeManager()
        assert m.is_active("nope") is False

    def test_enable_disable_cycle(self):
        m = PlanModeManager()
        m.enable("s1")
        assert m.is_active("s1") is True
        m.disable("s1")
        assert m.is_active("s1") is False

    def test_clear_removes_state(self):
        m = PlanModeManager()
        m.enable("s1")
        m.clear("s1")
        assert m.is_active("s1") is False

    def test_disable_is_idempotent(self):
        m = PlanModeManager()
        m.disable("never-enabled")  # must not raise
        m.enable("s1")
        m.disable("s1")
        m.disable("s1")  # double-disable: still off, no error
        assert m.is_active("s1") is False

    def test_sessions_are_isolated(self):
        m = PlanModeManager()
        m.enable("s1")
        assert m.is_active("s2") is False
        m.disable("s1")
        assert m.is_active("s1") is False
        m.enable("s2")
        assert m.is_active("s2") is True


# ── read_only metadata ───────────────────────────────────────────


class TestReadOnlyMetadata:
    def test_default_is_false(self):
        assert ToolMeta(name="t", description="d", param_schema={}).read_only is False

    def test_register_tool_accepts_read_only(self):
        reg = ToolRegistry()
        reg.register(
            ToolMeta(
                name="r",
                description="d",
                param_schema={"type": "object", "properties": {}},
                read_only=True,
            ),
            lambda: "ok",
        )
        assert reg.get_meta("r").read_only is True

    def test_annotation_set_is_exact(self):
        """Adding a tool without annotating read_only must fail this guard."""
        from agentnexus.tools.providers import default_tool_providers
        from agentnexus.tools.providers.base import ToolProviderContext

        actual: set[str] = set()
        skipped: list[str] = []
        for provider in default_tool_providers():
            name = provider.metadata().name
            ctx = ToolProviderContext(
                non_interactive=True,
                llm_client=MagicMock(),
                subagent_confirm=ConfirmBridge(),
                todo_list=MagicMock(),
            )
            reg = ToolRegistry()
            try:
                provider.register(reg, ctx.for_provider(name))
            except Exception as e:  # env-dependent optional deps (playwright…)
                skipped.append(f"{name}: {e}")
                continue
            actual |= {
                n for n in reg.list_tools()
                if reg.get_meta(n) is not None and reg.get_meta(n).read_only is True
            }
        if len(skipped) > len(default_tool_providers()) // 2:
            pytest.skip(f"most providers failed to import: {skipped}")
        assert actual == EXPECTED_READ_ONLY, (
            f"read_only annotation set drifted (skipped providers: {skipped}); "
            f"missing={sorted(EXPECTED_READ_ONLY - actual)}, extra={sorted(actual - EXPECTED_READ_ONLY)}"
        )


# ── gate behavior ────────────────────────────────────────────────


class TestPlanModeGate:
    def test_inactive_mode_runs_write_tool(self, gate):
        result = gate.agent._execute_tool("write_tool", {})
        assert result == "write_tool executed"
        assert gate.calls == [("write_tool", {})]

    def test_write_tool_blocked_when_active(self, gate):
        gate.manager.enable("s1")
        result = gate.agent._execute_tool("write_tool", {})
        assert isinstance(result, ToolError)
        assert result.error_code == "PLAN_MODE_BLOCKED"
        assert gate.calls == []

    def test_read_tool_allowed_when_active(self, gate):
        gate.manager.enable("s1")
        assert gate.agent._execute_tool("read_tool", {}) == "read_tool executed"
        assert gate.calls == [("read_tool", {})]

    def test_unknown_tool_blocked_when_active(self, gate):
        gate.manager.enable("s1")
        result = gate.agent._execute_tool("hallucinated_tool", {})
        assert isinstance(result, ToolError)
        assert result.error_code == "PLAN_MODE_BLOCKED"

    def test_exit_with_empty_plan_rejected(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(return_value=True)
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": ""})
        assert "不能为空" in result
        confirm.assert_not_called()
        assert gate.manager.is_active("s1") is True

    def test_exit_with_whitespace_plan_rejected(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(return_value=True)
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "  \n "})
        assert "不能为空" in result
        confirm.assert_not_called()
        assert gate.manager.is_active("s1") is True

    def test_exit_approved_persists_plan_and_unblocks(self, gate, tmp_path):
        from agentnexus.tools import workspace as ws_mod
        token = ws_mod.current_workspace.set(str(tmp_path))
        try:
            gate.manager.enable("s1")
            confirm = MagicMock(return_value=True)
            agent = _make_agent(gate.reg, confirm=confirm)
            agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
            result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "# 计划\n做 A"})
            assert "已退出" in result
            assert gate.manager.is_active("s1") is False
            plans = list((tmp_path / ".agentnexus" / "plans").glob("plan-*.md"))
            assert len(plans) == 1 and "# 计划" in plans[0].read_text(encoding="utf-8")
            assert str(plans[0]) in result
            # writes now pass through
            assert agent._execute_tool("write_tool", {}) == "write_tool executed"
        finally:
            ws_mod.current_workspace.reset(token)

    def test_exit_rejected_keeps_gate(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(return_value=False)
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "draft"})
        assert "拒绝" in result
        assert gate.manager.is_active("s1") is True
        blocked = agent._execute_tool("write_tool", {})
        assert isinstance(blocked, ToolError) and blocked.error_code == "PLAN_MODE_BLOCKED"

    def test_resubmit_after_rejection_succeeds(self, gate):
        gate.manager.enable("s1")
        answers = iter([False, True])
        confirm = MagicMock(side_effect=lambda _s: next(answers))
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "v1"})
        assert gate.manager.is_active("s1") is True
        result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "v2 revised"})
        assert "已退出" in result
        assert gate.manager.is_active("s1") is False

    def test_confirm_exception_treated_as_rejection(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(side_effect=RuntimeError("channel down"))
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "draft"})
        assert "拒绝" in result
        assert gate.manager.is_active("s1") is True

    def test_review_summary_marker_and_no_truncation(self, gate):
        gate.manager.enable("s1")
        seen: list[str] = []
        confirm = MagicMock(side_effect=lambda s: seen.append(s) or False)
        agent = _make_agent(gate.reg, confirm=confirm)
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        long_plan = "# 计划\n" + ("x" * 5000)
        agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": long_plan})
        (summary,) = seen
        assert summary.startswith(PLAN_REVIEW_MARKER)
        assert long_plan in summary

    def test_exit_when_inactive_never_mentions_approval(self, gate):
        result = gate.agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "x"})
        assert isinstance(result, str)
        for wording in ("批准", "已退出", "可以执行"):
            assert wording not in result
        assert gate.calls == []

    def test_subagent_gets_read_only_toolset(self, gate):
        gate.manager.enable("s1")
        gate.agent._execute_tool("subagent_run", {"task": "research", "role": "executor"})
        (name, kwargs), = gate.calls
        assert name == "subagent_run"
        assert kwargs["role"] == "executor"
        assert set(kwargs["allowed_tools"]) == {"read_tool"}
        assert "write_tool" not in kwargs["allowed_tools"]
        assert EXIT_PLAN_MODE_TOOL not in kwargs["allowed_tools"]
        assert "subagent_run" not in kwargs["allowed_tools"]

    def test_subagent_llm_whitelist_is_overridden(self, gate):
        gate.manager.enable("s1")
        gate.agent._execute_tool(
            "subagent_run", {"task": "t", "allowed_tools": ["write_tool"]}
        )
        (_name, kwargs), = gate.calls
        assert kwargs["allowed_tools"] == ["read_tool"]

    def test_mid_run_toggle_both_directions(self, gate):
        gate.manager.enable("s1")
        assert isinstance(gate.agent._execute_tool("write_tool", {}), ToolError)
        gate.manager.disable("s1")  # manual cancel mid-run, no re-binding
        assert gate.agent._execute_tool("write_tool", {}) == "write_tool executed"
        gate.manager.enable("s1")  # manual enable mid-run
        blocked = gate.agent._execute_tool("write_tool", {})
        assert isinstance(blocked, ToolError)

    def test_mock_registry_fails_closed(self, gate):
        """A duck-typed registry whose meta.read_only is a truthy Mock must not pass."""
        gate.manager.enable("s1")
        agent = _make_agent(MagicMock())  # get_meta() -> truthy MagicMock
        agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
        result = agent._execute_tool("write_tool", {})
        assert isinstance(result, ToolError)
        assert result.error_code == "PLAN_MODE_BLOCKED"

    def test_exit_tool_stays_in_sequential_lane(self, gate):
        assert gate.reg.get_meta(EXIT_PLAN_MODE_TOOL).concurrency_safe is False

    def test_plan_save_failure_does_not_block_exit(self, gate, tmp_path):
        from agentnexus.tools import workspace as ws_mod
        # .agentnexus exists as a FILE -> mkdir(parents=True) raises
        (tmp_path / ".agentnexus").write_text("blocker", encoding="utf-8")
        token = ws_mod.current_workspace.set(str(tmp_path))
        try:
            gate.manager.enable("s1")
            confirm = MagicMock(return_value=True)
            agent = _make_agent(gate.reg, confirm=confirm)
            agent.set_plan_mode(PlanModeBinding(gate.manager, "s1"))
            result = agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "draft"})
            assert "保存失败" in result and "已退出" in result
            assert gate.manager.is_active("s1") is False
        finally:
            ws_mod.current_workspace.reset(token)

    def test_prompt_block_tracks_live_state(self, gate):
        def _system_text() -> str:
            messages = gate.agent._build_messages("", "q", "", "")
            return "\n".join(m["content"] for m in messages if m["role"] == "system")

        gate.manager.enable("s1")
        assert "计划模式" in _system_text()
        assert "read_tool" in _system_text()
        gate.manager.disable("s1")
        assert "计划模式" not in _system_text()


class TestRejectedPlanPausesRun:
    """拒绝后回合必须立即结束并等待用户指示，而不是 agent 自行重新规划。"""

    def _make_llm(self):
        llm = MagicMock()
        llm.model = "test/test-model"
        llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
        llm.last_error = ""
        llm.last_truncated = False
        llm.last_tool_calls = []
        llm.last_reasoning_content = ""
        llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
        llm.capabilities = MagicMock()
        llm.capabilities.supports_thinking = False
        llm.capabilities.supports_tool_calling = True
        llm.capabilities.supports_json_mode = True
        llm.capabilities.supports_json_schema = False
        llm.capabilities.supports_parallel_tool_calls = False
        llm.capabilities.thinking_effort = "none"
        return llm

    def _make_agent(self, confirm):
        llm = self._make_llm()
        reg = ToolRegistry()
        reg.register(
            ToolMeta(
                name="read_tool",
                description="读",
                param_schema={"type": "object", "properties": {}},
                read_only=True,
            ),
            lambda **kw: "ok",
        )
        reg.register(_meta(EXIT_PLAN_MODE_TOOL), lambda **kw: "unreachable")
        manager = PlanModeManager()
        manager.enable("s1")
        agent = ReActAgent(llm, reg, max_steps=5, output=lambda _m: None, confirm_fn=confirm)
        agent.set_plan_mode(PlanModeBinding(manager, "s1"))
        return agent, llm, manager

    def test_rejection_ends_run_after_one_round(self):
        confirm = MagicMock(return_value=False)
        agent, llm, manager = self._make_agent(confirm)
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            llm.last_tool_calls = [
                {"name": EXIT_PLAN_MODE_TOOL, "arguments": {"plan": "# 计划"}, "id": "c1"}
            ]
            return "提交计划供审批"

        llm.think.side_effect = mock_think

        result = agent.run("做个方案")

        assert len(rounds) == 1, "拒绝后不得进入第二轮自动重规划"
        assert result.answer == PLAN_REJECTED_ANSWER
        assert manager.is_active("s1") is True  # 模式仍开，等待用户反馈后重提

    def test_approval_does_not_end_run(self):
        """对照：批准后模型继续执行（不触发暂停快路径）。"""
        confirm = MagicMock(return_value=True)
        agent, llm, manager = self._make_agent(confirm)
        rounds = []

        def mock_think(**kw):
            rounds.append(1)
            if len(rounds) == 1:
                llm.last_tool_calls = [
                    {"name": EXIT_PLAN_MODE_TOOL, "arguments": {"plan": "# 计划"}, "id": "c1"}
                ]
                return "提交计划供审批"
            llm.last_tool_calls = []
            return "最终答案：已按计划完成。"

        llm.think.side_effect = mock_think

        result = agent.run("做个方案")

        assert len(rounds) == 2
        assert result.answer == "最终答案：已按计划完成。"
        assert manager.is_active("s1") is False

    def test_empty_plan_does_not_set_rejection(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(return_value=False)
        agent = _make_agent(gate.reg, confirm=confirm)
        binding = PlanModeBinding(gate.manager, "s1")
        agent.set_plan_mode(binding)
        agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": ""})
        assert binding.take_rejection() is None

    def test_rejection_consumed_once(self, gate):
        gate.manager.enable("s1")
        confirm = MagicMock(return_value=False)
        agent = _make_agent(gate.reg, confirm=confirm)
        binding = PlanModeBinding(gate.manager, "s1")
        agent.set_plan_mode(binding)
        agent._execute_tool(EXIT_PLAN_MODE_TOOL, {"plan": "draft"})
        assert binding.take_rejection() == PLAN_REJECTED_ANSWER
        assert binding.take_rejection() is None


# ── MCP readOnlyHint ─────────────────────────────────────────────


class TestMCPReadOnly:
    def _config(self):
        from agentnexus.core.config import MCPServerConfig
        return MCPServerConfig(name="srv", command="srv")

    def _tool(self, read_only_hint):
        annotations = None
        if read_only_hint is not None:
            annotations = SimpleNamespace(readOnlyHint=read_only_hint)
        return SimpleNamespace(
            name="lookup",
            description="read something",
            inputSchema={"type": "object", "properties": {}},
            annotations=annotations,
        )

    def test_descriptor_reads_readonly_hint_true(self):
        from agentnexus.tools.mcp.descriptors import build_tool_descriptor
        d = build_tool_descriptor(self._config(), tool=self._tool(True), local_name="mcp_srv__lookup")
        assert d.read_only is True

    def test_descriptor_defaults_false_without_annotations(self):
        from agentnexus.tools.mcp.descriptors import build_tool_descriptor
        d = build_tool_descriptor(self._config(), tool=self._tool(None), local_name="mcp_srv__lookup")
        assert d.read_only is False

    def test_descriptor_reads_readonly_hint_false(self):
        from agentnexus.tools.mcp.descriptors import build_tool_descriptor
        d = build_tool_descriptor(self._config(), tool=self._tool(False), local_name="mcp_srv__lookup")
        assert d.read_only is False

    def test_weird_annotations_type_is_false_not_raise(self):
        from agentnexus.tools.mcp.descriptors import build_tool_descriptor
        tool = self._tool(None)
        tool.annotations = "not-an-object"
        d = build_tool_descriptor(self._config(), tool=tool, local_name="mcp_srv__lookup")
        assert d.read_only is False

    def test_register_tools_passes_read_only(self):
        from agentnexus.tools.mcp.adapter import MCPToolManager
        from agentnexus.tools.mcp.schema import MCPToolDescriptor

        mgr = MCPToolManager.__new__(MCPToolManager)
        mgr._callable_cache = {}
        mgr._tool_descriptors = {
            "mcp_srv__lookup": MCPToolDescriptor(
                local_name="mcp_srv__lookup",
                remote_name="lookup",
                server_name="srv",
                description="d",
                param_schema={"type": "object", "properties": {}},
                allowed_agents=["*"],
                risk_level="low",
                require_hitl=False,
                timeout_sec=30,
                rate_limit_per_min=0,
                read_only=True,
            )
        }
        reg = ToolRegistry()
        mgr.register_tools(reg)
        assert reg.get_meta("mcp_srv__lookup").read_only is True


# ── ChatService + routes ─────────────────────────────────────────


class TestChatServicePlanMode:
    def _chat(self):
        from agentnexus.services.chat import ChatService
        return ChatService(
            agent_factory=lambda _sid=None: MagicMock(),
            memory_factory_builder=lambda _sid: lambda: MagicMock(),
        )

    def test_unknown_session_raises(self):
        chat = self._chat()
        with pytest.raises(KeyError):
            chat.set_plan_mode("nope", True)

    def test_enable_and_query(self):
        chat = self._chat()
        s = chat.start_session()
        assert chat.is_plan_mode(s.id) is False
        assert chat.set_plan_mode(s.id, True) is True
        assert chat.is_plan_mode(s.id) is True

    def test_delete_session_clears_plan_mode(self):
        chat = self._chat()
        s = chat.start_session()
        chat.set_plan_mode(s.id, True)
        chat.delete_session(s.id)
        assert chat.is_plan_mode(s.id) is False


class TestPlanModeRoutes:
    @staticmethod
    def _client(**chat_kwargs):
        """TestClient whose lifespan registers the mock runtime for _get_runtime()."""
        from contextlib import contextmanager

        from fastapi.testclient import TestClient

        from agentnexus.server.app import create_app

        chat = MagicMock()
        for k, v in chat_kwargs.items():
            setattr(chat, k, v)
        runtime = MagicMock()
        runtime.chat = chat
        app = create_app(runtime)

        @contextmanager
        def _cm():
            with TestClient(app) as client:
                yield client, chat

        return _cm()

    def test_post_plan_mode(self):
        with self._client(set_plan_mode=MagicMock(return_value=True)) as (client, chat):
            resp = client.post("/api/session/s1/plan-mode", json={"enabled": True})
            assert resp.status_code == 200
            assert resp.json() == {"session_id": "s1", "plan_mode": True}
            chat.set_plan_mode.assert_called_once_with("s1", True)

    def test_get_session_includes_plan_mode(self):
        session = MagicMock(skill=None, profile=None, workspace=None)
        with self._client(
            get_session_snapshot=MagicMock(return_value={"session": session}),
            is_plan_mode=MagicMock(return_value=False),
        ) as (client, _chat):
            resp = client.get("/api/session/s1")
            assert resp.status_code == 200
            assert resp.json()["plan_mode"] is False

    def test_unknown_session_is_404(self):
        with self._client(set_plan_mode=MagicMock(side_effect=KeyError("nope"))) as (client, _chat):
            resp = client.post("/api/session/s1/plan-mode", json={"enabled": True})
            assert resp.status_code == 404
