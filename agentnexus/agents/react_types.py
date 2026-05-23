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
class ExecutionContext:
    """Per-run() mutable state shared across all FSM handlers."""

    # -- immutable config --
    question: str = ""

    # -- mutable LLM state --
    messages: list[dict] = field(default_factory=list)
    current_step: int = 0
    json_retries: int = 0
    strategy: CallingStrategy = CallingStrategy.PROMPT_JSON
    max_steps: int = 10
    max_json_retries: int = 2

    # -- capability & memory --
    session_caps: Any = None       # SessionCapabilityTracker
    memory_manager: Any = None     # MemoryManager

    # -- prompt building --
    tools: list[dict] = field(default_factory=list)
    tools_desc: str = ""
    memory_context: str = ""
    conv_ctx: str = ""

    # -- audit trail --
    steps: list[AgentStep] = field(default_factory=list)
    _total_usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    # -- per-step transient --
    last_response_text: str = ""
    last_reasoning: str = ""
    pending_tool_calls: list[dict] = field(default_factory=list)
    last_answer: Optional[str] = None
    thinking_enabled: bool = False
    last_subagent_payload: Optional[dict] = None

    # -- TUI event side-channel (bypasses FSM queue) --
    _on_emit: Any = None  # Callable[[ReActEvent, Optional[ReActState], Optional[ReActState]], None]

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
