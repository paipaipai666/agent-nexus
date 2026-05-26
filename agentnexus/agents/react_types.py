"""ReActAgent FSM types: states, events, context, and transition table entries."""

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, NamedTuple, Optional

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
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)
    error_message: Optional[str] = None


# ============================================================
# ReActState — FSM states
# ============================================================

class ReActState(Enum):
    INIT = auto()              # entry: build prompt, messages, memory
    SELECT_STRATEGY = auto()   # choose CallingStrategy from caps + session
    PREPARE_LLM_CALL = auto()  # set tools/response_format, inject JSON hint
    CALL_LLM = auto()          # blocking llm_client.think()
    RECEIVE_RESPONSE = auto()  # record AgentStep, route by strategy
    CHECK_TOOL_CALLS = auto()  # NATIVE: check last_tool_calls
    EXECUTE_TOOL = auto()      # run tool, collect observation
    CHECK_EMPTY = auto()       # non-NATIVE: is response_text empty?
    JSON_PARSE = auto()        # _robust_json_parse()
    CLASSIFY = auto()          # _classify_parsed() → tool_call / answer / error
    RETRY_GATE = auto()        # should we retry, degrade, or fallback?
    DEGRADE = auto()           # mark_failed + re-select strategy
    EMIT_ANSWER = auto()       # output final answer, save memory, conclude
    MAX_STEPS = auto()         # step limit reached
    ERROR_ABORT = auto()       # unrecoverable error
    DONE = auto()              # terminal state


# ============================================================
# ReActEventType — events that drive state transitions
# ============================================================

class ReActEventType(Enum):
    START = auto()             # user calls run(question)
    STRATEGY_READY = auto()    # _select_strategy() completed
    LLM_PARAMS_READY = auto()  # think parameters prepared
    LLM_RESPONSE = auto()      # LLM returned successfully
    LLM_ERROR = auto()         # LLM call failed
    TOOLS_FOUND = auto()       # NATIVE: last_tool_calls non-empty
    NO_TOOLS = auto()          # NATIVE: no tool_calls, has text → answer
    NO_TOOLS_NO_TEXT = auto()  # NATIVE: no tool_calls, no text → degrade
    TOOL_DONE = auto()         # single tool execution completed
    ALL_TOOLS_DONE = auto()    # all tools in batch executed
    EMPTY_RESPONSE = auto()    # non-NATIVE: response_text is empty
    HAS_CONTENT = auto()       # non-NATIVE: response_text has content
    PARSE_SUCCESS = auto()     # _robust_json_parse returned valid data
    PARSE_ERROR = auto()       # _robust_json_parse returned error
    CLASSIFIED_TOOL = auto()   # _classify_parsed → tool_call
    CLASSIFIED_ANSWER = auto() # _classify_parsed → answer
    CLASSIFIED_ERROR = auto()  # _classify_parsed → error
    RETRIES_LEFT = auto()      # json_retries < MAX
    NO_RETRIES = auto()        # json_retries exhausted
    DEGRADED = auto()          # strategy degraded successfully
    ABORT = auto()             # unrecoverable, terminate
    ROUTE_NATIVE = auto()      # internal: route to CHECK_TOOL_CALLS path
    ROUTE_JSON = auto()        # internal: route to CHECK_EMPTY path
    FALLBACK_TEXT = auto()     # internal: PROMPT_JSON exhausted → use raw text as answer
    THOUGHT_MISSING = auto()   # NATIVE: model returned tool_calls without Thought text
    TOOL_START = auto()        # direct emit: tool about to execute (TUI spinner)
    ANSWER_THOUGHT = auto()    # direct emit: thought shown before final answer after tool usage


# ============================================================
# ReActEvent — structured event emitted during FSM execution
# ============================================================

@dataclass
class ReActEvent:
    type: ReActEventType
    payload: dict = field(default_factory=dict)
    step_id: int = 0
    timestamp: float = field(default_factory=time.time)


# ============================================================
# Transition — single row in the transfer table
# ============================================================

class Transition(NamedTuple):
    state: ReActState
    event: Optional[ReActEventType]  # None = unconditional (always fire)
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
    max_steps: int = 10
    max_json_retries: int = 2
    thinking_enabled: bool = False
    cancel_checker: Any = None


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
    last_subagent_payload: Optional[dict] = None


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
    last_answer: Optional[str] = None

    # -- TUI event side-channel (bypasses FSM queue) --
    _on_emit: Any = None  # Callable[[ReActEvent, Optional[ReActState], Optional[ReActState]], None]

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
        max_steps: int = 10,
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
        last_answer: Optional[str] = None,
        thinking_enabled: bool = False,
        last_subagent_payload: Optional[dict] = None,
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

    def emit(self, event_type: 'ReActEventType', **payload: Any) -> None:
        """Push a real-time event directly to TUI, bypassing the FSM queue."""
        if self._on_emit:
            evt = ReActEvent(event_type, dict(payload), self.current_step)
            self._on_emit(evt, None, None)


# ============================================================
# ReActResult — structured return from ReActAgent.run()
# ============================================================

@dataclass
class ReActResult:
    answer: Optional[str] = None
    steps: list = field(default_factory=list)
