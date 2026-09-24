"""ReActAgent FSM types: states, events, context, and transition table entries."""

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, NamedTuple

# ============================================================
# CallingStrategy — how the agent communicates with the LLM
# ============================================================

class CallingStrategy(Enum):
    NATIVE_TOOLS = auto()   # tools → LLM native tool_calls (tier 1)
    JSON_MODE = auto()      # response_format={"type":"json_object"} + text parse (tier 2)
    PROMPT_JSON = auto()    # prompt instructs JSON format + text parse (tier 3)
    PLAIN_TEXT = auto()     # pure natural language, no structured output (tier 4)


# ============================================================
# AgentStep — single ReAct decision step audit entity
# ============================================================

@dataclass
class AgentStep:
    step_id: int
    strategy_used: CallingStrategy = CallingStrategy.NATIVE_TOOLS
    reasoning_content: str = ""
    reasoning_streamed: bool = False
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)
    error_message: str | None = None


# ============================================================
# ReActState — FSM states（FSM 重设计 Step 3，2026-09-24）
#
# 6 个状态，只做真正的决策点；流水线步骤（准备参数、解析 JSON 等）
# 全部收进 handler / decisions.py，不再占状态位。
# ============================================================

class ReActState(Enum):
    INIT = auto()         # 入口：建上下文、选协议档位
    AWAIT_MODEL = auto()  # 一轮模型往返 + 解释成标准化决策（auto-advance 循环）
    EXECUTE_TOOL = auto() # 执行工具批次
    RECOVER = auto()      # 所有"出问题了怎么办"的收口：重试 / 降档 / 兜底 / 终止
    ANSWER = auto()       # 交最终答案（可被 AGENT_STOP 钩子打回）
    DONE = auto()         # 终态


# ============================================================
# ReActEventType — events that drive state transitions
# （FSM 重设计 Step 3：收敛到 8 个队列事件 + 5 个旁路观测；
#   旧名保留为 alias，一个发布周期后删除）
# ============================================================

class ReActEventType(Enum):
    # ── 队列事件：驱动状态机。决策函数返回值封闭于这几种，
    #    转移表因此是全函数（totality 在测试里断言）。──
    START = auto()          # 用户调用 run(question)
    TOOLS_REQUESTED = auto()  # 解释器：模型要调工具（payload: tool_calls/thought/terminal_answer）
    ANSWER_READY = auto()   # 解释器：这是最终答案（payload 可带 text）
    FAULT = auto()          # 本轮输出不可用 / 致命错误（payload: reason/detail/fatal）
    TOOLS_DONE = auto()     # 整批工具执行完成
    ROUND_READY = auto()    # recover 决策：再来一轮模型调用
    ABORT = auto()          # 终止
    ANSWER_VETOED = auto()  # AGENT_STOP 钩子否决了最终答案（payload: reason）
    # ── 旁路观测：ctx.emit，不进队列，只给 TUI 实时展示 ──
    TOOL_DONE = auto()      # 单工具完成
    TOOL_START = auto()     # 工具即将执行
    STREAM_TOKEN = auto()   # LLM 流式 token
    STREAM_REASONING = auto()  # LLM 流式 reasoning
    ANSWER_THOUGHT = auto() # 答案前的思考展示
    # ── 旧名 alias（兼容 TUI / 下游测试，勿在新代码中使用）──
    TOOLS_FOUND = TOOLS_REQUESTED
    CLASSIFIED_TOOL = TOOLS_REQUESTED
    ALL_TOOLS_DONE = TOOLS_DONE
    LLM_PARAMS_READY = ROUND_READY
    RETRIES_LEFT = ROUND_READY
    DEGRADED = ROUND_READY
    NO_TOOLS = ANSWER_READY
    CLASSIFIED_ANSWER = ANSWER_READY
    FALLBACK_TEXT = ANSWER_READY
    STOP_VETOED = ANSWER_VETOED
    LLM_ERROR = FAULT
    EMPTY_RESPONSE = FAULT
    PARSE_ERROR = FAULT
    TRUNCATED_RESPONSE = FAULT
    CLASSIFIED_ERROR = FAULT
    NO_TOOLS_NO_TEXT = FAULT
    THOUGHT_MISSING = FAULT
    ROUTE_NATIVE = FAULT
    ROUTE_JSON = FAULT
    STRATEGY_READY = ROUND_READY
    NO_RETRIES = FAULT


