"""Transfer table for ReActAgent FSM — 25 transition rules."""

from agentnexus.agents.react_types import ReActEventType as E
from agentnexus.agents.react_types import ReActState as S
from agentnexus.agents.react_types import Transition

TRANSFER_TABLE: list[Transition] = [
    # ── INIT ──
    Transition(S.INIT, E.START, S.SELECT_STRATEGY, "_on_init"),
    # (step >= max is checked inside _on_init; it emits ABORT directly)

    # ── SELECT_STRATEGY ──
    Transition(S.SELECT_STRATEGY, E.STRATEGY_READY, S.PREPARE_LLM_CALL, "_on_strategy_ready"),

    # ── PREPARE_LLM_CALL ──
    Transition(S.PREPARE_LLM_CALL, E.LLM_PARAMS_READY, S.CALL_LLM, "_on_llm_params_ready"),

    # ── CALL_LLM ──
    Transition(S.CALL_LLM, E.LLM_RESPONSE, S.RECEIVE_RESPONSE, "_on_llm_response"),
    Transition(S.CALL_LLM, E.LLM_ERROR, S.ERROR_ABORT, "_on_llm_error"),

    # ── RECEIVE_RESPONSE → routing ──
    Transition(S.RECEIVE_RESPONSE, E.ROUTE_NATIVE, S.CHECK_TOOL_CALLS, "_on_receive_native"),
    Transition(S.RECEIVE_RESPONSE, E.ROUTE_JSON, S.CHECK_EMPTY, "_on_receive_json"),

    # ── CHECK_TOOL_CALLS ──
    Transition(S.CHECK_TOOL_CALLS, E.TOOLS_FOUND, S.EXECUTE_TOOL, "_on_tools_found"),
    Transition(S.CHECK_TOOL_CALLS, E.NO_TOOLS, S.EMIT_ANSWER, "_on_no_tools_answer"),
    Transition(S.CHECK_TOOL_CALLS, E.NO_TOOLS_NO_TEXT, S.DEGRADE, "_on_no_tools_degrade"),
    Transition(S.CHECK_TOOL_CALLS, E.THOUGHT_MISSING, S.RETRY_GATE, "_on_thought_missing"),

    # ── EXECUTE_TOOL ──
    Transition(S.EXECUTE_TOOL, E.TOOL_DONE, S.EXECUTE_TOOL, "_on_tool_done"),
    Transition(S.EXECUTE_TOOL, E.ALL_TOOLS_DONE, S.PREPARE_LLM_CALL, "_on_all_tools_done"),

    # ── CHECK_EMPTY ──
    Transition(S.CHECK_EMPTY, E.EMPTY_RESPONSE, S.RETRY_GATE, "_on_empty_response"),
    Transition(S.CHECK_EMPTY, E.HAS_CONTENT, S.JSON_PARSE, "_on_has_content"),

    # ── JSON_PARSE ──
    Transition(S.JSON_PARSE, E.PARSE_SUCCESS, S.CLASSIFY, "_on_parse_success"),
    Transition(S.JSON_PARSE, E.PARSE_ERROR, S.RETRY_GATE, "_on_parse_error"),

    # ── CLASSIFY ──
    Transition(S.CLASSIFY, E.CLASSIFIED_TOOL, S.EXECUTE_TOOL, "_on_classified_tool"),
    Transition(S.CLASSIFY, E.CLASSIFIED_ANSWER, S.EMIT_ANSWER, "_on_classified_answer"),
    Transition(S.CLASSIFY, E.CLASSIFIED_ERROR, S.RETRY_GATE, "_on_classified_error"),

    # ── RETRY_GATE ──
    Transition(S.RETRY_GATE, E.RETRIES_LEFT, S.PREPARE_LLM_CALL, "_on_retries_left"),
    Transition(S.RETRY_GATE, E.NO_RETRIES, S.DEGRADE, "_on_no_retries_degrade"),
    Transition(S.RETRY_GATE, E.FALLBACK_TEXT, S.EMIT_ANSWER, "_on_fallback_text"),

    # ── DEGRADE ──
    Transition(S.DEGRADE, E.DEGRADED, S.PREPARE_LLM_CALL, "_on_degraded"),

    # ── MAX_STEPS ──
    Transition(S.MAX_STEPS, E.ABORT, S.DONE, "_on_max_steps_abort"),

    # ── ERROR_ABORT ──
    Transition(S.ERROR_ABORT, E.ABORT, S.DONE, "_on_error_abort"),

    # ── EMIT_ANSWER ── (unconditional: always → DONE)
    Transition(S.EMIT_ANSWER, None, S.DONE, "_on_emit_answer"),
]
