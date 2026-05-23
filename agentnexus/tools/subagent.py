"""Subagent delegation tool built on top of ReActAgent."""

from __future__ import annotations

import json
from typing import Iterable

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.llm import AgentLLM
from agentnexus.tools.tool_executor import ToolExecutor

_SAFE_SUBAGENT_TOOLS = {
    "grep_search",
    "web_search",
    "kb_search",
    "file_read",
    "file_list",
    "memory_search",
}

_ROLE_TOOL_PRESETS = {
    "general": ["grep_search", "web_search", "kb_search", "file_read", "file_list"],
    "researcher": ["web_search", "kb_search", "file_read", "file_list", "grep_search"],
    "reader": ["file_read", "file_list", "grep_search"],
    "analyst": ["grep_search", "web_search", "kb_search", "file_read", "file_list", "memory_search"],
}

_ROLE_DESCRIPTIONS = {
    "general": "通用分析型子代理",
    "researcher": "信息检索与事实核查子代理",
    "reader": "代码与文档阅读子代理",
    "analyst": "分析归纳子代理",
}


def _clone_llm(parent_llm: AgentLLM | None) -> AgentLLM:
    if parent_llm is None:
        return AgentLLM()
    return AgentLLM(
        model=parent_llm.model,
        apiKey=parent_llm.api_key,
        baseUrl=parent_llm.base_url,
        timeout=parent_llm.timeout,
    )


def _resolve_allowed_tools(role: str, allowed_tools: Iterable[str] | None) -> list[str]:
    if allowed_tools:
        resolved = [name for name in allowed_tools if name in _SAFE_SUBAGENT_TOOLS]
        return resolved
    preset = _ROLE_TOOL_PRESETS.get(role, _ROLE_TOOL_PRESETS["general"])
    return [name for name in preset if name in _SAFE_SUBAGENT_TOOLS]


def _build_subagent_prompt(task: str, role: str) -> str:
    role_desc = _ROLE_DESCRIPTIONS.get(role, role or _ROLE_DESCRIPTIONS["general"])
    return (
        f"你是由父代理委派的子代理，当前角色：{role_desc}。\n"
        "只完成当前子任务，不要假装你拥有未执行过的观察。"
        "如果信息不足，明确指出缺口。完成后直接给出结论。\n\n"
        f"子任务：{task}"
    )


def _register_child_tools(executor: ToolExecutor, parent_llm: AgentLLM | None,
                          non_interactive: bool, include_tools: list[str]) -> None:
    from agentnexus.tools import register_all_tools

    register_all_tools(
        executor,
        non_interactive=non_interactive,
        llm_client=parent_llm,
        include_tools=set(include_tools),
        enable_subagent=False,
    )



def make_subagent_run(parent_llm: AgentLLM | None = None, non_interactive: bool = False):
    def subagent_run(task: str, role: str = "general",
                     allowed_tools: list[str] | None = None,
                     max_steps: int = 4) -> str:
        tool_names = _resolve_allowed_tools(role, allowed_tools)
        if not tool_names:
            return json.dumps({
                "status": "error",
                "role": role,
                "answer": "",
                "summary": "没有可用的安全工具可分配给子代理。",
                "steps_used": 0,
                "allowed_tools": [],
            }, ensure_ascii=False)

        child_llm = _clone_llm(parent_llm)
        child_executor = ToolExecutor()
        _register_child_tools(child_executor, parent_llm, non_interactive, tool_names)
        child_agent = ReActAgent(
            child_llm,
            child_executor,
            max_steps=max(1, min(int(max_steps), 8)),
            output=lambda *_args, **_kwargs: None,
            conversation_mode=False,
            agent_id=f"subagent_{role}",
        )

        try:
            result = child_agent.run(_build_subagent_prompt(task, role), memory_manager=None)
            answer = (result.answer or "").strip()
            payload = {
                "status": "ok" if answer else "empty",
                "role": role,
                "answer": answer,
                "summary": answer[:500],
                "steps_used": len(result.steps),
                "allowed_tools": tool_names,
            }
        except Exception as exc:
            payload = {
                "status": "error",
                "role": role,
                "answer": "",
                "summary": f"子代理执行失败: {exc}",
                "steps_used": 0,
                "allowed_tools": tool_names,
            }

        return json.dumps(payload, ensure_ascii=False)

    return subagent_run