# ============================================================
# RetryReason — typed category for RETRY_GATE decisions
# ============================================================

class RetryReason(Enum):
    EMPTY_RESPONSE = auto()    # LLM returned empty response text
    PARSE_ERROR = auto()       # JSON parse failed (detail carries parser message)
    CLASSIFY_ERROR = auto()    # parsed payload is neither tool_call nor answer
    THOUGHT_MISSING = auto()   # native tool_calls without visible thought text
    TRUNCATED = auto()         # output cut by token limit (finish_reason=length)


# ============================================================
# ReActEvent — structured event emitted during FSM execution
# ============================================================

@dataclass
class ReActEvent:
    type: ReActEventType
    payload: dict = field(default_factory=dict)
    step_id: int = 0
    timestamp: float = field(default_factory=time.time)
    seq: int = 0  # monotonic per-run order across FSM queue and ctx.emit side-channel


# ============================================================
# Transition — single row in the transfer table
# ============================================================

class Transition(NamedTuple):
    state: ReActState
    event: ReActEventType | None  # None = unconditional (always fire)
    next_state: ReActState
    handler: str  # method name on ReActAgent


# ============================================================
# ExecutionContext — shared mutable state across handlers
# ============================================================

@dataclass
class RunState:
    question: str = ""
    current_step: int = 0
    json_retries: int = 0
    strategy: CallingStrategy = CallingStrategy.PROMPT_JSON
    max_steps: int | None = None    # None = 不设上限（决策3，2026-09-24 拍板）
    max_json_retries: int = 2
    stop_vetoes: int = 0       # consecutive AGENT_STOP hook vetoes (cap prevents loops)
    thinking_enabled: bool = False
    cancel_checker: Any = None
    _current_step_span: Any = None  # TraceSpan for current plan_node (avoid circular import)
    # Fast-path stash: visible text accompanying a bookkeeping-only tool batch
    # (todo_add/todo_update), used as the final answer without another LLM round.
    terminal_answer: str | None = None


@dataclass
class MemoryRetrievalState:
    session_caps: Any = None       # SessionCapabilityTracker
    memory_manager: Any = None     # MemoryManager
    memory_context: str = ""
    conv_ctx: str = ""


@dataclass
class ToolCallState:
    tools: list[dict] = field(default_factory=list)
    tools_desc: str = ""
    pending_tool_calls: list[dict] = field(default_factory=list)
    last_subagent_payload: dict | None = None


def _state_property(state_name: str, attr_name: str):
    def getter(self):
        return getattr(getattr(self, state_name), attr_name)

    def setter(self, value):
        setattr(getattr(self, state_name), attr_name, value)

    return property(getter, setter)


