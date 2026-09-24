"""ReActAgent - event-driven FSM implementation.

Thought -> Action -> Observation loop driven by a transfer-table state machine.
Each decision point is an explicit state; each transition is a handler method.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from agentnexus.agents import decisions, json_helpers, react_runtime
from agentnexus.agents.exceptions import AgentCancelled
from agentnexus.agents.fsm import StateMachine
from agentnexus.agents.llm_strategy import build_json_format_section, call_llm
from agentnexus.agents.prompt_builder import (
    assemble_react_messages,
    build_conversation_context,
    build_react_sections,
)
from agentnexus.agents.react_transitions import TRANSFER_TABLE
from agentnexus.agents.react_types import (
    AgentStep,
    CallingStrategy,
    ExecutionContext,
    ReActEvent,
    ReActEventType,
    ReActResult,
    RetryReason,
)
from agentnexus.agents.runtime_context import build_environment_block, load_project_instructions
from agentnexus.agents.tool_runner import execute_tool
from agentnexus.core.capabilities import SessionCapabilityTracker
from agentnexus.core.config import get_settings
from agentnexus.core.llm import AgentLLM
from agentnexus.observability.drift_detector import DriftDetector
from agentnexus.observability.tracer import trace_manager

if TYPE_CHECKING:
    from agentnexus.tools.errors import ToolError
from agentnexus.prompts import load_prompt
from agentnexus.skills import (
    CompiledSessionProfile,
    SessionProfile,
    compile_persona_fragment,
    load_core_fragments,
    validate_session_profile,
)
from agentnexus.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── FSM redesign Step 1: decision logic + shared constants live in
#    agentnexus.agents.decisions (pure functions).  Names re-exported here
#    for backward compatibility with internal call sites. ──
_split_native_thought_answer = decisions.split_native_thought_answer
_THOUGHT_MARKER_RE = decisions._THOUGHT_MARKER_RE
_BOOKKEEPING_TOOLS = decisions._BOOKKEEPING_TOOLS
_TERMINAL_TEXT_MIN_CHARS = decisions._TERMINAL_TEXT_MIN_CHARS

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
        # 决策3（2026-09-24 拍板）：默认不设步数上限。
        # max_steps=None 表示无限（跑飞兜底见 docs/fsm-redesign-proposal.md §9，
        # 靠闭环检测 + 软提示 + 用户取消，不再靠硬性步数终止）。
        self.max_steps = max_steps
        self._output = output or print
        self._confirm = confirm_fn or self._default_confirm
        self._async_confirm = async_confirm
        self.conversation_mode = conversation_mode
        self.agent_id = agent_id
        self._total_usage: dict = {"input_tokens": 0, "output_tokens": 0}
        self._step_count: int = 0
        self._on_event: Callable | None = None
        self._session_profile: SessionProfile | None = None
        self._compiled_session_profile: CompiledSessionProfile | None = None
        self._available_skill_context: str = ""
        self._mcp_context: str = ""
        self._workflow_context: str = ""
        self._cancel_checker: Callable[[], bool] | None = None
        self._todo_list = None  # Set externally after construction
        self._degrade_count = 0
        # Persona and behavioral fragments — loaded once, stable across sessions
        settings = get_settings()
        self._persona_text: str = compile_persona_fragment(settings.persona)
        self._behavior_fragments_text: str = load_core_fragments()
        # User-defined appendix injected at the very end of the system
        # context — highest-priority user instruction surface, still
        # subordinate to the safety rules in the fixed rules prefix.
        self._append_system_prompt: str = (settings.append_system_prompt or "").strip()

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
        # Forward to the LLM client so its watcher can close an in-flight
        # stream immediately on cancel (产品决策: 取消 = 立刻停止).
        if hasattr(self.llm_client, "set_cancel_checker"):
            self.llm_client.set_cancel_checker(checker)

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
        self._step_count = 0
        self._degrade_count = 0

        # ── user prompt submit hook (root agent only; subagent task text
        #    is internal, not a user prompt) ─────────────────────
        is_subagent = self.agent_id.startswith("subagent_")
        if not is_subagent:
            prompt_ctx = hook_mgr.fire(HookType.USER_PROMPT_SUBMIT, {
                "prompt": question,
                "agent_id": self.agent_id,
            })
            if prompt_ctx.aborted:
                refusal = f"[已拒绝] {prompt_ctx.abort_reason or '用户输入被钩子拦截'}"
                hook_mgr.fire(HookType.AGENT_END, {
                    "answer": refusal, "steps": 0, "agent_id": self.agent_id,
                })
                return ReActResult(answer=refusal, steps=[])
            question = prompt_ctx.payload.get("prompt", question)
        self._drift_detector = DriftDetector(
            original_goal=question,
            # DriftDetector 用 max_steps 算子任务超支比例；None（不设上限）
            # 时传 0，其内部 `<= 0` 守卫会跳过该检查（drift_detector.py:237）
            max_steps=self.max_steps if isinstance(self.max_steps, int) else 0,
        )

        # Publish the cancel checker so subagent tool closures (running on
        # dispatcher lane threads) stop at their next step boundary when the
        # parent run is cancelled. Note: sessions sharing one executor share
        # this bridge — the last run to start wins.
        cancel_bridge = getattr(self.tool_executor, "cancel_bridge", None)
        if cancel_bridge is not None:
            cancel_bridge.set_checker(self._cancel_checker)

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
            self._step_count = len(steps)
        except KeyboardInterrupt:
            answer = "[Agent execution cancelled by user]"
            steps = []
            self._total_usage = {"input_tokens": 0, "output_tokens": 0}
            self._step_count = 0

        # ── agent end hook ───────────────────────────────────────
        hook_mgr.fire(HookType.AGENT_END, {
            "answer": answer,
            "steps": len(steps),
            "agent_id": self.agent_id,
        })

        if cancel_bridge is not None:
            cancel_bridge.set_checker(None)

        return ReActResult(answer=answer, steps=steps)

    def _get_handlers(self) -> dict:
        """Return mapping of handler_name -> bound method for the FSM engine."""
        return {
            "_on_init": self._on_init,
            "_on_round": self._on_round,
            "_on_round_advance": self._on_round_advance,
            "_on_tools_requested": self._on_tools_requested,
            "_on_answer_ready": self._on_answer_ready,
            "_on_recover": self._on_recover,
            "_on_vetoed": self._on_vetoed,
            "_on_error_abort": self._on_error_abort,
            "_on_emit_answer": self._on_emit_answer,
        }

    # ================================================================
    # FSM Handlers — each returns list[ReActEvent] for the event queue
    # ================================================================

    def _on_init(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """INIT + START -> build prompts, messages, memory; select strategy."""
        # Session-scoped capability tracker owned by the LLM client: degrade
        # marks survive across runs and reset on model hot-switch (configure()).
        session_caps = self.llm_client.session_tracker
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
            memory_state.conv_ctx = self._build_conversation_context(memory_manager)

        # Build messages with stable prefix for prompt caching
        ctx.messages = self._build_messages(
            tool_state.tools_desc,
            run_state.question,
            memory_state.memory_context,
            memory_state.conv_ctx,
            workflow_context=self._workflow_context,
            native_tools=(run_state.strategy == CallingStrategy.NATIVE_TOOLS),
        )
        ctx.initial_count = len(ctx.messages)

        if memory_manager:
            def rebuild():
                profile = self._compiled_session_profile
                policy = profile.tool_policy if profile else None
                new_tools_desc = self.tool_executor.get_available_tools(self.agent_id, tool_policy=policy)
                new_conv = ""
                if self.conversation_mode:
                    new_conv = self._build_conversation_context(memory_manager)
                    memory_state.conv_ctx = new_conv
                # Rebuild messages with stable prefix structure
                new_messages = self._build_messages(
                    new_tools_desc,
                    run_state.question,
                    memory_state.memory_context,
                    new_conv,
                    workflow_context=self._workflow_context,
                    native_tools=(run_state.strategy == CallingStrategy.NATIVE_TOOLS),
                )
                # Replace exactly the initial block; keep accumulated messages after it
                ctx.messages[:ctx.initial_count] = new_messages
                ctx.initial_count = len(new_messages)
            memory_manager.on_after_compact = rebuild

        return []  # 落点 AWAIT_MODEL，由 auto-advance 触发第一轮 _on_round

    # ── AWAIT_MODEL 循环体 ──

    def _on_round(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """一轮模型往返 + 解释成决策事件。

        返回封闭于 {TOOLS_REQUESTED, ANSWER_READY, FAULT, ROUND_READY}，
        与 TRANSFER_TABLE 的 AWAIT_MODEL 行一一对应（totality 由测试断言）。
        """
        # 步数上限（决策3：默认 None 不设上限；配置时软收尾给出诚实答案，不强制）
        if (ctx.run_state.max_steps is not None
                and ctx.run_state.current_step >= ctx.run_state.max_steps):
            self._output("已达到最大步数，流程终止。")
            if not ctx.last_answer:
                ctx.last_answer = (
                    f"（已达到最大步数 {ctx.run_state.max_steps}，共执行 "
                    f"{ctx.run_state.current_step} 步，任务未完成。"
                    "请缩小问题范围或分步提问。）"
                )
            return [ReActEvent(ReActEventType.ANSWER_READY)]

        # 关闭上一步的 plan_node span（如果存在）
        prev_span = ctx.run_state._current_step_span
        if prev_span is not None:
            try:
                trace_ctx = trace_manager.active
                if trace_ctx:
                    trace_ctx.end_span(prev_span, metadata={"status": "ok", "step_type": "plan"})
            except Exception:
                pass
            ctx.run_state._current_step_span = None

        ctx.run_state.current_step += 1

        # 创建当前步骤的 plan_node span
        trace_ctx = trace_manager.active
        if trace_ctx:
            step_span = trace_ctx.start_span("plan_node", {
                "step_index": ctx.run_state.current_step,
                "strategy": ctx.run_state.strategy.name,
                "question_preview": ctx.run_state.question[:200],
            })
            ctx.run_state._current_step_span = step_span

        # 每 3 步执行一次漂移检测
        if ctx.run_state.current_step > 0 and ctx.run_state.current_step % 3 == 0:
            drift_signals = self._drift_detector.check(
                step_index=ctx.run_state.current_step,
                current_goal=ctx.run_state.question[:200],
            )
            if drift_signals:
                # 记录漂移信号到 trace
                if trace_ctx:
                    with trace_ctx._lock:
                        for signal in drift_signals:
                            logger.warning(
                                "[漂移检测] step=%d type=%s severity=%s: %s",
                                signal.step_index, signal.signal_type.value,
                                signal.severity.value, signal.detail,
                            )
                # 如果检测到 critical 漂移，注入提示让 Agent 重新聚焦
                critical = [s for s in drift_signals if s.severity.value == "critical"]
                if critical:
                    try:
                        from agentnexus.observability.alerting import emit_drift_alerts

                        emit_drift_alerts(critical, trace_id=getattr(trace_ctx, "trace_id", "") or "")
                    except Exception as alert_exc:
                        logger.debug("Drift alert emit failed: %s", alert_exc)
                    ctx.messages.append({
                        "role": "user",
                        "content": f"[系统提示] 检测到任务可能偏离原始目标。原始目标: {self._drift_detector.original_goal[:200]}。请重新聚焦于原始目标。",
                    })

        streamed_reasoning = {"flag": False}

        def _stream_token(token: str, is_reasoning: bool = False):
            # 检查取消信号——流式输出期间也能响应 ESC 中断
            checker = ctx.run_state.cancel_checker
            if checker is not None and checker():
                raise AgentCancelled("cancelled")
            if is_reasoning:
                streamed_reasoning["flag"] = True
                ctx.emit(ReActEventType.STREAM_REASONING, token=token)
            else:
                ctx.emit(ReActEventType.STREAM_TOKEN, token=token)

        response_text = call_llm(
            self.llm_client,
            ctx,
            json_format_section=self._build_json_format_section(),
            on_token=_stream_token,
        )

        if self.llm_client.last_error and not response_text:
            err = self.llm_client.last_error
            self._output(f"错误: {err}")
            return [ReActEvent(ReActEventType.FAULT,
                               {"fatal": True, "detail": err})]

        # ── 记录本轮 step（替代旧 RECEIVE_RESPONSE 态的 record_llm_response）──
        # reasoning_streamed 必须反映"是否真流式展示过"，而不是"有没有 reasoning
        # 内容"——否则未流式时 ANSWER_THOUGHT 补展示路径会被永久压死。
        step = AgentStep(
            step_id=ctx.run_state.current_step,
            strategy_used=ctx.run_state.strategy,
            reasoning_content=self.llm_client.last_reasoning_content,
            reasoning_streamed=streamed_reasoning["flag"],
            content=response_text,
        )
        ctx.steps.append(step)
        cur = getattr(self.llm_client, "last_usage", {})
        if isinstance(cur, dict):
            ctx._total_usage["input_tokens"] += cur.get("input_tokens", 0)
            ctx._total_usage["output_tokens"] += cur.get("output_tokens", 0)
        if ctx.memory_state.memory_manager:
            ctx.memory_state.memory_manager.mark_api_call()
        ctx.last_response_text = response_text
        ctx.last_reasoning = self.llm_client.last_reasoning_content or ""

        # ── 解释成决策（decisions.py 纯函数）──
        if ctx.run_state.strategy == CallingStrategy.NATIVE_TOOLS:
            d = decisions.interpret_native(
                response_text=ctx.last_response_text,
                reasoning_text=ctx.last_reasoning,
                tool_calls=self.llm_client.last_tool_calls,
                truncated=self.llm_client.last_truncated,
                streamed=any(s.reasoning_streamed for s in ctx.steps),
                tool_exists=lambda name: self.tool_executor.get_tool(name) is not None,
            )
        else:
            d = decisions.interpret_json(
                response_text=ctx.last_response_text,
                truncated=self.llm_client.last_truncated,
            )

        # ── fault → RECOVER ──
        if d.kind == "fault":
            if d.fail_pending_calls:
                # 截断 + 待执行调用：整批标错、不执行，直接再来一轮
                # （原 _fail_truncated_tool_calls → ALL_TOOLS_DONE → 下一轮的语义）
                return self._fail_truncated_tool_calls(ctx)
            return [ReActEvent(ReActEventType.FAULT, {
                "reason": d.reason,
                "detail": d.detail,
                "no_tools_no_text": d.no_tools_no_text,
            })]

        # ── answer → ANSWER ──
        if d.kind == "answer":
            memory_manager = ctx.memory_state.memory_manager
            if d.recovered_protocol_json or ctx.run_state.strategy != CallingStrategy.NATIVE_TOOLS:
                # JSON 协议答案 / 协议 JSON 还原：沿用旧 _on_classified_answer 的思考展示
                self._emit_answer_thought(ctx)
            elif d.persist_reasoning:
                # 推理已通过流式逐 token 展示；只把推理落盘以便会话恢复。
                if memory_manager:
                    memory_manager.append("system", f"[思考过程] {d.persist_reasoning}")
            elif d.display_thought:
                # 思考与答案分通道/可拆分 → 补展示思考。
                if memory_manager:
                    memory_manager.append("assistant", d.display_thought,
                                          metadata={"display_only": True})
                ctx.emit(ReActEventType.ANSWER_THOUGHT, thought=d.display_thought)
            ctx.last_answer = d.text
            return [ReActEvent(ReActEventType.ANSWER_READY, {"text": d.text})]

        # ── tools → EXECUTE_TOOL ──
        if d.recovered_protocol_json:
            ctx.tool_state.pending_tool_calls = [{
                "id": f"recovered_{ctx.run_state.current_step}",
                "name": tc["name"],
                "arguments": tc["arguments"],
            } for tc in d.tool_calls]
            self._on_native_tool_calls(ctx, "")
        elif ctx.run_state.strategy == CallingStrategy.NATIVE_TOOLS:
            if d.terminal_answer is not None:
                # Fast path: bookkeeping-only batch carrying answer-grade text.
                ctx.run_state.terminal_answer = d.terminal_answer
            ctx.tool_state.pending_tool_calls = list(d.tool_calls)
            self._on_native_tool_calls(ctx, d.thought)
        else:
            ctx.tool_state.pending_tool_calls = [
                {"id": "", "name": tc["name"], "arguments": tc["arguments"]}
                for tc in d.tool_calls
            ]
            if not d.thought:
                d.thought = self._select_visible_thought(ctx.last_response_text,
                                                         ctx.last_reasoning)
        return [ReActEvent(ReActEventType.TOOLS_REQUESTED, {
            "tool_calls": list(ctx.tool_state.pending_tool_calls),
            "thought": d.thought,
            "terminal_answer": d.terminal_answer,
            "strategy": ctx.run_state.strategy.name,
        })]

    def _on_round_advance(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """TOOLS_DONE / ROUND_READY 的接收态：无需副作用，auto-advance 继续。"""
        return []

    # ── NATIVE_TOOLS path ──

    def _fail_truncated_tool_calls(self, ctx: ExecutionContext) -> list[ReActEvent]:
        """Fail every pending tool call from a truncated response (pi semantics).

        The assistant message with tool_calls is recorded, each call gets an
        error observation, and the loop re-enters the LLM so the model can
        re-issue with complete arguments. No tool is executed.
        """
        thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning)
        self._on_native_tool_calls(ctx, thought)
        error_text = (
            "工具调用未执行：模型响应达到输出长度上限，工具参数可能被截断。"
            "请用更短、完整的参数重新发起调用。"
        )
        for tc in list(ctx.pending_tool_calls):
            ctx.messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": error_text,
            })
            react_runtime.record_tool_done(ctx, {
                "name": tc["name"],
                "arguments": tc.get("arguments", {}),
                "result": "<truncated>",
                "id": tc.get("id", ""),
            })
        ctx.pending_tool_calls = []
        return [ReActEvent(ReActEventType.ROUND_READY)]

    def _on_native_tool_calls(self, ctx: ExecutionContext, thought: str = None):
        """Record tool_calls into step, append assistant message."""
        react_runtime.record_native_tool_calls(
            ctx,
            thought=thought or "",
            reasoning_content=self.llm_client.last_reasoning_content or "",
            output=self._output,
        )

    def _on_tools_requested(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL 落地：执行整批工具，发 TOOLS_DONE 或 ANSWER_READY（fast path）。

        返回封闭于 {TOOLS_DONE, ANSWER_READY}。
        """
        payload = event.payload
        thought = payload.get("thought", "")
        is_native = payload.get("strategy") == CallingStrategy.NATIVE_TOOLS.name
        # JSON 协议路径的 thinking 展示（原生路径已在 _on_native_tool_calls 输出）
        if thought and not is_native:
            self._output(f"思考: {thought}")
        # 漂移记录（旧 _on_tool_done 职责；batch 合并后逐调用记录）
        for tc in list(ctx.tool_state.pending_tool_calls):
            self._record_tool_drift(ctx, tc)
        react_runtime.execute_pending_tools_batch(
            ctx,
            registry=self.tool_executor,
            execute_tool=self._execute_tool,
            output=self._output,
        )
        ctx.run_state.json_retries = 0  # 成功的工具轮重置 JSON 重试预算
        terminal = ctx.run_state.terminal_answer
        if terminal:
            ctx.run_state.terminal_answer = None
            ctx.last_answer = terminal
            return [ReActEvent(ReActEventType.ANSWER_READY)]
        if is_native:
            ctx.messages.append(
                {"role": "user",
                 "content": "请先用 Thought 分析以上工具返回的结果，判断信息是否充分，再决定下一步。"})
        return [ReActEvent(ReActEventType.TOOLS_DONE)]

    def _record_tool_drift(self, ctx: ExecutionContext, tool_call: dict):
        """记录工具调用到漂移检测器（原 _on_tool_done 的职责）。"""
        tool_name = tool_call.get("name", "")
        if tool_name and hasattr(self, "_drift_detector"):
            import hashlib
            params_hash = hashlib.md5(
                str(tool_call.get("arguments", "")).encode()).hexdigest()[:8]
            self._drift_detector.record_step(
                step_index=ctx.run_state.current_step,
                tool_name=tool_name,
                params_hash=params_hash,
            )

    # ── Non-NATIVE (JSON) path ──
    # （Step 3 起 JSON 协议的 empty/parse/classify 全部收进 _on_round 的
    #   decisions.interpret_json，以下中间态 handler 已删除）

    def _on_answer_ready(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EXECUTE_TOOL + ANSWER_READY -> terminal answer already in ctx.last_answer."""
        reason = self._fire_agent_stop(ctx)
        if reason:
            return [ReActEvent(ReActEventType.STOP_VETOED, {"reason": reason})]
        return []  # EMIT_ANSWER handler will read ctx.last_answer

    # ── RECOVER ──

    def _on_recover(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """RECOVER 落地：统一的重试 / 降档 / 兜底 / 终止策略。

        合并了旧 RETRY_GATE / DEGRADE / ERROR_ABORT 三个状态的全部策略。
        返回封闭于 {ROUND_READY, ANSWER_READY, ABORT}。
        """
        payload = event.payload

        # ── 致命错误（LLM 调用失败等）→ 终止 ──
        if payload.get("fatal"):
            detail = payload.get("detail") or "LLM call failed"
            self._output(f"错误: {detail}")
            return [ReActEvent(ReActEventType.ABORT)]

        # ── 原生"无工具无文本" → 直接降档工具能力 ──
        if payload.get("no_tools_no_text"):
            ctx.memory_state.session_caps.mark_failed("tool_calling")
            return self._degrade_and_continue(ctx)

        reason = payload.get("reason")
        detail = payload.get("detail", "")

        # ── JSON 类故障：重试预算 → 降档 → 兜底提取 ──
        if reason in (RetryReason.EMPTY_RESPONSE, RetryReason.PARSE_ERROR,
                      RetryReason.CLASSIFY_ERROR, RetryReason.TRUNCATED):
            kind = decisions.retry_gate_decision(
                json_retries=ctx.run_state.json_retries,
                max_json_retries=ctx.run_state.max_json_retries,
                strategy=ctx.run_state.strategy,
            )
            if kind == "round":
                ctx.json_retries += 1
                self._emit_retry_nudge(ctx, reason, detail)
                return [ReActEvent(ReActEventType.ROUND_READY)]
            if kind == "degrade":
                ctx.memory_state.session_caps.mark_failed("json_mode")
                return self._degrade_and_continue(ctx)
            # salvage：从原文里硬提取答案，交 ANSWER 态统一处理
            # （stop hook 否决在 ANSWER 落点的 _on_answer_ready 里执行；
            #   这里不能直接发 ANSWER_VETOED——RECOVER 没有这一行会 FSMError）
            if ctx.steps:
                ctx.steps[-1].error_message = f"JSON parse failed: {detail or reason}"
            ctx.last_answer = self._extract_answer_from_text(ctx.last_response_text)
            return [ReActEvent(ReActEventType.ANSWER_READY)]

        logger.warning("RECOVER got unhandled fault payload: %s", payload)
        return [ReActEvent(ReActEventType.ABORT)]

    def _emit_retry_nudge(self, ctx: ExecutionContext, reason: RetryReason, detail: str):
        """重试时向对话注入提示（保留旧 _on_retries_left 的分原因文案）。"""
        if reason == RetryReason.EMPTY_RESPONSE:
            err_hint = ""
            if self.llm_client.last_error:
                err_hint = f" (LLM last_error: {self.llm_client.last_error[:200]})"
            self._output(
                f"[重试 {ctx.json_retries}/{ctx.max_json_retries}] LLM 返回空响应{err_hint}。提示给出答案...")
            ctx.messages.append(
                {"role": "user", "content": "请根据工具执行结果，直接给出清晰完整的最终答案。"})
        elif reason == RetryReason.TRUNCATED:
            self._output(
                f"[截断重试 {ctx.json_retries}/{ctx.max_json_retries}] 上一次回复达到输出长度上限")
            ctx.messages.append(
                {"role": "user", "content": "你的上一次回复被输出长度上限截断。请缩短本次输出后重试。"})
        else:
            self._output(f"[JSON 重试 {ctx.json_retries}/{ctx.max_json_retries}] {detail or reason}")
            raw = ctx.last_response_text
            truncated = (raw[:2000] + "\n...[响应截断]...") if len(raw) > 2000 else raw
            ctx.messages.append({"role": "assistant", "content": truncated})
            ctx.messages.append({"role": "user", "content":
                f"你的上一次回复不是合法的 JSON。错误: {detail or reason}。\n"
                f"{self._build_json_format_section()}"})
            memory_manager = ctx.memory_state.memory_manager
            if memory_manager and ctx.last_response_text:
                # Skip empty appends — a blank assistant row renders as an
                # empty thinking card in history (and pollutes the journal).
                memory_manager.append("assistant", ctx.last_response_text)

    def _degrade_and_continue(self, ctx: ExecutionContext) -> list[ReActEvent]:
        """降档协议档位并继续（原 DEGRADE 态 + _on_degraded/_on_no_retries_degrade）。"""
        self._degrade_count += 1
        if self._degrade_count > 3:
            self._output("[策略降级] 超过最大降级次数，终止流程。")
            return [ReActEvent(ReActEventType.ABORT)]
        ctx.run_state.strategy = self._select_strategy(ctx.memory_state.session_caps)
        new_strategy = ctx.run_state.strategy.name
        self._output(f"[策略降级] → {new_strategy}")
        if ctx.run_state.strategy != CallingStrategy.NATIVE_TOOLS:
            # The initial block was built without the text tool list (native
            # schemas carried it). JSON strategies read tool names/params from
            # that list, so rebuild the initial block to inject it.
            new_messages = self._build_messages(
                ctx.tool_state.tools_desc,
                ctx.run_state.question,
                ctx.memory_state.memory_context,
                ctx.memory_state.conv_ctx,
                workflow_context=self._workflow_context,
                native_tools=False,
            )
            ctx.messages[:ctx.initial_count] = new_messages
            ctx.initial_count = len(new_messages)
        return [ReActEvent(ReActEventType.ROUND_READY, {"strategy": new_strategy})]

    # ── Terminal states ──

    def _on_error_abort(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """ERROR_ABORT + ABORT -> terminate."""
        return []

    def _fire_agent_stop(self, ctx: ExecutionContext) -> str | None:
        """AGENT_STOP hook: return the veto reason, or None to allow emission.

        Called by every EMIT_ANSWER producer (the returned STOP_VETOED event
        is looked up in the EMIT_ANSWER landing state — see TRANSFER_TABLE).
        Consecutive vetoes are capped so a misconfigured hook cannot loop
        forever (max_steps is the second backstop).
        """
        run_state = ctx.run_state
        if run_state.stop_vetoes >= 2:
            logger.warning("AGENT_STOP hook vetoed twice — allowing answer to emit")
            return None
        from agentnexus.core.hooks import HookType, get_hook_manager

        stop_ctx = get_hook_manager().fire(HookType.AGENT_STOP, {
            "answer": ctx.last_answer or "",
            "steps": run_state.current_step,
            "question": run_state.question,
            "agent_id": self.agent_id,
        })
        if not stop_ctx.aborted:
            return None
        run_state.stop_vetoes += 1
        reason = stop_ctx.abort_reason or "AGENT_STOP hook vetoed the answer"
        self._output(f"[agent_stop 拦截 {run_state.stop_vetoes}/2] {reason}")
        return reason

    def _on_emit_answer(self, ctx: ExecutionContext, _event: ReActEvent) -> list[ReActEvent]:
        """EMIT_ANSWER -> output answer, save memory, conclude."""
        # 关闭最后一个 plan_node span
        prev_span = ctx.run_state._current_step_span
        if prev_span is not None:
            try:
                trace_ctx = trace_manager.active
                if trace_ctx:
                    trace_ctx.end_span(prev_span, metadata={"status": "ok", "step_type": "plan"})
            except Exception:
                pass
            ctx.run_state._current_step_span = None

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

    def _on_vetoed(self, ctx: ExecutionContext, event: ReActEvent) -> list[ReActEvent]:
        """ANSWER + ANSWER_VETOED -> 注入钩子反馈，落回 AWAIT_MODEL 由 auto-advance 进下一轮。"""
        reason = event.payload.get("reason", "")
        ctx.messages.append({"role": "user", "content": (
            f"你的最终答案被 agent_stop 钩子拦截：{reason}\n"
            "请针对拦截原因修正或补充你的回答，然后重新给出最终答案。"
        )})
        return []

    # ================================================================
    # Static / helper methods (unchanged from original)
    # ================================================================

    def _select_strategy(self, session_caps: SessionCapabilityTracker) -> CallingStrategy:
        return decisions.select_strategy(session_caps, self.llm_client.capabilities)

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
        # When reasoning was streamed, persist the reasoning_content to STM
        # so it survives session navigation (the streaming tokens are lost
        # when the user navigates away and back).
        if any(step.reasoning_streamed for step in ctx.steps):
            reasoning = ctx.last_reasoning or ""
            if reasoning:
                memory_manager = ctx.memory_state.memory_manager
                if memory_manager:
                    memory_manager.append("system", f"[思考过程] {reasoning}")
            return

        raw_text = (ctx.last_response_text or "").strip()
        if not raw_text:
            return

        thought = self._select_visible_thought(ctx.last_response_text, ctx.last_reasoning).strip()
        if not thought or thought == raw_text:
            return

        memory_manager = ctx.memory_state.memory_manager
        if memory_manager:
            memory_manager.append("assistant", thought, metadata={"display_only": True})
        ctx.emit(ReActEventType.ANSWER_THOUGHT, thought=thought)

    @staticmethod
    def _select_visible_thought(response_text: str, reasoning_text: str) -> str:
        return decisions.select_visible_thought(response_text, reasoning_text)

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

    def _build_messages(self, tools_desc: str, question: str,
                         memory_context: str, conversation_context: str,
                         workflow_context: str = "",
                         native_tools: bool = False) -> list[dict[str, str]]:
        """Build messages array with stable prefix for prompt caching."""
        compiled = self._compiled_session_profile
        todo_context = self._todo_list.format_context() if self._todo_list else ""
        # Merge agent-level persona and behavior fragments into conversation context
        extra_blocks: list[str] = []
        if self._persona_text:
            extra_blocks.append(self._persona_text)
        if self._behavior_fragments_text:
            extra_blocks.append(self._behavior_fragments_text)
        merged_conversation = conversation_context
        if extra_blocks:
            suffix = "\n\n".join(extra_blocks)
            merged_conversation = (
                conversation_context + "\n\n" + suffix if conversation_context else suffix
            )
        sections = build_react_sections(
            memory_context=memory_context,
            conversation_context=merged_conversation,
            available_skill_context=self._available_skill_context,
            mcp_context=self._mcp_context,
            compiled_profile=compiled,
            todo_context=todo_context,
            environment_context=build_environment_block(),
            project_instructions=load_project_instructions(),
            append_system_prompt=self._append_system_prompt,
        )
        return assemble_react_messages(
            system_rules=self._react_template.split("== 可用工具 ==")[0].rstrip(),
            tools_desc=tools_desc,
            sections=sections,
            question=question,
            workflow_context=workflow_context,
            include_tools_desc=not native_tools,
        )

    def _build_conversation_context(self, memory_manager) -> str:
        return build_conversation_context(memory_manager)

    def _execute_tool(self, name: str, arguments: dict) -> str | dict | ToolError:
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
