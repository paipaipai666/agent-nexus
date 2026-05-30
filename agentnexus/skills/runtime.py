"""Lightweight pre-run workflow runtime for session profiles."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from string import Formatter
from typing import Any

from agentnexus.core.text_utils import collapse_and_truncate
from agentnexus.observability.tracer import trace_manager
from agentnexus.skills.profile import filter_tool_meta
from agentnexus.skills.workflow import SessionProfile, WorkflowStep

_CORE_TEMPLATE_KEYS = {"tools", "question", "history", "memory_context", "conversation_context"}


@dataclass(frozen=True)
class WorkflowRuntimeEvent:
    step_id: str
    step_type: str
    status: str
    summary: str = ""
    run_id: str = ""
    duration_ms: float = 0
    error: str = ""


@dataclass(frozen=True)
class WorkflowRunEvent:
    run_id: str
    step_id: str
    step_type: str
    status: str
    summary: str = ""
    duration_ms: float = 0
    error: str = ""


@dataclass
class WorkflowStepState:
    id: str
    type: str
    status: str = "pending"
    context_block: str = ""
    error: str = ""
    started_at: float = 0
    ended_at: float = 0

    @property
    def duration_ms(self) -> float:
        if not self.started_at or not self.ended_at:
            return 0
        return round((self.ended_at - self.started_at) * 1000, 1)


@dataclass
class WorkflowRunContext:
    question: str
    variables: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRunState:
    run_id: str
    question: str
    workflow_id: str
    status: str = "running"
    current_step: int = 0
    variables: dict[str, Any] = field(default_factory=dict)
    steps: list[WorkflowStepState] = field(default_factory=list)
    events: list[WorkflowRunEvent] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for step in self.steps if step.status == "ok")

    @property
    def error_count(self) -> int:
        return sum(1 for step in self.steps if step.status == "error")

    @property
    def skipped_count(self) -> int:
        return sum(1 for step in self.steps if step.status == "skipped")


@dataclass
class WorkflowRunResult:
    question: str
    workflow_context: str
    events: list[WorkflowRuntimeEvent] = field(default_factory=list)
    state: WorkflowRunState | None = None

    @property
    def enhanced_question(self) -> str:
        if not self.workflow_context:
            return self.question
        return f"{self.workflow_context}\n\n== User Question ==\n{self.question}"


class WorkflowRuntime:
    """Executes safe declarative workflow pre-steps before ReActAgent.run()."""

    def prepare(
        self,
        question: str,
        profile: SessionProfile | None,
        *,
        tool_executor: Any = None,
        memory_manager: Any = None,
    ) -> WorkflowRunResult:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_WORKFLOW_STEP, {
            "method": "prepare", "question": question[:200],
            "has_profile": profile is not None,
        })

        if profile is None:
            return WorkflowRunResult(question=question, workflow_context="", events=[])

        state = self.start(question, profile)
        state = self.advance(state, profile, tool_executor=tool_executor, memory_manager=memory_manager)
        context = self.render_context(state)
        events = [
            WorkflowRuntimeEvent(
                step_id=event.step_id,
                step_type=event.step_type,
                status=event.status,
                summary=event.summary,
                run_id=event.run_id,
                duration_ms=event.duration_ms,
                error=event.error,
            )
            for event in state.events
        ]
        hook_mgr.fire(HookType.AFTER_WORKFLOW_STEP, {
            "method": "prepare", "step_count": len(events),
            "event_count": len(events),
        })
        return WorkflowRunResult(question=question, workflow_context=context, events=events, state=state)

    def start(self, question: str, profile: SessionProfile) -> WorkflowRunState:
        steps = [
            WorkflowStepState(id=step.id or f"{step.type}_{index}", type=step.type)
            for index, step in enumerate(profile.steps, start=1)
        ]
        return WorkflowRunState(
            run_id=f"workflow_{uuid.uuid4().hex[:12]}",
            question=question,
            workflow_id=profile.workflow_id,
            variables={**(profile.prompt_profile.variables or {}), "question": question},
            steps=steps,
        )

    def advance(
        self,
        state: WorkflowRunState,
        profile: SessionProfile,
        *,
        tool_executor: Any = None,
        memory_manager: Any = None,
    ) -> WorkflowRunState:
        ctx = WorkflowRunContext(
            question=state.question,
            variables=dict(state.variables),
        )

        for index, step in enumerate(profile.steps):
            if index >= len(state.steps):
                break
            step_state = state.steps[index]
            if step_state.status not in {"pending", "running"}:
                continue
            state.current_step = index
            step_state.status = "running"
            step_state.started_at = time.time()
            summary = ""
            error = ""
            try:
                with trace_manager.span("workflow_step", {
                    "run_id": state.run_id,
                    "workflow_id": profile.workflow_id,
                    "step_id": step_state.id,
                    "step_type": step_state.type,
                }) as span:
                    block = self._run_step(
                        step,
                        ctx,
                        profile,
                        tool_executor=tool_executor,
                        memory_manager=memory_manager,
                    )
                    step_state.context_block = _truncate_block(block)
                    step_state.status = "ok"
                    summary = collapse_and_truncate(block or step.type, 72)
                    span.output = {
                        "status": step_state.status,
                        "summary": summary,
                    }
                    span.metadata = {
                        "status": step_state.status,
                        "workflow_id": profile.workflow_id,
                    }
                _update_workflow_trace_span(
                    state.run_id,
                    step_state.id,
                    status=step_state.status,
                    summary=summary,
                    workflow_id=profile.workflow_id,
                )
            except Exception as exc:
                message = f"{step.type} failed: {exc}"
                step_state.context_block = f"[{step_state.id}] {message}"
                step_state.status = "error"
                step_state.error = str(exc)
                summary = message
                error = str(exc)
                _update_workflow_trace_span(
                    state.run_id,
                    step_state.id,
                    status=step_state.status,
                    summary=summary,
                    workflow_id=profile.workflow_id,
                    error=error,
                )
            finally:
                step_state.ended_at = time.time()
                state.events.append(WorkflowRunEvent(
                    run_id=state.run_id,
                    step_id=step_state.id,
                    step_type=step_state.type,
                    status=step_state.status,
                    summary=summary,
                    duration_ms=step_state.duration_ms,
                    error=error,
                ))

        state.status = "completed"
        state.ended_at = time.time()
        return state

    def render_context(self, state: WorkflowRunState) -> str:
        blocks = [f"== Workflow Runtime Context ==\nWorkflow: {state.workflow_id}\nRun: {state.run_id}"]
        for step in state.steps:
            if step.context_block:
                blocks.append(step.context_block)
            elif step.status == "skipped":
                blocks.append(f"[{step.id}] skipped")
        return "\n\n".join(blocks)

    def _run_step(
        self,
        step: WorkflowStep,
        ctx: WorkflowRunContext,
        profile: SessionProfile,
        *,
        tool_executor: Any,
        memory_manager: Any,
    ) -> str:
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()
        hook_mgr.fire(HookType.BEFORE_WORKFLOW_STEP, {
            "method": "_run_step", "step_type": step.type, "step_id": step.id,
        })

        result = ""
        if step.type == "prompt":
            prompt = _format_with_variables(step.prompt or "", ctx.variables)
            result = f"[prompt:{step.id or '-'}]\n{prompt}" if prompt else ""
        elif step.type == "retrieve":
            result = self._run_retrieve(step, ctx, profile, tool_executor=tool_executor, memory_manager=memory_manager)
        elif step.type == "tool_call":
            result = self._run_tool_call(step, ctx, profile, tool_executor=tool_executor)
        elif step.type == "checkpoint":
            text = _format_with_variables(step.prompt or "checkpoint", ctx.variables)
            result = f"[checkpoint:{step.id or '-'}]\n{text}"
        elif step.type == "finalize":
            lines = [f"[finalize:{step.id or '-'}]"]
            if step.prompt:
                lines.append(_format_with_variables(step.prompt, ctx.variables))
            if profile.success_criteria:
                lines.append("Success criteria:")
                lines.extend(f"- {_format_with_variables(item, ctx.variables)}" for item in profile.success_criteria)
            result = "\n".join(lines)

        hook_mgr.fire(HookType.AFTER_WORKFLOW_STEP, {
            "method": "_run_step", "step_type": step.type,
            "step_id": step.id, "result_length": len(result),
        })
        return result
        return ""

    def _run_retrieve(
        self,
        step: WorkflowStep,
        ctx: WorkflowRunContext,
        profile: SessionProfile,
        *,
        tool_executor: Any,
        memory_manager: Any,
    ) -> str:
        query = _format_with_variables(step.prompt or ctx.question, ctx.variables)
        if tool_executor is not None and _tool_visible(tool_executor, "kb_search", profile):
            args = {
                "query": query,
                "namespace": profile.retrieval_policy.namespace,
                "top_k": profile.retrieval_policy.top_k,
                "view": profile.retrieval_policy.view,
            }
            args.update(_supported_kb_filters(profile.retrieval_policy.filters))
            result = tool_executor.invoke(
                "kb_search",
                args,
                caller="react_agent",
                tool_policy=profile.tool_policy,
            )
            return f"[retrieve:{step.id or '-'}]\n{result}"
        if memory_manager is not None and profile.memory_policy.inject_long_term:
            result = memory_manager.init_session(query)
            return f"[retrieve:{step.id or '-'}]\n{result}" if result else ""
        return f"[retrieve:{step.id or '-'}]\n(no retrieval source available)"

    def _run_tool_call(
        self,
        step: WorkflowStep,
        ctx: WorkflowRunContext,
        profile: SessionProfile,
        *,
        tool_executor: Any,
    ) -> str:
        if not step.tool:
            raise ValueError("tool_call step requires tool")
        if tool_executor is None:
            raise ValueError("tool executor unavailable")
        args = _format_value(step.arguments, ctx.variables)
        result = tool_executor.invoke(
            step.tool,
            args,
            caller="react_agent",
            tool_policy=profile.tool_policy,
        )
        return f"[tool_call:{step.id or step.tool}]\n{step.tool}: {result}"


def _tool_visible(tool_executor: Any, tool_name: str, profile: SessionProfile) -> bool:
    registry = tool_executor if hasattr(tool_executor, "list_tools") else None
    if registry is None:
        return False
    entry = getattr(registry, "_tools", {}).get(tool_name)
    if entry is None:
        return False
    meta, _ = entry
    allowed_agent = "*" in meta.allowed_agents or "react_agent" in meta.allowed_agents
    return allowed_agent and filter_tool_meta(tool_name, meta, profile.tool_policy)


def _format_value(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _format_with_variables(value, variables)
    if isinstance(value, dict):
        return {key: _format_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_format_value(item, variables) for item in value]
    return value


def _supported_kb_filters(filters: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "source",
        "file_format",
        "section_title",
        "page_number",
        "block_type",
        "has_code",
        "has_list",
        "heading_depth",
    }
    return {key: value for key, value in (filters or {}).items() if key in allowed}


def _format_with_variables(text: str, variables: dict[str, Any]) -> str:
    if not text:
        return text
    allowed = {
        field_name for _, field_name, _, _ in Formatter().parse(text)
        if field_name and field_name not in _CORE_TEMPLATE_KEYS
    }
    safe_vars = {key: value for key, value in variables.items() if key in allowed}
    try:
        return text.format(**safe_vars)
    except (KeyError, ValueError):
        return text


def _truncate_block(text: str, limit: int = 4000) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: limit - 32] + "\n[truncated workflow context]"


def _update_workflow_trace_span(
    run_id: str,
    step_id: str,
    *,
    status: str,
    summary: str,
    workflow_id: str,
    error: str = "",
) -> None:
    active = trace_manager.active
    if active is None:
        return
    for span in reversed(active.spans):
        if (
            span.name == "workflow_step"
            and span.input.get("run_id") == run_id
            and span.input.get("step_id") == step_id
        ):
            span.output = {
                "status": status,
                "summary": summary,
            }
            if error:
                span.output["error"] = error
            span.metadata = {
                "status": status,
                "workflow_id": workflow_id,
            }
            if error:
                span.metadata["error"] = error
            return
