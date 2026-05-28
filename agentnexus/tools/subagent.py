"""Subagent delegation tool built on top of ReActAgent."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from agentnexus.tools.mcp_adapter import MCPToolManager

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.tracer import trace_manager
from agentnexus.tools.tool_executor import ToolExecutor

_SAFE_SUBAGENT_TOOLS = {
    "grep_search",
    "web_search",
    "kb_search",
    "file_read",
    "file_list",
    "memory_search",
    "python_execute",
}

_ROLE_TOOL_PRESETS = {
    "explorer": ["grep_search", "web_search", "kb_search", "file_read", "file_list", "memory_search"],
    "executor": ["python_execute", "file_read", "file_list", "grep_search"],
}

_ROLE_DESCRIPTIONS = {
    "explorer": "Explorer 子代理，适合阅读、检索、归纳和信息收集",
    "executor": "Executor 子代理，适合在受控环境中执行 Python 片段并验证结果",
}

_LEGACY_ROLE_ALIASES = {
    "explorer": "explorer",
    "general": "explorer",
    "reader": "explorer",
    "researcher": "explorer",
    "analyst": "explorer",
    "executor": "executor",
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



def _normalize_role(role: str | None) -> str:
    normalized = (role or "explorer").strip().lower()
    return _LEGACY_ROLE_ALIASES.get(normalized, "explorer")



def _resolve_allowed_tools(
    role: str,
    allowed_tools: Iterable[str] | None,
    mcp_manager: "MCPToolManager | None" = None,
) -> tuple[list[str], str | None]:
    preset = [
        name
        for name in _ROLE_TOOL_PRESETS.get(role, _ROLE_TOOL_PRESETS["explorer"])
        if name in _SAFE_SUBAGENT_TOOLS
    ]
    if allowed_tools is None:
        return preset, None

    allowed_pool = set(preset)
    if mcp_manager is not None:
        allowed_pool.update(mcp_manager.list_subagent_tool_names())

    requested = [name for name in allowed_tools if name in allowed_pool]
    if requested:
        return requested, None
    return preset, "requested_tools_filtered"



def _build_subagent_prompt(task: str, role: str, retry_reason: str | None = None) -> str:
    role_desc = _ROLE_DESCRIPTIONS.get(role, _ROLE_DESCRIPTIONS["explorer"])
    retry_block = ""
    if retry_reason:
        retry_block = (
            f"\n重试要求：上一次子代理执行未产出可用结论，原因：{retry_reason}。"
            "请更保守地使用已有工具和观察，优先给出清晰结论。\n"
        )
    return (
        f"你是由父代理委派的 {role_desc}。\n"
        "只完成当前子任务，不要假装你拥有未执行过的观察。"
        "如果信息不足，明确指出缺口。完成后直接给出结论。"
        f"{retry_block}\n"
        f"子任务：{task}"
    )



def _register_child_tools(executor: ToolExecutor, parent_llm: AgentLLM | None,
                          non_interactive: bool, include_tools: list[str],
                          subagent_confirm: Callable[[str], bool] | None = None,
                          mcp_manager: "MCPToolManager | None" = None) -> None:
    from agentnexus.tools import register_all_tools

    register_all_tools(
        executor,
        non_interactive=non_interactive,
        llm_client=parent_llm,
        include_tools=set(include_tools),
        enable_subagent=False,
        subagent_confirm=subagent_confirm,
        mcp_manager=mcp_manager,
    )



def _extract_step_summary(result) -> str:
    steps = getattr(result, "steps", []) or []
    for step in reversed(steps):
        for candidate in (getattr(step, "content", ""), getattr(step, "reasoning_content", "")):
            text = (candidate or "").strip()
            if not text:
                continue
            extracted = ReActAgent._extract_answer_from_text(text).strip()
            if extracted:
                return extracted[:1000]
    return ""



def _run_subagent_attempt(parent_llm: AgentLLM | None, non_interactive: bool,
                          task: str, role: str, tool_names: list[str], max_steps: int,
                          retry_reason: str | None = None,
                          subagent_confirm: Callable[[str], bool] | None = None,
                          mcp_manager: "MCPToolManager | None" = None) -> tuple[dict | None, Exception | None]:
    from agentnexus.core.hooks import HookType, get_hook_manager

    hook_mgr = get_hook_manager()
    hook_mgr.fire(HookType.BEFORE_SUBAGENT_RUN, {
        "task": task, "role": role, "tool_names": tool_names,
        "max_steps": max_steps, "retry_reason": retry_reason,
    })

    child_llm = _clone_llm(parent_llm)
    child_executor = ToolExecutor()
    _register_child_tools(
        child_executor,
        parent_llm,
        non_interactive,
        tool_names,
        subagent_confirm,
        mcp_manager,
    )
    child_agent = ReActAgent(
        child_llm,
        child_executor,
        max_steps=max(1, min(int(max_steps), 8)),
        output=lambda *_args, **_kwargs: None,
        confirm_fn=subagent_confirm,
        conversation_mode=False,
        agent_id=f"subagent_{role}",
    )

    try:
        with trace_manager.span("subagent_attempt", {
            "role": role,
            "tool_names": tool_names,
            "max_steps": max_steps,
            "retry_reason": retry_reason or "",
            "task_preview": task[:200],
        }) as span:
            result = child_agent.run(_build_subagent_prompt(task, role, retry_reason), memory_manager=None)
            answer = (result.answer or "").strip()
            salvaged = _extract_step_summary(result)
            span.output = {
                "answer": answer[:500],
                "salvaged": salvaged[:500],
                "steps_used": len(getattr(result, "steps", []) or []),
            }
            span.metadata = {
                "status": "ok",
                "agent_id": f"subagent_{role}",
            }
            return {
                "role": role,
                "tool_names": tool_names,
                "answer": answer,
                "salvaged": salvaged,
                "steps_used": len(getattr(result, "steps", []) or []),
                "result": result,
            }, None
    except Exception as exc:
        hook_mgr.fire(HookType.AFTER_SUBAGENT_RUN, {
            "task": task, "role": role, "success": False, "error": str(exc),
        })
        return None, exc



def _build_payload(status: str, role: str, answer: str, summary: str,
                   steps_used: int, allowed_tools: list[str], recovery: dict | None = None) -> str:
    payload = {
        "status": status,
        "role": role,
        "answer": answer,
        "summary": summary,
        "steps_used": steps_used,
        "allowed_tools": allowed_tools,
    }
    if recovery:
        payload["recovery"] = recovery
    return json.dumps(payload, ensure_ascii=False)



def make_subagent_run(parent_llm: AgentLLM | None = None, non_interactive: bool = False,
                      subagent_confirm: Callable[[str], bool] | None = None,
                      mcp_manager: "MCPToolManager | None" = None):
    def subagent_run(task: str, role: str = "explorer",
                     allowed_tools: list[str] | None = None,
                     max_steps: int = 4) -> str:
        effective_role = _normalize_role(role)
        tool_names, tool_recovery = _resolve_allowed_tools(effective_role, allowed_tools, mcp_manager)
        with trace_manager.span("subagent", {
            "requested_role": role,
            "effective_role": effective_role,
            "allowed_tools": tool_names,
            "max_steps": max_steps,
            "task_preview": (task or "")[:200],
        }) as span:
            if not tool_names:
                payload = _build_payload(
                    status="error",
                    role=effective_role,
                    answer="",
                    summary="没有可用的安全工具可分配给子代理。",
                    steps_used=0,
                    allowed_tools=[],
                    recovery={"attempted": False, "reason": tool_recovery or "no_safe_tools"},
                )
                span.output = {"payload": payload[:500]}
                span.metadata = {"status": "error", "agent_id": f"subagent_{effective_role}"}
                return payload

            attempt, error = _run_subagent_attempt(
                parent_llm,
                non_interactive,
                task,
                effective_role,
                tool_names,
                max_steps,
                subagent_confirm=subagent_confirm,
                mcp_manager=mcp_manager,
            )
            recovery = {
                "attempted": False,
                "reason": tool_recovery,
                "attempts": 1,
            }
            if tool_recovery:
                recovery["attempted"] = True

            if error is None and attempt is not None:
                direct_answer = attempt["answer"]
                answer = direct_answer or attempt["salvaged"]
                if answer:
                    if not direct_answer:
                        recovery.update({
                            "attempted": True,
                            "reason": recovery["reason"] or "salvaged_step_content",
                            "attempts": max(recovery["attempts"], 1),
                        })
                    payload = _build_payload(
                        status="ok" if not recovery["attempted"] else "fallback",
                        role=attempt["role"],
                        answer=answer,
                        summary=answer[:500],
                        steps_used=attempt["steps_used"],
                        allowed_tools=attempt["tool_names"],
                        recovery=recovery if recovery["attempted"] else None,
                    )
                    span.output = {"payload": payload[:500]}
                    span.metadata = {
                        "status": "ok" if not recovery["attempted"] else "fallback",
                        "agent_id": f"subagent_{effective_role}",
                        "recovery": recovery if recovery["attempted"] else None,
                    }
                    return payload

            fallback_role = "explorer"
            fallback_tools = [name for name in _ROLE_TOOL_PRESETS["explorer"] if name in _SAFE_SUBAGENT_TOOLS]
            fallback_reason = tool_recovery or (str(error) if error else "empty_answer")

            recovery.update({
                "attempted": True,
                "reason": fallback_reason,
                "attempts": 2,
            })

            fallback_attempt, fallback_error = _run_subagent_attempt(
                parent_llm,
                non_interactive,
                task,
                fallback_role,
                fallback_tools,
                min(max_steps + 1, 8),
                retry_reason=fallback_reason,
                subagent_confirm=subagent_confirm,
                mcp_manager=mcp_manager,
            )

            if fallback_error is None and fallback_attempt is not None:
                answer = fallback_attempt["answer"] or fallback_attempt["salvaged"]
                if answer:
                    payload = _build_payload(
                        status="fallback",
                        role=fallback_attempt["role"],
                        answer=answer,
                        summary=answer[:500],
                        steps_used=fallback_attempt["steps_used"],
                        allowed_tools=fallback_attempt["tool_names"],
                        recovery=recovery,
                    )
                    span.output = {"payload": payload[:500]}
                    span.metadata = {
                        "status": "fallback",
                        "agent_id": f"subagent_{fallback_role}",
                        "recovery": recovery,
                    }
                    return payload

            if attempt is not None and attempt.get("salvaged"):
                salvaged = attempt["salvaged"]
                payload = _build_payload(
                    status="fallback",
                    role=attempt["role"],
                    answer=salvaged,
                    summary=salvaged[:500],
                    steps_used=attempt["steps_used"],
                    allowed_tools=attempt["tool_names"],
                    recovery=recovery,
                )
                span.output = {"payload": payload[:500]}
                span.metadata = {
                    "status": "fallback",
                    "agent_id": f"subagent_{effective_role}",
                    "recovery": recovery,
                }
                return payload

            error_summary = str(fallback_error or error or "子代理未产出有效答案")
            payload = _build_payload(
                status="error",
                role=effective_role,
                answer="",
                summary=f"子代理执行失败: {error_summary}",
                steps_used=(fallback_attempt or attempt or {}).get("steps_used", 0),
                allowed_tools=(fallback_attempt or attempt or {}).get("tool_names", fallback_tools),
                recovery=recovery,
            )
            span.output = {"payload": payload[:500]}
            span.metadata = {"status": "error", "agent_id": f"subagent_{effective_role}", "recovery": recovery}
            return payload

    return subagent_run