@dataclass(init=False)
class ExecutionContext:
    """Per-run() mutable state shared across all FSM handlers."""

    run_state: RunState = field(default_factory=RunState)
    memory_state: MemoryRetrievalState = field(default_factory=MemoryRetrievalState)
    tool_state: ToolCallState = field(default_factory=ToolCallState)

    # -- mutable LLM state --
    messages: list[dict] = field(default_factory=list)

    # -- audit trail --
    steps: list[AgentStep] = field(default_factory=list)
    _total_usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    # -- per-step transient --
    last_response_text: str = ""
    last_reasoning: str = ""
    last_answer: str | None = None

    # -- compaction rebuild anchor: length of the initial message block --
    initial_count: int = 0

    # -- monotonic event order (FSM queue + emit side-channel share it) --
    _event_seq: int = 0

    # -- TUI event side-channel (bypasses FSM queue) --
    _on_emit: Any = None  # Callable[[ReActEvent, ReActState | None, ReActState | None], None]

    question = _state_property("run_state", "question")
    current_step = _state_property("run_state", "current_step")
    json_retries = _state_property("run_state", "json_retries")
    strategy = _state_property("run_state", "strategy")
    max_steps = _state_property("run_state", "max_steps")
    max_json_retries = _state_property("run_state", "max_json_retries")
    thinking_enabled = _state_property("run_state", "thinking_enabled")
    cancel_checker = _state_property("run_state", "cancel_checker")
    session_caps = _state_property("memory_state", "session_caps")
    memory_manager = _state_property("memory_state", "memory_manager")
    memory_context = _state_property("memory_state", "memory_context")
    conv_ctx = _state_property("memory_state", "conv_ctx")
    tools = _state_property("tool_state", "tools")
    tools_desc = _state_property("tool_state", "tools_desc")
    pending_tool_calls = _state_property("tool_state", "pending_tool_calls")
    last_subagent_payload = _state_property("tool_state", "last_subagent_payload")

    def __init__(
        self,
        question: str = "",
        messages: list[dict] | None = None,
        current_step: int = 0,
        json_retries: int = 0,
        strategy: CallingStrategy = CallingStrategy.PROMPT_JSON,
        max_steps: int | None = None,
        max_json_retries: int = 2,
        session_caps: Any = None,
        memory_manager: Any = None,
        tools: list[dict] | None = None,
        tools_desc: str = "",
        memory_context: str = "",
        conv_ctx: str = "",
        steps: list[AgentStep] | None = None,
        _total_usage: dict | None = None,
        last_response_text: str = "",
        last_reasoning: str = "",
        pending_tool_calls: list[dict] | None = None,
        last_answer: str | None = None,
        thinking_enabled: bool = False,
        last_subagent_payload: dict | None = None,
        _on_emit: Any = None,
        cancel_checker: Any = None,
        run_state: RunState | None = None,
        memory_state: MemoryRetrievalState | None = None,
        tool_state: ToolCallState | None = None,
    ) -> None:
        self.run_state = run_state or RunState(
            question=question,
            current_step=current_step,
            json_retries=json_retries,
            strategy=strategy,
            max_steps=max_steps,
            max_json_retries=max_json_retries,
            thinking_enabled=thinking_enabled,
            cancel_checker=cancel_checker,
        )
        self.memory_state = memory_state or MemoryRetrievalState(
            session_caps=session_caps,
            memory_manager=memory_manager,
            memory_context=memory_context,
            conv_ctx=conv_ctx,
        )
        self.tool_state = tool_state or ToolCallState(
            tools=tools or [],
            tools_desc=tools_desc,
            pending_tool_calls=pending_tool_calls or [],
            last_subagent_payload=last_subagent_payload,
        )
        self.messages = messages or []
        self.steps = steps or []
        self._total_usage = _total_usage or {"input_tokens": 0, "output_tokens": 0}
        self.last_response_text = last_response_text
        self.last_reasoning = last_reasoning
        self.last_answer = last_answer
        self._on_emit = _on_emit

    def next_seq(self) -> int:
        """Allocate the next monotonic event sequence number for this run."""
        self._event_seq += 1
        return self._event_seq

    def emit(self, event_type: 'ReActEventType', **payload: Any) -> None:
        """Push a real-time event directly to TUI, bypassing the FSM queue."""
        if self._on_emit:
            evt = ReActEvent(event_type, dict(payload), self.current_step, seq=self.next_seq())
            self._on_emit(evt, None, None)


# ============================================================
# ReActResult — structured return from ReActAgent.run()
# ============================================================

@dataclass
class ReActResult:
    answer: str | None = None
    steps: list = field(default_factory=list)
