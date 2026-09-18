"""Behavioral tests for Tier-0 user-interaction hook events (M2).

Covers:
- USER_PROMPT_SUBMIT: prompt rewrite reaches the LLM; abort refuses the run
- AGENT_STOP: veto re-enters the LLM loop; consecutive vetoes capped at 2
- PERMISSION_REQUEST: hook deny blocks without prompting; allow ignored by
  default; allow honored only with hitl_hooks_may_approve; decision recorded
  in the registry audit log
- NOTIFICATION: fires alongside HITL requests
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnexus.core.config import get_settings
from agentnexus.core.hooks import HookType, get_hook_manager
from agentnexus.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def restore_hooks():
    """Remove only the hooks this test registered (keep framework hooks)."""
    mgr = get_hook_manager()
    before = {h["name"] for h in mgr.list_hooks()}
    yield
    for meta in mgr.list_hooks():
        if meta["name"] not in before:
            mgr.unregister(meta["name"])


def make_agent(llm, agent_id="react_agent"):
    from agentnexus.agents.re_act_agent import ReActAgent

    return ReActAgent(llm, ToolRegistry(), conversation_mode=False, agent_id=agent_id)


def native_llm(*answers: str) -> MagicMock:
    llm = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = False
    # Explicit attribute values: MagicMock auto-attrs are truthy and would
    # misroute the FSM (e.g. last_truncated truthy → truncation retry loop).
    llm.last_error = None
    llm.last_reasoning_content = None
    llm.last_tool_calls = []
    llm.last_truncated = False
    llm.think.side_effect = list(answers)
    return llm


class TestUserPromptSubmit:
    def test_hook_rewrites_prompt_before_llm(self):
        llm = native_llm('{"answer": "done"}')
        agent = make_agent(llm)

        def rewrite(ctx):
            ctx.payload["prompt"] = ctx.payload["prompt"].replace("bad", "good")

        get_hook_manager().register(HookType.USER_PROMPT_SUBMIT, rewrite, name="rw")
        result = agent.run("something bad")
        assert result.answer == "done"
        user_msg = llm.think.call_args.kwargs["messages"][-1]["content"]
        assert "something good" in user_msg

    def test_abort_refuses_run_without_llm_call(self):
        llm = native_llm('{"answer": "unreached"}')
        agent = make_agent(llm)

        def blocker(ctx):
            ctx.abort("API key detected in prompt", code="POLICY_VIOLATION")

        get_hook_manager().register(HookType.USER_PROMPT_SUBMIT, blocker, name="blk")
        result = agent.run("here is an API key")
        assert result.answer.startswith("[已拒绝]")
        assert "API key" in result.answer
        llm.think.assert_not_called()

    def test_subagent_runs_do_not_fire_user_prompt_submit(self):
        llm = native_llm('{"answer": "sub done"}')
        agent = make_agent(llm, agent_id="subagent_explorer")
        fired: list[str] = []

        def observer(ctx):
            fired.append(ctx.payload.get("prompt", ""))

        get_hook_manager().register(HookType.USER_PROMPT_SUBMIT, observer, name="obs")
        agent.run("internal task text")
        assert fired == []


class TestAgentStop:
    def test_veto_reenters_loop_and_second_answer_wins(self):
        llm = native_llm('{"answer": "first"}', '{"answer": "second"}')
        agent = make_agent(llm)
        vetoes: list[str] = []

        def veto_once(ctx):
            if not vetoes:  # veto only the first attempt
                vetoes.append(ctx.payload["answer"])
                ctx.abort("缺少测试通过的证据")

        get_hook_manager().register(HookType.AGENT_STOP, veto_once, name="v1")
        result = agent.run("q")
        assert result.answer == "second"
        assert llm.think.call_count == 2
        second_call_msgs = llm.think.call_args_list[1].kwargs["messages"]
        assert any("agent_stop" in str(m.get("content", "")) for m in second_call_msgs)

    def test_vetoes_capped_at_two(self):
        answers = ['{"answer": "a1"}', '{"answer": "a2"}', '{"answer": "a3"}']
        llm = native_llm(*answers)
        agent = make_agent(llm)

        def always_veto(ctx):
            ctx.abort("never satisfied")

        get_hook_manager().register(HookType.AGENT_STOP, always_veto, name="v2")
        result = agent.run("q")
        # two vetoes consumed, third emit proceeds
        assert result.answer == "a3"
        assert llm.think.call_count == 3


class TestPermissionRequest:
    def _registry_with_hitl_tool(self) -> tuple[ToolRegistry, MagicMock]:
        registry = ToolRegistry()
        registry.register_tool(
            "dangerous_op", "destructive", lambda: "ok",
            risk_level="high", require_hitl=True,
        )
        approver = MagicMock(return_value=True)
        return registry, approver

    def test_hook_deny_blocks_without_prompting(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "hitl_hooks_may_approve", False, raising=False)
        registry, approver = self._registry_with_hitl_tool()

        def deny(ctx):
            ctx.abort("dangerous_op 不在白名单", code="PERMISSION_DENIED")

        get_hook_manager().register(HookType.PERMISSION_REQUEST, deny, name="deny")
        result = registry.invoke("dangerous_op", {}, caller="test", hitl_approver=approver)
        assert "hook blocked" in result
        assert "不在白名单" in result
        approver.assert_not_called()
        entry = registry.get_audit_log()[-1]
        assert entry.hitl_triggered and entry.hitl_decision.startswith("hook_denied")

    def test_hook_allow_ignored_by_default(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "hitl_hooks_may_approve", False, raising=False)
        registry, approver = self._registry_with_hitl_tool()

        def allow(ctx):
            ctx.payload["decision"] = "allow"

        get_hook_manager().register(HookType.PERMISSION_REQUEST, allow, name="allow")
        result = registry.invoke("dangerous_op", {}, caller="test", hitl_approver=approver)
        assert result == "ok"
        approver.assert_called_once()  # human prompt still required
        assert registry.get_audit_log()[-1].hitl_decision == "user_allowed"

    def test_hook_allow_honored_with_opt_in(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "hitl_hooks_may_approve", True, raising=False)
        registry, approver = self._registry_with_hitl_tool()

        def allow(ctx):
            ctx.payload["decision"] = "allow"

        notifications: list[dict] = []
        get_hook_manager().register(HookType.PERMISSION_REQUEST, allow, name="allow2")
        get_hook_manager().register(
            HookType.NOTIFICATION,
            lambda ctx: notifications.append(dict(ctx.payload)),
            name="notify",
        )
        result = registry.invoke("dangerous_op", {}, caller="test", hitl_approver=approver)
        assert result == "ok"
        approver.assert_not_called()
        entry = registry.get_audit_log()[-1]
        assert entry.hitl_decision == "hook_allowed"
        assert any(n.get("kind") == "hitl_request" for n in notifications)

    def test_user_denial_recorded(self):
        registry, _ = self._registry_with_hitl_tool()
        result = registry.invoke(
            "dangerous_op", {}, caller="test", hitl_approver=lambda summary: False,
        )
        assert "用户取消" in result
        assert registry.get_audit_log()[-1].hitl_decision == "user_denied"
