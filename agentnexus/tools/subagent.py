"""Subagent delegation tool built on top of ReActAgent."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import TYPE_CHECKING, Callable, Iterable

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agentnexus.tools.mcp.adapter import MCPToolManager

from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.core.llm import AgentLLM
from agentnexus.core.text_utils import collapse_and_truncate
from agentnexus.observability.tracer import trace_manager
from agentnexus.tools.registry import ToolRegistry

_SAFE_SUBAGENT_TOOLS = {
    "grep_search",
    "web_search",
    "kb_search",
    "file_read",
    "file_list",
    "memory_search",
    # shell_exec replaces the removed python_execute for delegated execution;
    # it stays HITL-gated via the subagent confirm bridge at call time.
    "shell_exec",
}

_ROLE_TOOL_PRESETS = {
    "explorer": ["grep_search", "web_search", "kb_search", "file_read", "file_list", "memory_search"],
    "executor": ["shell_exec", "file_read", "file_list", "grep_search"],
}

_ROLE_DESCRIPTIONS = {
    "explorer": "Explorer 子代理，适合阅读、检索、归纳和信息收集",
    "executor": "Executor 子代理，适合在受控环境中执行命令并验证结果",
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



def _register_child_tools(executor: ToolRegistry, parent_llm: AgentLLM | None,
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



def _make_child_forwarder(sctx, entry):
    """Bridge child-agent ReActEvents into the parent run's event stream.

    Called on the lane-pool thread running child_agent.run() (FSM subscribe
    callback, chat.py:795 signature). Observability only — a throwing
    forwarder must never break the child loop.
    """
    def forward(event, _from_state, _to_state):
        if event is None:
            return
        try:
            etype = event.type.name
            p = event.payload or {}
            if etype == "TOOL_START":
                sctx.event(entry, "tool_call", status="tool_calling",
                           tool_name=p.get("name", ""),
                           arguments=p.get("arguments", {}),
                           tool_call_id=p.get("id", ""))
            elif etype == "TOOL_DONE":
                sctx.event(entry, "tool_result", status="thinking",
                           tool_name=p.get("name", ""),
                           tool_call_id=p.get("id", ""),
                           result=collapse_and_truncate(p.get("result", ""), 300),
                           duration_ms=p.get("duration_ms", 0))
            elif etype == "STREAM_TOKEN":
                token = p.get("token", "")
                if token:
                    sctx.event(entry, "token", content=token)
            elif etype == "STREAM_REASONING":
                token = p.get("token", "")
                if token:
                    sctx.event(entry, "reasoning", content=token)
            elif etype in ("TOOLS_REQUESTED", "ANSWER_THOUGHT"):
                thought = (p.get("thought") or "").strip()
                if thought:
                    sctx.event(entry, "thinking", status="thinking",
                               content=thought)
        except Exception:
            logger.debug("subagent forwarder failed", exc_info=True)

    return forward



def _run_subagent_attempt(parent_llm: AgentLLM | None, non_interactive: bool,
                          task: str, role: str, tool_names: list[str],
                          retry_reason: str | None = None,
                          subagent_confirm: Callable[[str], bool] | None = None,
                          mcp_manager: "MCPToolManager | None" = None,
                          cancel_bridge=None,
                          subagent_ctx=None, subagent_entry=None) -> tuple[dict | None, Exception | None]:
    from agentnexus.core.hooks import HookType, get_hook_manager

    hook_mgr = get_hook_manager()
    hook_mgr.fire(HookType.BEFORE_SUBAGENT_RUN, {
        "task": task, "role": role, "tool_names": tool_names,
        "retry_reason": retry_reason,
    })

    child_llm = _clone_llm(parent_llm)
    child_executor = ToolRegistry()
    try:
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
            # No step cap: the child runs until the task completes. The
            # wall-clock budget (subagent_timeout_sec) is the real guard —
            # a hard step cap truncated long investigations mid-work
            # ("已达到最大步数") while buying nothing the timeout doesn't.
            max_steps=None,
            output=lambda *_args, **_kwargs: None,
            confirm_fn=subagent_confirm,
            conversation_mode=False,
            agent_id=f"subagent_{role}",
        )
        # Timeout cancel signal — set when the wall-clock budget expires so the
        # child stops at the next FSM boundary instead of orphaning its thread.
        local_cancel = threading.Event()
        # Cooperative cancellation: parent run cancelled, UI interrupt on this
        # subagent, or the wall-clock budget expired → stop at next boundary.
        # Always installed — a child without any checker cannot be cancelled.
        child_agent.set_cancel_checker(
            lambda: local_cancel.is_set()
                    or (cancel_bridge is not None and cancel_bridge.check())
                    or (subagent_entry is not None and subagent_entry.cancel_event.is_set())
        )
        if subagent_ctx is not None and subagent_entry is not None:
            # Visibility: forward child FSM events into the parent run's
            # queue so the UI can show this subagent's trajectory/status.
            child_agent._on_event = _make_child_forwarder(subagent_ctx, subagent_entry)

        try:
            with trace_manager.span("subagent_attempt", {
                "role": role,
                "tool_names": tool_names,
                "retry_reason": retry_reason or "",
                "task_preview": task[:200],
                "parent_trace_id": trace_manager.get_inherited_trace() or "",
            }) as span:
                try:
                    from agentnexus.core.config import get_settings
                    deadline = get_settings().subagent_timeout_sec
                except Exception:
                    deadline = 1800
                runner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subagent-runner")
                parent_trace_id = trace_manager.get_inherited_trace()

                def _run_child(prompt: str):
                    # Runner thread has no active TraceContext; stamp the
                    # parent's trace id so child spans stay joinable
                    # (same pattern as tools/dispatcher.py).
                    trace_manager.set_inherited_trace(parent_trace_id)
                    try:
                        return child_agent.run(prompt, memory_manager=None)
                    finally:
                        trace_manager.set_inherited_trace(None)

                try:
                    future = runner.submit(
                        _run_child,
                        _build_subagent_prompt(task, role, retry_reason),
                    )
                    try:
                        result = future.result(timeout=deadline)
                    except FutureTimeout:
                        # Budget expired: signal cooperative cancel. The child FSM
                        # checks the cancel flag at every step boundary, raises
                        # AgentCancelled, and the runner thread exits — no orphan.
                        local_cancel.set()
                        if subagent_entry is not None:
                            subagent_entry.cancel_event.set()
                        logger.warning(
                            "Subagent attempt exceeded %ss — cancelling cooperatively", deadline,
                        )
                        try:
                            result = future.result(timeout=120)  # grace for in-flight LLM call
                        except FutureTimeout:
                            raise TimeoutError(
                                f"subagent ignored cancel and ran past {deadline}s + 120s grace"
                            )
                finally:
                    runner.shutdown(wait=False)
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
    finally:
        # The child registry is per-attempt; without shutdown its pool
        # threads would linger (one+ per attempt on Python < 3.13).
        child_executor.close()



def _build_payload(status: str, role: str, answer: str, summary: str,
                   steps_used: int, allowed_tools: list[str], recovery: dict | None = None,
                   subagent_id: str = "", name: str = "") -> str:
    payload = {
        "status": status,
        "role": role,
        "answer": answer,
        "summary": summary,
        "steps_used": steps_used,
        "allowed_tools": allowed_tools,
        "subagent_id": subagent_id,
        "name": name,
    }
    if recovery:
        payload["recovery"] = recovery
    return json.dumps(payload, ensure_ascii=False)



def make_subagent_run(parent_llm: AgentLLM | None = None, non_interactive: bool = False,
                      subagent_confirm: Callable[[str], bool] | None = None,
                      mcp_manager: "MCPToolManager | None" = None,
                      cancel_bridge=None, subagent_bridge=None):
    def subagent_run(task: str, role: str = "explorer",
                     allowed_tools: list[str] | None = None,
                     name: str | None = None) -> str:
        effective_role = _normalize_role(role)
        tool_names, tool_recovery = _resolve_allowed_tools(effective_role, allowed_tools, mcp_manager)
        with trace_manager.span("subagent", {
            "requested_role": role,
            "effective_role": effective_role,
            "allowed_tools": tool_names,
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

            # Subagent visibility: register the named entry (agent-provided
            # `name` or auto-generated) for the sidebar/detail view.
            sctx = subagent_bridge.get_context() if subagent_bridge is not None else None
            entry = sctx.started(name, effective_role, task) if sctx is not None else None
            _sid = entry.subagent_id if entry is not None else ""
            _name = entry.name if entry is not None else ""

            if entry is not None and entry.cancel_event.is_set():
                # Interrupted while queued on the lane pool — never start.
                sctx.finished(entry, "interrupted", error="cancelled before start")
                payload = _build_payload(
                    status="error", role=effective_role, answer="",
                    summary="子代理已被打断", steps_used=0,
                    allowed_tools=tool_names, subagent_id=_sid, name=_name,
                )
                span.output = {"payload": payload[:500]}
                span.metadata = {"status": "interrupted", "agent_id": f"subagent_{effective_role}"}
                return payload

            attempt, error = _run_subagent_attempt(
                parent_llm,
                non_interactive,
                task,
                effective_role,
                tool_names,
                subagent_confirm=subagent_confirm,
                mcp_manager=mcp_manager,
                cancel_bridge=cancel_bridge,
                subagent_ctx=sctx,
                subagent_entry=entry,
            )
            if error is not None and (isinstance(error, AgentCancelled)
                                      or (entry is not None and entry.cancel_event.is_set())):
                # Cancelled (per-subagent interrupt or parent run cancel):
                # do NOT run the fallback attempt — a second child run would
                # only burn tokens before hitting the same checker.
                if sctx is not None and entry is not None:
                    sctx.finished(entry, "interrupted", error=str(error) or "cancelled")
                payload = _build_payload(
                    status="error", role=effective_role, answer="",
                    summary="子代理已被打断",
                    steps_used=(attempt or {}).get("steps_used", 0) if attempt else 0,
                    allowed_tools=tool_names, subagent_id=_sid, name=_name,
                )
                span.output = {"payload": payload[:500]}
                span.metadata = {"status": "interrupted", "agent_id": f"subagent_{effective_role}"}
                return payload
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
                        subagent_id=_sid,
                        name=_name,
                    )
                    span.output = {"payload": payload[:500]}
                    span.metadata = {
                        "status": "ok" if not recovery["attempted"] else "fallback",
                        "agent_id": f"subagent_{effective_role}",
                        "recovery": recovery if recovery["attempted"] else None,
                    }
                    if sctx is not None and entry is not None:
                        sctx.finished(entry, "completed", summary=answer[:500],
                                      steps_used=attempt["steps_used"])
                    return payload

            fallback_role = "explorer"
            fallback_tools = [name for name in _ROLE_TOOL_PRESETS["explorer"] if name in _SAFE_SUBAGENT_TOOLS]
            fallback_reason = tool_recovery or (str(error) if error else "empty_answer")

            recovery.update({
                "attempted": True,
                "reason": fallback_reason,
                "attempts": 2,
            })

            if sctx is not None and entry is not None:
                sctx.event(entry, "retry", status="thinking", reason=fallback_reason)

            fallback_attempt, fallback_error = _run_subagent_attempt(
                parent_llm,
                non_interactive,
                task,
                fallback_role,
                fallback_tools,
                retry_reason=fallback_reason,
                subagent_confirm=subagent_confirm,
                mcp_manager=mcp_manager,
                cancel_bridge=cancel_bridge,
                subagent_ctx=sctx,
                subagent_entry=entry,
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
                        subagent_id=_sid,
                        name=_name,
                    )
                    span.output = {"payload": payload[:500]}
                    span.metadata = {
                        "status": "fallback",
                        "agent_id": f"subagent_{fallback_role}",
                        "recovery": recovery,
                    }
                    if sctx is not None and entry is not None:
                        sctx.finished(entry, "completed", summary=answer[:500],
                                      steps_used=fallback_attempt["steps_used"])
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
                    subagent_id=_sid,
                    name=_name,
                )
                span.output = {"payload": payload[:500]}
                span.metadata = {
                    "status": "fallback",
                    "agent_id": f"subagent_{effective_role}",
                    "recovery": recovery,
                }
                if sctx is not None and entry is not None:
                    sctx.finished(entry, "completed", summary=salvaged[:500],
                                  steps_used=attempt["steps_used"])
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
                subagent_id=_sid,
                name=_name,
            )
            span.output = {"payload": payload[:500]}
            span.metadata = {"status": "error", "agent_id": f"subagent_{effective_role}", "recovery": recovery}
            if sctx is not None and entry is not None:
                sctx.finished(entry, "failed", summary=f"子代理执行失败: {error_summary}"[:500],
                              error=error_summary[:500],
                              steps_used=(fallback_attempt or attempt or {}).get("steps_used", 0))
            return payload

    return subagent_run
