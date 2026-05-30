"""ReActAgent - event-driven FSM implementation.

Thought -> Action -> Observation loop driven by a transfer-table state machine.
Each decision point is an explicit state; each transition is a handler method.
"""

from typing import Callable

from agentnexus.agents import json_helpers, react_runtime
from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.llm_strategy import build_json_format_section, call_llm
from agentnexus.agents.prompt_builder import build_conversation_context, build_react_messages, build_react_prompt
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActResult,
)
from agentnexus.agents.tool_runner import execute_tool
from agentnexus.core.capabilities import SessionCapabilityTracker
from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.tracer import trace_manager
from agentnexus.prompts import load_prompt
from agentnexus.skills import CompiledSessionProfile, SessionProfile, validate_session_profile
from agentnexus.tools.registry import ToolRegistry

REACT_PROMPT_TEMPLATE = load_prompt("react")
REACT_THINK_PROMPT_TEMPLATE = load_prompt("react_think")

MAX_JSON_RETRIES = 2

# Re-export for backward compatibility
__all__ = ["ReActAgent", "CallingStrategy", "AgentStep"]


class ReActAgent:
    """Event-driven ReAct agent with transfer-table FSM.

    Constructor and public API remain backward-compatible.
    The run() method now delegates to a StateMachine with per-transition handlers.
    """

    def __init__(self, llm_client: AgentLLM, tool_executor: ToolRegistry,
                 max_steps: int | None = None,
                 output=None, confirm_fn=None, async_confirm=None,
                 conversation_mode: bool = False,
                 agent_id: str = "react_agent"):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        configured_max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self.max_steps = configured_max_steps if isinstance(configured_max_steps, int) else 5
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self._async_confirm = async_confirm
        self.conversation_mode = conversation_mode
        self.agent_id = agent_id
        self._total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        self._on_event: Callable | None = None
        self._session_profile: SessionProfile | None = None
        self._compiled_session_profile: CompiledSessionProfile | None = None
        self._available_skill_context: str = ""
        self._mcp_context: str = ""
        self._workflow_context: str = ""
        self._cancel_checker: Callable[[], bool] | None = None
        self._todo_list = None  # Set externally after construction
        self._degrade_count = 0
        self._thought_retries = 0

    # ================================================================
    # Public API (unchanged)
    # ================================================================

    @property
    def total_usage(self) -> dict:
        return dict(self._total_usage)

    @property
    def model_id(self) -> str:
        return self.llm_client.model

    @property
    def session_profile(self) -> SessionProfile | None:
        return self._session_profile

    @property
    def compiled_session_profile(self) -> CompiledSessionProfile | None:
        return self._compiled_session_profile

    def set_session_profile(self, profile: SessionProfile | None) -> None:
        """Apply a workflow-backed session profile for future runs."""
        self._session_profile = profile
        self._compiled_session_profile = validate_session_profile(profile) if profile is not None else None

    def set_available_skill_context(self, context: str) -> None:
        """Expose local skill metadata to the next prompt without selecting a skill."""
        self._available_skill_context = context or ""

    def set_mcp_context(self, context: str) -> None:
        """Expose discovered MCP resources and prompts to the next prompt."""
        self._mcp_context = context or ""

    def set_workflow_context(self, context: str) -> None:
        """Expose workflow runtime context to the next prompt as a system message."""
        self._workflow_context = context or ""

    def set_cancel_checker(self, checker: Callable[[], bool] | None) -> None:
        """Install a cooperative cancellation callback for the next run."""
        self._cancel_checker = checker

    @property
    def _react_template(self) -> str:
        """Select react template based on thinking capability."""
        if getattr(self.llm_client.capabilities, 'supports_thinking', False) is True:
            return REACT_THINK_PROMPT_TEMPLATE
        return REACT_PROMPT_TEMPLATE

    def run(self, question: str, memory_manager=None) -> ReActResult:
        """Thin entry point: build context, run FSM loop, return structured result."""
        from agentnexus.core.hooks import HookType, get_hook_manager

        hook_mgr = get_hook_manager()

        # ── agent start hook ─────────────────────────────────────
        hook_mgr.fire(HookType.AGENT_START, {
            "question": question,
            "agent_id": self.agent_id,
        })

        self._total_usage = {"input_tokens": 0, "output_tokens": 0}
        self._degrade_count = 0
        self._thought_retries = 0

        try:
            ctx = ExecutionContext(
                question=question,
                messages=[],
                current_step=0,
                json_retries=0,
                strategy=CallingStrategy.PROMPT_JSON,
                max_steps=self.max_steps,
                max_json_retries=MAX_JSON_RETRIES,
                memory_manager=memory_manager,
            )
            ctx.run_state.thinking_enabled = self.llm_client.capabilities.supports_thinking
            ctx.run_state.cancel_checker = self._cancel_checker

            fsm = StateMachine(TRANSFER_TABLE)
            if self._on_event:
                fsm.subscribe(self._on_event)
                ctx._on_emit = self._on_event

            answer, steps = fsm.run_loop(
                ReActEvent(ReActEventType.START, {"question": question}),
                ctx,
                self._get_handlers(),
            )
            self._total_usage = ctx._total_usage
        except KeyboardInterrupt:
            answer = "[Agent execution cancelled by user]"
            steps = []
            self._total_usage = {"input_tokens": 0, "output_tokens": 0}

        # ── agent end hook ───────────────────────────────────────
        hook_mgr.fire(HookType.AGENT_END, {
            "answer": answer,
            "steps": len(steps),
            "agent_id": self.agent_id,
        })

        return ReActResult(answer=answer, steps=steps)

    def _get_handlers(self) -> dict:
        """Return mapping of handler_name -> bound method for the FSM engine."""
        return {
            "_on_init": self._on_init,
            "_on_strategy_ready": self._on_strategy_ready,
            "_on_llm_params_ready": self._on_llm_params_ready,
            "_on_llm_response": self._on_llm_response,
            "_on_llm_error": self._on_llm_error,
            "_on_receive_native": self._on_receive_native,
            "_on_receive_json": self._on_receive_json,
            "_on_tools_found": self._on_tools_found,
            "_on_thought_missing": self._on_thought_missing,
            "_on_no_tools_answer": self._on_no_tools_answer,
            "_on_no_tools_degrade": self._on_no_tools_degrade,
            "_on_tool_done": self._on_tool_done,
            "_on_all_tools_done": self._on_all_tools_done,
            "_on_empty_response": self._on_empty_response,
            "_on_has_content": self._on_has_content,
            "_on_parse_success": self._on_parse_success,
            "_on_parse_error": self._on_parse_error,
            "_on_classified_tool": self._on_classified_tool,
            "_on_classified_answer": self._on_classified_answer,
            "_on_classified_error": self._on_classified_error,
            "_on_retries_left": self._on_retries_left,
            "_on_no_retries_degrade": self._on_no_retries_degrade,
            "_on_fallback_text": self._on_fallback_text,
            "_on_degraded": self._on_degraded,
            "_on_max_steps_abort": self._on_max_steps_abort,
            "_on_error_abort": self._on_error_abort,
            "_on_emit_answer": self._on_emit_answer,
        }

    # ================================================================
    # FSM Handlers — each returns list[ReActEvent] for the event queue
    # ================================================================

    def _on_init(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """INIT + START -> build prompts, messages, memory; select strategy."""
        session_caps = SessionCapabilityTracker()
        run_state = ctx.run_state
        memory_state = ctx.memory_state
        tool_state = ctx.tool_state

        memory_state.session_caps = session_caps
        run_state.strategy = self._select_strategy(session_caps)

        memory_manager = memory_state.memory_manager
        if memory_manager:
            memory_state.memory_context = memory_manager.init_session(run_state.question)
            memory_manager.append("user", run_state.question)

        tool_policy = self._compiled_session_profile.tool_policy if self._compiled_session_profile else None
        tool_state.tools = self.tool_executor.to_openai_tools(self.agent_id, tool_policy=tool_policy)
        tool_state.tools_desc = self.tool_executor.get_available_tools(self.agent_id, tool_policy=tool_policy)

        if self.conversation_mode and memory_manager:
            memory_state.conv_ctx = self._build_conversation_context(memory_manager, per_msg_limit=800)

        # Build messages with stable prefix for prompt caching
        ctx.messages = self._build_messages(
            tool_state.tools_desc,
            run_state.question,
            memory_state.memory_context,
            memory_state.conv_ctx,
            workflow_context=self._workflow_context,
        )

        if memory_manager:
            def rebuild():
                profile = self._compiled_session_profile
                policy = profile.tool_policy if profile else None
                new_tools_desc = self.tool_executor.get_available_tools(self.agent_id, tool_policy=policy)
                new_conv = ""
                if self.conversation_mode:
                    new_conv = self._build_conversation_context(memory_manager, per_msg_limit=800)
                    memory_state.conv_ctx = new_conv
                # Rebuild messages with stable prefix structure
                new_messages = self._build_messages(
                    new_tools_desc,
                    run_state.question,
                    memory_state.memory_context,
                    new_conv,
                    workflow_context=self._workflow_context,
                )
                # Preserve accumulated assistant/tool/user messages after the initial messages
                ctx.messages[:len(new_messages)] = new_messages
            memory_manager._on_after_compact = rebuild

        if run_state.current_step >= run_state.max_steps:
            return [ReActEvent(ReActEventType.ABORT)]

        return [ReActEvent(ReActEventType.STRATEGY_READY,
                           {"strategy": run_state.strategy.name})]

    def _on_strategy_ready(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """SELECT_STRATEGY + STRATEGY_READY -> advance to PREPARE_LLM_CALL."""
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_llm_params_ready(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """PREPARE_LLM_CALL + LLM_PARAMS_READY -> set params, call LLM."""
        ctx.run_state.current_step += 1

        def _stream_token(token: str, is_reasoning: bool = False):
            if is_reasoning:
                ctx.emit(ReActEventType.STREAM_REASONING, token=token)
            else:
                ctx.emit(ReActEventType.STREAM_TOKEN, token=token)

        on_token = _stream_token

        response_text = call_llm(
            self.llm_client,
            ctx,
            json_format_section=self._build_json_format_section(),
            on_token=on_token,
        )

        if self.llm_client.last_error and not response_text:
            return [ReActEvent(ReActEventType.LLM_ERROR,
                               {"error": self.llm_client.last_error})]

        return [ReActEvent(ReActEventType.LLM_RESPONSE,
                           {"response_text": response_text})]

    def _on_llm_response(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CALL_LLM + LLM_RESPONSE -> record AgentStep, route to native or JSON path."""
        return react_runtime.record_llm_response(
            ctx,
            response_text=event.payload.get("response_text", ""),
            llm_client=self.llm_client,
        )

    def _on_llm_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CALL_LLM + LLM_ERROR -> abort."""
        err = event.payload.get("error", "LLM call failed")
        self._output(f"错误: {err}")
        return [ReActEvent(ReActEventType.ABORT)]

    # ── NATIVE_TOOLS path ──

    def _on_receive_native(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """RECEIVE_RESPONSE + ROUTE_NATIVE -> check tool_calls."""
        if ctx.pending_tool_calls:
            thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning)
            if not thought:
                ctx.messages.append(
                    {"role": "user",
                     "content": "你必须先用 Thought 分析当前情况、说明意图，然后才能调用工具。"})
                return [ReActEvent(ReActEventType.THOUGHT_MISSING,
                                   {"reason": "missing_thought"})]
            self._on_native_tool_calls(ctx, thought)
            evt = ReActEvent(ReActEventType.TOOLS_FOUND,
                             {"tool_calls": list(ctx.pending_tool_calls),
                              "thought": thought})
            return [evt]

        text = ctx.last_response_text or ctx.last_reasoning
        if text:
            self._emit_answer_thought(ctx)
            ctx.last_answer = text
            return [ReActEvent(ReActEventType.NO_TOOLS,
                               {"text": text})]

        return [ReActEvent(ReActEventType.NO_TOOLS_NO_TEXT)]

    def _on_native_tool_calls(self, ctx: ExecutionContext, thought: str = None):
        """Record tool_calls into step, append assistant message."""
        react_runtime.record_native_tool_calls(
            ctx,
            thought=thought or "",
            reasoning_content=self.llm_client.last_reasoning_content or "",
            output=self._output,
        )

    def _on_tools_found(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + TOOLS_FOUND -> start executing first tool."""
        return [self._exec_next_tool(ctx)]

    def _on_thought_missing(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + THOUGHT_MISSING -> route to retry gate."""
        self._thought_retries += 1
        if self._thought_retries > 2:
            ctx.memory_state.session_caps.mark_failed("tool_calling")
            return [ReActEvent(ReActEventType.DEGRADED)]
        return self._check_retry_gate(ctx, event.payload.get("reason", "missing_thought"))

    def _on_tool_done(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL + TOOL_DONE -> execute next tool or finish."""
        react_runtime.record_tool_done(ctx, event.payload)
        return [self._exec_next_tool(ctx)]

    def _exec_next_tool(self, ctx: ExecutionContext) -> ReActEvent:
        """Execute the next pending tool call. Emits TOOL_DONE or ALL_TOOLS_DONE."""
        return react_runtime.execute_pending_tool(
            ctx,
            execute_tool=self._execute_tool,
            output=self._output,
        )

    def _on_no_tools_answer(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + NO_TOOLS -> plain text is the final answer."""
        return []  # EMIT_ANSWER handler will read ctx.last_answer

    def _on_no_tools_degrade(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + NO_TOOLS_NO_TEXT -> degrade from NATIVE_TOOLS."""
        ctx.memory_state.session_caps.mark_failed("tool_calling")
        return [ReActEvent(ReActEventType.DEGRADED)]

    # ── Non-NATIVE (JSON) path ──

    def _on_receive_json(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """RECEIVE_RESPONSE + ROUTE_JSON -> check if response is empty."""
        if not ctx.last_response_text:
            return [ReActEvent(ReActEventType.EMPTY_RESPONSE)]
        return [ReActEvent(ReActEventType.HAS_CONTENT)]

    def _on_empty_response(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_EMPTY + EMPTY_RESPONSE -> retry or abort."""
        return self._check_retry_gate(ctx, "empty_response")

    def _on_has_content(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_EMPTY + HAS_CONTENT -> JSON parse the response."""
        if self.llm_client.last_truncated:
            ctx.last_answer = json_helpers.extract_answer_from_text(ctx.last_response_text)
            self._output("[截断检测] LLM 输出被截断，直接提取答案文本")
            return [ReActEvent(ReActEventType.PARSE_SUCCESS, {
                "parsed": {"type": "answer", "text": ctx.last_answer}
            })]
        parsed = self._robust_json_parse(ctx.last_response_text)
        if parsed["type"] == "error":
            ctx.last_answer = None  # signal parse error for retry gate
            return [ReActEvent(ReActEventType.PARSE_ERROR, {"reason": parsed.get("reason", "")})]
        return [ReActEvent(ReActEventType.PARSE_SUCCESS, {"parsed": parsed})]

    def _on_parse_success(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """JSON_PARSE + PARSE_SUCCESS -> classify parsed data."""
        parsed = event.payload["parsed"]
        if parsed["type"] == "tool_call":
            return [ReActEvent(ReActEventType.CLASSIFIED_TOOL, {"parsed": parsed})]
        elif parsed["type"] == "answer":
            return [ReActEvent(ReActEventType.CLASSIFIED_ANSWER, {"parsed": parsed})]
        return [ReActEvent(ReActEventType.CLASSIFIED_ERROR, {"parsed": parsed})]

    def _on_parse_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """JSON_PARSE + PARSE_ERROR -> check retry gate."""
        return self._check_retry_gate(ctx, event.payload.get("reason", "JSON parse failed"))

    # ── CLASSIFY ──

    def _on_all_tools_done(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL + ALL_TOOLS_DONE -> inject observation analysis then continue."""
        if ctx.run_state.strategy == CallingStrategy.NATIVE_TOOLS:
            ctx.messages.append(
                {"role": "user",
                 "content": "请先用 Thought 分析以上工具返回的结果，判断信息是否充分，再决定下一步。"})
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_classified_tool(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_TOOL -> execute tool from JSON-parsed data."""
        parsed = event.payload["parsed"]
        thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning)
        return react_runtime.execute_json_tool_call(
            ctx,
            parsed=parsed,
            thought=thought,
            execute_tool=self._execute_tool,
            output=self._output,
        )

    def _on_classified_answer(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_ANSWER -> set final answer."""
        self._emit_answer_thought(ctx)
        ctx.last_answer = event.payload["parsed"]["text"]
        return []  # EMIT_ANSWER reads ctx.last_answer

    def _on_classified_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_ERROR -> check retry gate."""
        parsed = event.payload.get("parsed", {})
        return self._check_retry_gate(ctx, parsed.get("reason", "unknown"))

    # ── RETRY_GATE ──

    def _check_retry_gate(self, ctx: ExecutionContext, reason: str) -> list[ReActEvent]:
        """Shared logic: decide whether to retry, degrade, or fallback."""
        return react_runtime.retry_gate(ctx, reason)

    def _on_retries_left(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + RETRIES_LEFT -> increment retries, add hint, retry."""
        ctx.json_retries += 1
        reason = event.payload.get("reason", "")

        if reason == "empty_response":
            err_hint = ""
            if self.llm_client.last_error:
                err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})"
            self._output(
                f"[重试 {ctx.json_retries}/{ctx.max_json_retries}] LLM 返回空响应{err_hint}。提示给出答案...")
            ctx.messages.append(
                {"role": "user", "content": "请根据工具执行结果，直接给出清晰完整的最终答案。"})
        else:
            self._output(f"[JSON 重试 {ctx.json_retries}/{ctx.max_json_retries}] {reason}")
            raw = ctx.last_response_text
            truncated = (raw[:2000] + "\n...[响应截断]...") if len(raw) > 2000 else raw
            ctx.messages.append({"role": "assistant", "content": truncated})
            ctx.messages.append({"role": "user", "content":
                f"你的上一次回复不是合法的 JSON。错误: {reason}。\n"
                f"{self._build_json_format_section()}"})
            memory_manager = ctx.memory_state.memory_manager
            if memory_manager:
                memory_manager.append("assistant", ctx.last_response_text)
            if ctx.run_state.json_retries >= ctx.run_state.max_json_retries:
                if ctx.run_state.strategy == CallingStrategy.JSON_MODE:
                    ctx.memory_state.session_caps.mark_failed("json_mode")
                    return [ReActEvent(ReActEventType.DEGRADED)]

        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_no_retries_degrade(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + NO_RETRIES -> degrade from JSON_MODE."""
        ctx.memory_state.session_caps.mark_failed("json_mode")
        return [ReActEvent(ReActEventType.DEGRADED)]

    def _on_fallback_text(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + FALLBACK_TEXT -> salvage answer text before falling back to raw output."""
        step = ctx.steps[-1]
        step.error_message = f"JSON parse failed: {event.payload.get('reason', 'unknown')}"
        ctx.last_answer = self._extract_answer_from_text(ctx.last_response_text)
        return []  # EMIT_ANSWER reads ctx.last_answer

    # ── DEGRADE ──

    def _on_degraded(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """DEGRADE + DEGRADED -> re-select strategy, continue loop."""
        self._degrade_count += 1
        if self._degrade_count > 3:
            self._output("[策略降级] 超过最大降级次数，终止流程。")
            return [ReActEvent(ReActEventType.ABORT)]
        ctx.run_state.strategy = self._select_strategy(ctx.memory_state.session_caps)
        new_strategy = ctx.run_state.strategy.name
        self._output(f"[策略降级] → {new_strategy}")
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY,
                           {"strategy": new_strategy})]

    # ── Terminal states ──

    def _on_max_steps_abort(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """MAX_STEPS + ABORT -> output warning, terminate."""
        self._output("已达到最大步数，流程终止。")
        return []

    def _on_error_abort(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """ERROR_ABORT + ABORT -> terminate."""
        return []

    def _on_emit_answer(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EMIT_ANSWER -> output answer, save memory, conclude."""
        answer = ctx.last_answer
        run_state = ctx.run_state
        memory_state = ctx.memory_state
        tool_state = ctx.tool_state
        with trace_manager.span("final_answer", {
            "question": run_state.question[:200],
            "used_subagent": bool(tool_state.last_subagent_payload),
        }) as span:
            self._output(f"最终答案: {answer}")
            if memory_state.memory_manager:
                memory_state.memory_manager.append("system", f"[最终答案] {answer}")
                memory_state.memory_manager.conclude(run_state.question, answer)
            span.output = {
                "answer": str(answer)[:500],
                "subagent_answer": str((tool_state.last_subagent_payload or {}).get("answer", ""))[:500],
            }
            span.metadata = {
                "status": "ok",
                "used_subagent": bool(tool_state.last_subagent_payload),
                "subagent_status": (tool_state.last_subagent_payload or {}).get("status", ""),
                "subagent_role": (tool_state.last_subagent_payload or {}).get("role", ""),
                "subagent_recovery": (tool_state.last_subagent_payload or {}).get("recovery", None),
            }
        return []

    # ================================================================
    # Static / helper methods (unchanged from original)
    # ================================================================

    def _select_strategy(self, session_caps: SessionCapabilityTracker) -> CallingStrategy:
        caps = self.llm_client.capabilities
        if session_caps.is_available("tool_calling", caps.supports_tool_calling):
            return CallingStrategy.NATIVE_TOOLS
        elif session_caps.is_available("json_mode", caps.supports_json_mode):
            return CallingStrategy.JSON_MODE
        else:
            return CallingStrategy.PROMPT_JSON

    @staticmethod
    def _robust_json_parse(raw_text: str) -> dict:
        return json_helpers.robust_json_parse(raw_text)

    @staticmethod
    def _classify_parsed(data: dict) -> dict:
        return json_helpers.classify_parsed(data)

    @staticmethod
    def _try_fix_json(text: str) -> dict | None:
        return json_helpers.try_fix_json(text)

    @staticmethod
    def _normalize_jsonish_text(text: str) -> str:
        return json_helpers.normalize_jsonish_text(text)

    @staticmethod
    def _extract_answer_from_text(text: str) -> str:
        return json_helpers.extract_answer_from_text(text)

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        return json_helpers.parse_json_response(text)

    def _emit_answer_thought(self, ctx: ExecutionContext) -> None:
        if not any(step.tool_outputs for step in ctx.steps):
            return

        # Skip if reasoning_content was already streamed via STREAM_REASONING
        if any(step.reasoning_streamed for step in ctx.steps):
            return

        raw_text = (ctx.last_response_text or "").strip()
        if not raw_text:
            return

        thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning).strip()
        if not thought or thought == raw_text:
            return

        ctx.emit(ReActEventType.ANSWER_THOUGHT, thought=thought)

    @staticmethod
    def _select_visible_thought(response_text: str, reasoning_text: str) -> str:
        reasoning = (reasoning_text or "").strip()
        if reasoning:
            return reasoning

        text = (response_text or "").strip()
        if not text:
            return ""

        parsed = ReActAgent._try_fix_json(text)
        if isinstance(parsed, dict):
            thought = str(parsed.get("thought", "")).strip()
            if thought:
                return thought
            if "tool" in parsed or "answer" in parsed or "params" in parsed:
                return ""

        return text

    @staticmethod
    def _build_json_format_section() -> str:
        return build_json_format_section()

    def _default_confirm(self, code: str) -> bool:
        self._output(f"[警告] 即将执行代码 (预览): {code}")
        try:
            response = input("确认执行? [y/N] ").strip().lower()
            return response == "y"
        except (EOFError, OSError):
            return False

    def _build_prompt(self, tools_desc: str, question: str, history_str: str,
                       memory_context: str, conversation_context: str) -> str:
        compiled = self._compiled_session_profile
        template = compiled.prompt_template if compiled else self._react_template
        todo_context = self._todo_list.format_context() if self._todo_list else ""
        return build_react_prompt(
            template=template,
            tools_desc=tools_desc,
            question=question,
            history_str=history_str,
            memory_context=memory_context,
            conversation_context=conversation_context,
            available_skill_context=self._available_skill_context,
            mcp_context=self._mcp_context,
            compiled_profile=compiled,
            todo_context=todo_context,
        )

    def _build_messages(self, tools_desc: str, question: str,
                         memory_context: str, conversation_context: str,
                         workflow_context: str = "") -> list[dict[str, str]]:
        """Build messages array with stable prefix for prompt caching."""
        compiled = self._compiled_session_profile
        todo_context = self._todo_list.format_context() if self._todo_list else ""
        return build_react_messages(
            system_rules=self._react_template.split("== 可用工具 ==")[0].rstrip(),
            tools_desc=tools_desc,
            question=question,
            memory_context=memory_context,
            conversation_context=conversation_context,
            available_skill_context=self._available_skill_context,
            mcp_context=self._mcp_context,
            compiled_profile=compiled,
            todo_context=todo_context,
            workflow_context=workflow_context,
        )

    def _build_conversation_context(self, memory_manager, per_msg_limit: int = 500) -> str:
        return build_conversation_context(memory_manager, per_msg_limit=per_msg_limit)

    def _execute_tool(self, name: str, arguments: dict) -> str:
        policy = self._compiled_session_profile.tool_policy if self._compiled_session_profile else None
        return execute_tool(
            tool_executor=self.tool_executor,
            name=name,
            arguments=arguments,
            caller=self.agent_id,
            hitl_approver=self._confirm,
            tool_policy=policy,
            cancel_checker=self._cancel_checker,
        )
