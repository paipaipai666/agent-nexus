"""ReActAgent FSM types: states, events, context, and transition table entries."""

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, NamedTuple


class CallingStrategy(Enum):
    NATIVE_TOOLS = auto()
    JSON_MODE = auto()
    PROMPT_JSON = auto()
    PLAIN_TEXT = auto()


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


class ReActState(Enum):
    INIT = auto()
    AWAIT_MODEL = auto()
    EXECUTE_TOOL = auto()
    RECOVER = auto()
    ANSWER = auto()
    DONE = auto()


class ReActEventType(Enum):
    START = auto()
    TOOLS_REQUESTED = auto()
    ANSWER_READY = auto()
    FAULT = auto()
    TOOLS_DONE = auto()
    ROUND_READY = auto()
    ABORT = auto()
    ANSWER_VETOED = auto()
    TOOL_DONE = auto()
    TOOL_START = auto()
    STREAM_TOKEN = auto()
    STREAM_REASONING = auto()
    ANSWER_THOUGHT = auto()
    LOOP_WARNING = auto()
    BUDGET_REMINDER = auto()
    LLM_CALL = auto()
    DRIFT_SIGNAL = auto()
    COMPACTION = auto()
    MEMORY_REFRESH = auto()
    ANSWER_FINALIZED = auto()
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


class RetryReason(Enum):
    EMPTY_RESPONSE = auto()
    PARSE_ERROR = auto()
    CLASSIFY_ERROR = auto()
    THOUGHT_MISSING = auto()
    TRUNCATED = auto()


@dataclass
class ReActEvent:
    type: ReActEventType
    payload: dict = field(default_factory=dict)
    step_id: int = 0
    timestamp: float = field(default_factory=time.time)
    seq: int = 0


class Transition(NamedTuple):
    state: ReActState
    event: ReActEventType | None
    next_state: ReActState
    handler: str


@dataclass
class RunState:
    question: str = ""
    current_step: int = 0
    json_retries: int = 0
    strategy: CallingStrategy = CallingStrategy.PROMPT_JSON
    max_steps: int | None = None
    max_json_retries: int = 2
    loop_warn_count: int = 0
    token_budget: int | None = None
    token_reminders: int = 0
    stop_vetoes: int = 0
    thinking_enabled: bool = False
    cancel_checker: Any = None
    _current_step_span: Any = None
    partial_answer: str = ""


@dataclass
class MemoryRetrievalState:
    session_caps: Any = None
    memory_manager: Any = None
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

    messages: list[dict] = field(default_factory=list)

    steps: list[AgentStep] = field(default_factory=list)
    _total_usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    last_response_text: str = ""
    last_reasoning: str = ""
    last_answer: str | None = None

    initial_count: int = 0

    _event_seq: int = 0

    _on_emit: Any = None

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


@dataclass
class ReActResult:
    answer: str | None = None
    steps: list = field(default_factory=list)
