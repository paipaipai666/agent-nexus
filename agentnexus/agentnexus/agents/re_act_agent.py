"""ReActAgent — event-driven FSM implementation.

Thought → Action → Observation loop driven by a transfer-table state machine.
Each decision point is an explicit state; each transition is a handler method.
"""

import json
import re
from typing import Callable

from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActResult,
)
from agentnexus.core.capabilities import SessionCapabilityTracker
from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.prompts import load_prompt
from agentnexus.tools.tool_executor import ToolExecutor

REACT_PROMPT_TEMPLATE = load_prompt("react")

MAX_JSON_RETRIES = 2

# Re-export for backward compatibility
__all__ = ["ReActAgent", "CallingStrategy", "AgentStep"]


class ReActAgent:
    """Event-driven ReAct agent with transfer-table FSM.

    Constructor and public API remain backward-compatible.
    The run() method now delegates to a StateMachine with per-transition handlers.
    """

    _HITL_TOOLS = {"python_execute", "code_executor", "shell_exec"}

    def __init__(self, llm_client: AgentLLM, tool_executor: ToolExecutor,
                 max_steps: int | None = None,
                 output=None, confirm_fn=None, async_confirm=None,
                 conversation_mode: bool = False):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self._async_confirm = async_confirm
        self.conversation_mode = conversation_mode
        self._total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        self._on_event: Callable | None = None

    # ================================================================
    # Public API (unchanged)
    # ================================================================

    @property
    def total_usage(self) -> dict:
        return dict(self._total_usage)

    @property
    def model_id(self) -> str:
        return self.llm_client.model

    def run(self, question: str, memory_manager=None) -> ReActResult:
        """Thin entry point: build context, run FSM loop, return structured result."""
        self._total_usage = {"input_tokens": 0, "output_tokens": 0}

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
        ctx.thinking_enabled = self.llm_client.capabilities.supports_thinking

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
        return ReActResult(answer=answer, steps=steps)

    def _get_handlers(self) -> dict:
        """Return mapping of handler_name → bound method for the FSM engine."""
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
        """INIT + START → build prompts, messages, memory; select strategy."""
        session_caps = SessionCapabilityTracker()
        ctx.session_caps = session_caps
        ctx.strategy = self._select_strategy(session_caps)

        memory_manager = ctx.memory_manager
        if memory_manager:
            ctx.memory_context = memory_manager.init_session(ctx.question)
            memory_manager.append("user", ctx.question)

        ctx.tools = self.tool_executor.registry.to_openai_tools()
        ctx.tools_desc = self.tool_executor.getAvailableTools()

        if self.conversation_mode and memory_manager:
            ctx.conv_ctx = self._build_conversation_context(memory_manager, per_msg_limit=800)

        system_content = self._build_prompt(
            ctx.tools_desc, ctx.question, "", ctx.memory_context, ctx.conv_ctx)

        ctx.messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": ctx.question},
        ]

        if memory_manager:
            def rebuild():
                new_tools_desc = self.tool_executor.getAvailableTools()
                new_conv = ""
                if self.conversation_mode:
                    new_conv = self._build_conversation_context(memory_manager, per_msg_limit=800)
                    ctx.conv_ctx = new_conv
                new_system = self._build_prompt(
                    new_tools_desc, ctx.question, "", ctx.memory_context, new_conv)
                ctx.messages[0] = {"role": "system", "content": new_system}
            memory_manager._on_after_compact = rebuild

        if ctx.current_step >= ctx.max_steps:
            return [ReActEvent(ReActEventType.ABORT)]

        return [ReActEvent(ReActEventType.STRATEGY_READY,
                           {"strategy": ctx.strategy.name})]

    def _on_strategy_ready(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """SELECT_STRATEGY + STRATEGY_READY → advance to PREPARE_LLM_CALL."""
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_llm_params_ready(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """PREPARE_LLM_CALL + LLM_PARAMS_READY → set params, call LLM."""
        ctx.current_step += 1

        strategy = ctx.strategy
        if strategy == CallingStrategy.NATIVE_TOOLS:
            think_tools = ctx.tools
            think_rfmt = None
        elif strategy == CallingStrategy.JSON_MODE:
            think_tools = None
            think_rfmt = {"type": "json_object"}
        elif strategy == CallingStrategy.PROMPT_JSON:
            think_tools = None
            think_rfmt = None
            last_msg = ctx.messages[-1]
            if "== 输出格式" not in last_msg.get("content", ""):
                last_msg["content"] += "\n\n" + self._build_json_format_section()
        else:  # PLAIN_TEXT
            think_tools = None
            think_rfmt = None

        projection_fn = ctx.memory_manager.build_projection if ctx.memory_manager else None
        response_text = self.llm_client.think(
            messages=ctx.messages, tools=think_tools, response_format=think_rfmt,
            projection_fn=projection_fn, thinking=ctx.thinking_enabled)

        if self.llm_client.last_error and not response_text:
            return [ReActEvent(ReActEventType.LLM_ERROR,
                               {"error": self.llm_client.last_error})]

        return [ReActEvent(ReActEventType.LLM_RESPONSE,
                           {"response_text": response_text})]

    def _on_llm_response(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CALL_LLM + LLM_RESPONSE → record AgentStep, route to native or JSON path."""
        response_text = event.payload.get("response_text", "")

        step = AgentStep(
            step_id=ctx.current_step,
            strategy_used=ctx.strategy,
            reasoning_content=self.llm_client.last_reasoning_content,
            content=response_text,
        )
        ctx.steps.append(step)

        cur = getattr(self.llm_client, "last_usage", {})
        if isinstance(cur, dict):
            ctx._total_usage["input_tokens"] += cur.get("input_tokens", 0)
            ctx._total_usage["output_tokens"] += cur.get("output_tokens", 0)

        if ctx.memory_manager:
            ctx.memory_manager.mark_api_call()

        ctx.last_response_text = response_text
        ctx.last_reasoning = self.llm_client.last_reasoning_content or ""

        if ctx.strategy == CallingStrategy.NATIVE_TOOLS:
            tool_calls = self.llm_client.last_tool_calls
            if not isinstance(tool_calls, list):
                tool_calls = []
            ctx.pending_tool_calls = tool_calls
            return [ReActEvent(ReActEventType.ROUTE_NATIVE)]
        else:
            return [ReActEvent(ReActEventType.ROUTE_JSON)]

    def _on_llm_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CALL_LLM + LLM_ERROR → abort."""
        err = event.payload.get("error", "LLM call failed")
        self._output(f"错误: {err}")
        return [ReActEvent(ReActEventType.ABORT)]

    # ── NATIVE_TOOLS path ──

    def _on_receive_native(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """RECEIVE_RESPONSE + ROUTE_NATIVE → check tool_calls."""
        if ctx.pending_tool_calls:
            thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning)
            if not thought:
                ctx.json_retries = 0  # fresh retry budget for thought injection
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
            ctx.last_answer = text
            return [ReActEvent(ReActEventType.NO_TOOLS,
                               {"text": text})]

        return [ReActEvent(ReActEventType.NO_TOOLS_NO_TEXT)]

    def _on_native_tool_calls(self, ctx: ExecutionContext, thought: str = None):
        """Record tool_calls into step, append assistant message."""
        step = ctx.steps[-1]
        step.tool_calls = list(ctx.pending_tool_calls)
        self._output(f"思考: {thought}")
        ctx.emit(ReActEventType.TOOLS_FOUND,
                 thought=thought,
                 tool_calls=list(ctx.pending_tool_calls))
        if ctx.memory_manager:
            ctx.memory_manager.append("assistant", thought)

        assistant_msg: dict = {"role": "assistant", "content": thought}
        if self.llm_client.last_reasoning_content:
            assistant_msg["reasoning_content"] = self.llm_client.last_reasoning_content
        assistant_tool_calls = []
        for tc in ctx.pending_tool_calls:
            assistant_tool_calls.append({
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            })
        if assistant_tool_calls:
            assistant_msg["tool_calls"] = assistant_tool_calls
        ctx.messages.append(assistant_msg)

    def _on_tools_found(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + TOOLS_FOUND → start executing first tool."""
        return [self._exec_next_tool(ctx)]

    def _on_thought_missing(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + THOUGHT_MISSING → route to retry gate."""
        return self._check_retry_gate(ctx, event.payload.get("reason", "missing_thought"))

    def _on_tool_done(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL + TOOL_DONE → execute next tool or finish."""
        tc = event.payload
        step = ctx.steps[-1]
        step.tool_outputs.append({
            "tool": tc.get("name", ""),
            "output": tc.get("result", ""),
        })

        if ctx.memory_manager:
            ctx.memory_manager.append("tool",
                f"Action: {tc.get('name', '')}[{json.dumps(tc.get('arguments', {}), ensure_ascii=False)}]\n"
                f"Observation: {tc.get('result', '')}")
            if tc.get('name', '') in ('read', 'file_read', 'file_read_text'):
                filepath = tc.get('arguments', {}).get('file_path',
                            tc.get('arguments', {}).get('path', ''))
                if filepath:
                    ctx.memory_manager.bridge_read(str(filepath), str(tc.get('result', ''))[:5000])

        return [self._exec_next_tool(ctx)]

    def _exec_next_tool(self, ctx: ExecutionContext) -> ReActEvent:
        """Execute the next pending tool call. Emits TOOL_DONE or ALL_TOOLS_DONE."""
        if not ctx.pending_tool_calls:
            # All tools done — refresh memory, reset retries
            if ctx.memory_manager and ctx.memory_manager.has_new_memories():
                ctx.memory_context = ctx.memory_manager.refresh_ltm_context(ctx.question)
            ctx.json_retries = 0
            return ReActEvent(ReActEventType.ALL_TOOLS_DONE)

        tc = ctx.pending_tool_calls.pop(0)
        self._output(
            f"行动: {tc['name']}({', '.join(f'{k}={v}' for k, v in tc['arguments'].items())})")
        ctx.emit(ReActEventType.TOOL_START,
                 name=tc["name"], arguments=tc["arguments"])
        observation = self._execute_tool(tc["name"], tc["arguments"])
        self._output(f"观察: {observation}")

        # Append tool result to messages
        ctx.messages.append({
            "role": "tool",
            "tool_call_id": tc.get("id", ""),
            "content": str(observation),
        })

        return ReActEvent(ReActEventType.TOOL_DONE,
                          {"name": tc["name"], "arguments": tc["arguments"],
                           "result": observation, "id": tc.get("id", "")})

    def _on_no_tools_answer(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + NO_TOOLS → plain text is the final answer."""
        return []  # EMIT_ANSWER handler will read ctx.last_answer

    def _on_no_tools_degrade(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_TOOL_CALLS + NO_TOOLS_NO_TEXT → degrade from NATIVE_TOOLS."""
        ctx.session_caps.mark_failed("tool_calling")
        return [ReActEvent(ReActEventType.DEGRADED)]

    # ── Non-NATIVE (JSON) path ──

    def _on_receive_json(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """RECEIVE_RESPONSE + ROUTE_JSON → check if response is empty."""
        if not ctx.last_response_text:
            return [ReActEvent(ReActEventType.EMPTY_RESPONSE)]
        return [ReActEvent(ReActEventType.HAS_CONTENT)]

    def _on_empty_response(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_EMPTY + EMPTY_RESPONSE → retry or abort."""
        return self._check_retry_gate(ctx, "empty_response")

    def _on_has_content(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """CHECK_EMPTY + HAS_CONTENT → JSON parse the response."""
        parsed = self._robust_json_parse(ctx.last_response_text)
        if parsed["type"] == "error":
            ctx.last_answer = None  # signal parse error for retry gate
            return [ReActEvent(ReActEventType.PARSE_ERROR, {"reason": parsed.get("reason", "")})]
        return [ReActEvent(ReActEventType.PARSE_SUCCESS, {"parsed": parsed})]

    def _on_parse_success(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """JSON_PARSE + PARSE_SUCCESS → classify parsed data."""
        parsed = event.payload["parsed"]
        if parsed["type"] == "tool_call":
            return [ReActEvent(ReActEventType.CLASSIFIED_TOOL, {"parsed": parsed})]
        elif parsed["type"] == "answer":
            return [ReActEvent(ReActEventType.CLASSIFIED_ANSWER, {"parsed": parsed})]
        return [ReActEvent(ReActEventType.CLASSIFIED_ERROR, {"parsed": parsed})]

    def _on_parse_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """JSON_PARSE + PARSE_ERROR → check retry gate."""
        return self._check_retry_gate(ctx, event.payload.get("reason", "JSON parse failed"))

    # ── CLASSIFY ──

    def _on_all_tools_done(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL + ALL_TOOLS_DONE → inject observation analysis then continue."""
        ctx.messages.append(
            {"role": "user",
             "content": "请先用 Thought 分析以上工具返回的结果，判断信息是否充分，再决定下一步。"})
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_classified_tool(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_TOOL → execute tool from JSON-parsed data."""
        parsed = event.payload["parsed"]
        thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning)
        step = ctx.steps[-1]
        step.tool_calls = [{"name": parsed["tool"], "arguments": parsed["params"]}]

        if thought:
            self._output(f"思考: {thought}")
            ctx.emit(ReActEventType.TOOLS_FOUND,
                     thought=thought,
                     tool_calls=list(step.tool_calls))

        self._output(
            f"行动: {parsed['tool']}({', '.join(f'{k}={v}' for k, v in parsed['params'].items())})")
        ctx.emit(ReActEventType.TOOL_START,
                 name=parsed["tool"], arguments=parsed["params"])
        observation = self._execute_tool(parsed["tool"], parsed["params"])
        self._output(f"观察: {observation}")
        ctx.emit(ReActEventType.TOOL_DONE,
                 name=parsed["tool"], arguments=parsed["params"],
                 result=observation, id="")

        step.tool_outputs.append({"tool": parsed["tool"], "output": observation})

        ctx.messages.append({"role": "assistant", "content": ctx.last_response_text})
        ctx.messages.append({"role": "user", "content":
            f"工具执行结果:\n{observation}\n\n请根据结果继续。如果信息充分，输出最终答案。\n"
            f"格式: {{\"answer\": \"你的回答\"}}"})

        if ctx.memory_manager:
            ctx.memory_manager.append("assistant", ctx.last_response_text)
            ctx.memory_manager.append("tool",
                f"Action: {parsed['tool']}[{json.dumps(parsed['params'], ensure_ascii=False)}]\n"
                f"Observation: {observation}")
            if ctx.memory_manager.has_new_memories():
                ctx.memory_context = ctx.memory_manager.refresh_ltm_context(ctx.question)

        ctx.json_retries = 0
        return [ReActEvent(ReActEventType.ALL_TOOLS_DONE)]

    def _on_classified_answer(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_ANSWER → set final answer."""
        ctx.last_answer = event.payload["parsed"]["text"]
        return []  # EMIT_ANSWER reads ctx.last_answer

    def _on_classified_error(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """CLASSIFY + CLASSIFIED_ERROR → check retry gate."""
        parsed = event.payload.get("parsed", {})
        return self._check_retry_gate(ctx, parsed.get("reason", "unknown"))

    # ── RETRY_GATE ──

    def _check_retry_gate(self, ctx: ExecutionContext, reason: str) -> list[ReActEvent]:
        """Shared logic: decide whether to retry, degrade, or fallback."""
        if ctx.json_retries < ctx.max_json_retries:
            return [ReActEvent(ReActEventType.RETRIES_LEFT, {"reason": reason})]

        if ctx.strategy == CallingStrategy.JSON_MODE:
            return [ReActEvent(ReActEventType.NO_RETRIES, {"reason": reason})]
        else:
            return [ReActEvent(ReActEventType.FALLBACK_TEXT, {"reason": reason})]

    def _on_retries_left(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + RETRIES_LEFT → increment retries, add hint, retry."""
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
            ctx.messages.append({"role": "assistant", "content": ctx.last_response_text})
            ctx.messages.append({"role": "user", "content":
                f"你的上一次回复不是合法的 JSON。错误: {reason}。\n"
                f"{self._build_json_format_section()}"})
            if ctx.memory_manager:
                ctx.memory_manager.append("assistant", ctx.last_response_text)
            if ctx.json_retries >= ctx.max_json_retries:
                if ctx.strategy == CallingStrategy.JSON_MODE:
                    ctx.session_caps.mark_failed("json_mode")
                    return [ReActEvent(ReActEventType.DEGRADED)]

        return [ReActEvent(ReActEventType.LLM_PARAMS_READY)]

    def _on_no_retries_degrade(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + NO_RETRIES → degrade from JSON_MODE."""
        ctx.session_caps.mark_failed("json_mode")
        return [ReActEvent(ReActEventType.DEGRADED)]

    def _on_fallback_text(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RETRY_GATE + FALLBACK_TEXT → use raw text as final answer (PROMPT_JSON exhausted)."""
        step = ctx.steps[-1]
        step.error_message = f"JSON parse failed: {event.payload.get('reason', 'unknown')}"
        ctx.last_answer = ctx.last_response_text
        return []  # EMIT_ANSWER reads ctx.last_answer

    # ── DEGRADE ──

    def _on_degraded(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """DEGRADE + DEGRADED → re-select strategy, continue loop."""
        ctx.strategy = self._select_strategy(ctx.session_caps)
        new_strategy = ctx.strategy.name
        self._output(f"[策略降级] → {new_strategy}")
        return [ReActEvent(ReActEventType.LLM_PARAMS_READY,
                           {"strategy": new_strategy})]

    # ── Terminal states ──

    def _on_max_steps_abort(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """MAX_STEPS + ABORT → output warning, terminate."""
        self._output("已达到最大步数，流程终止。")
        return []

    def _on_error_abort(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """ERROR_ABORT + ABORT → terminate."""
        return []

    def _on_emit_answer(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EMIT_ANSWER → output answer, save memory, conclude."""
        answer = ctx.last_answer
        self._output(f"最终答案: {answer}")
        if ctx.memory_manager:
            ctx.memory_manager.append("system", f"[最终答案] {answer}")
            ctx.memory_manager.conclude(ctx.question, answer)
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
        if not raw_text or not raw_text.strip():
            return {"type": "error", "reason": "empty response"}
        clean = raw_text.strip()
        markdown_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', clean, re.DOTALL)
        if markdown_match:
            clean = markdown_match.group(1).strip()
        try:
            data = json.loads(clean)
            return ReActAgent._classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            fixed = re.sub(r',\s*([}\]])', r'\1', clean)
            data = json.loads(fixed)
            return ReActAgent._classify_parsed(data)
        except (json.JSONDecodeError, ValueError):
            pass
        data = ReActAgent._try_fix_json(clean)
        if data:
            return ReActAgent._classify_parsed(data)
        return {"type": "error", "reason": "JSON parse failed after all repair attempts",
                "raw": raw_text[:500]}

    @staticmethod
    def _classify_parsed(data: dict) -> dict:
        if not isinstance(data, dict):
            return {"type": "error", "reason": "JSON is not an object"}
        if "tool" in data and "params" in data:
            tool = str(data["tool"])
            params = data["params"] if isinstance(data["params"], dict) else {}
            return {"type": "tool_call", "tool": tool, "params": params}
        if "answer" in data:
            return {"type": "answer", "text": str(data["answer"])}
        if len(data) == 1:
            key = next(iter(data))
            return {"type": "answer", "text": str(data[key])}
        return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}

    @staticmethod
    def _try_fix_json(text: str) -> dict | None:
        if not text:
            return None
        s = text.strip()
        start = s.find("{")
        if start == -1:
            return None
        depth = 0
        end = -1
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            s = s + "}"
            end = len(s) - 1
        candidate = s[start:end + 1]
        candidate = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _parse_json_response(text: str) -> dict:
        if not text or not text.strip():
            return {"type": "error", "reason": "empty response"}
        data = ReActAgent._try_fix_json(text)
        if not data:
            return {"type": "error", "reason": "not valid JSON"}
        if not isinstance(data, dict):
            return {"type": "error", "reason": "JSON is not an object"}
        if "tool" in data and "params" in data:
            tool = str(data["tool"])
            params = data["params"] if isinstance(data["params"], dict) else {}
            return {"type": "tool_call", "tool": tool, "params": params}
        if "answer" in data:
            return {"type": "answer", "text": str(data["answer"])}
        if len(data) == 1:
            key = next(iter(data))
            return {"type": "answer", "text": str(data[key])}
        return {"type": "error", "reason": "JSON missing 'tool' or 'answer' key"}

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
        return (
            "== 输出格式（严格遵守）==\n"
            "你必须在每次回复中输出合法的 JSON 对象。\n\n"
            "调用工具时:\n"
            '{"thought": "1-3句简洁分析，说明意图和依据", "tool": "工具名", "params": {"参数名": "值", ...}}\n\n'
            "给出最终答案时:\n"
            '{"answer": "你的完整回答"}\n\n'
            "答案中的换行用 \\n 表示，双引号用 \\\" 转义。"
        )

    def _default_confirm(self, code: str) -> bool:
        self._output(f"[警告] 即将执行代码 (预览): {code}")
        try:
            response = input("确认执行? [y/N] ").strip().lower()
            return response == "y"
        except (EOFError, OSError):
            return True

    def _build_prompt(self, tools_desc: str, question: str, history_str: str,
                       memory_context: str, conversation_context: str) -> str:
        return REACT_PROMPT_TEMPLATE.format(
            tools=tools_desc,
            question=question,
            history=history_str,
            memory_context=memory_context,
            conversation_context=conversation_context,
        )

    def _build_conversation_context(self, memory_manager, per_msg_limit: int = 500) -> str:
        if not memory_manager or not memory_manager.short_term:
            return ""
        stm = memory_manager.short_term
        summary = stm.get_summary()
        messages = stm.get_all()
        user_assistant_msgs = [m for m in messages if m["role"] in ("user", "assistant")]
        if summary:
            recent = user_assistant_msgs[-3:] if len(user_assistant_msgs) > 3 else user_assistant_msgs
            parts = ["== 对话历史摘要 ==", summary]
            if recent:
                parts.append("\n== 最近对话 ==")
                for m in recent:
                    role_label = "用户" if m["role"] == "user" else "助手"
                    content = m["content"][:per_msg_limit]
                    parts.append(f"{role_label}: {content}")
            return "\n".join(parts) + "\n\n"
        if not user_assistant_msgs:
            return ""
        recent = user_assistant_msgs[-6:]
        lines = []
        for m in recent:
            role_label = "用户" if m["role"] == "user" else "助手"
            content = m["content"][:per_msg_limit]
            lines.append(f"{role_label}: {content}")
        return "== 近期对话 ==\n" + "\n".join(lines) + "\n\n"

    def _execute_tool(self, name: str, arguments: dict) -> str:
        need_hitl = name in self._HITL_TOOLS
        try:
            return str(self.tool_executor.registry.invoke(
                name=name,
                params=arguments,
                caller="react_agent",
                hitl_approver=self._confirm if need_hitl else None,
            ))
        except Exception as e:
            return f"错误: 工具 '{name}' 执行失败: {e}"
