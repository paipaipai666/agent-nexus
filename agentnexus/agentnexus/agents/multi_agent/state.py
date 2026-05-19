import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import ValidationError


class AgentState(TypedDict):
    task: str
    trace_id: str
    memory_session_id: str
    started_at: float
    tool_call_count: int
    plan: list[str]
    plan_complexity: str
    plan_metadata: dict
    research_result: str
    research_status: Literal["ok", "error", ""]
    code_result: str
    code_status: Literal["ok", "error", ""]
    analysis: str
    critique_score: float
    critique_feedback: str
    critique_fail_type: str
    hard_verdict: Optional[dict]
    error_type: str
    retry_count: int
    retry_instruction: str
    research_query: str
    code_spec: str
    code_specs: list[str]
    research_claims: list[dict]
    exec_stdout: str
    exec_stderr: str
    exec_success: bool
    exec_exception: str
    exec_diff: dict
    coder_truncated: bool
    error_history: Annotated[list[dict], operator.add]
    messages: Annotated[list, operator.add]


# ── AgentIO contracts: explicit input/output keys per FSM node ────────────
# Each entry declares which state keys the node REQUIRES (inputs) and PRODUCES (outputs).
# validate_state() cross-checks these at node entry — missing keys → early error.

NODE_CONTRACTS: dict[str, dict] = {
    "plan": {
        "inputs": ["task"],
        "outputs": ["plan", "plan_complexity", "plan_metadata", "messages"],
        "doc": "Decompose task into research/code steps via LLM planner.",
    },
    "research": {
        "inputs": ["task", "plan"],
        "outputs": ["research_result", "research_claims", "research_status", "messages"],
        "doc": "Search knowledge base + web, return structured claims with sources.",
    },
    "code": {
        "inputs": ["task", "plan", "retry_count", "messages"],
        "outputs": ["code_result", "code_status", "messages", "coder_truncated"],
        "doc": "Generate executable Python code via LLM coder, apply schema validation.",
    },
    "execute": {
        "inputs": ["messages"],
        "outputs": ["exec_success", "exec_stdout", "exec_stderr", "exec_exception", "retry_count", "messages"],
        "doc": "Execute generated code in sandbox, capture stdout/stderr/exception.",
    },
    "analyst": {
        "inputs": ["task", "plan", "messages"],
        "outputs": ["analysis", "critique_score", "critique_feedback", "critique_fail_type",
                     "hard_verdict", "retry_instruction", "retry_count", "messages", "error_history"],
        "doc": "Synthesize final answer, run critic, record error history.",
    },
}


def validate_state(state: dict, node_name: str) -> None:
    """Defensive type check + contract validation at node entry.

    Checks that all keys declared in NODE_CONTRACTS[node_name].inputs exist in state.
    Missing required keys → hard error (fail fast) instead of silent fallback.
    """
    # Contract check: required inputs must exist
    if node_name in NODE_CONTRACTS:
        for key in NODE_CONTRACTS[node_name]["inputs"]:
            if key not in state:
                raise ValidationError.from_exception_data(
                    title=f"[{node_name}] AgentState 缺少必需输入键",
                    line_errors=[{
                        "type": "missing",
                        "loc": (key,),
                        "msg": f"节点 '{node_name}' 需要 '{key}'，但 state 中不存在。"
                               f"声明输入: {NODE_CONTRACTS[node_name]['inputs']}",
                        "input": list(state.keys()),
                    }],
                )

    # Type checks for commonly-used keys
    _check_type(state, "task", str, node_name)
    _check_type(state, "plan", list, node_name)
    _check_type(state, "retry_count", int, node_name)
    _check_type(state, "critique_score", (int, float), node_name)
    _check_type(state, "exec_success", bool, node_name)
    _check_type(state, "coder_truncated", bool, node_name)
    _check_type(state, "research_status", str, node_name)
    _check_type(state, "code_status", str, node_name)
    _check_type(state, "messages", list, node_name)


def _check_type(state: dict, key: str, expected: type | tuple, node: str) -> None:
    if key not in state:
        return
    val = state[key]
    if not isinstance(val, expected):
        raise ValidationError.from_exception_data(
            title=f"[{node}] AgentState.{key}",
            line_errors=[{
                "type": "type_error",
                "loc": (key,),
                "msg": f"Expected {expected}, got {type(val).__name__} (value={str(val)})",
                "input": val,
            }],
        )
