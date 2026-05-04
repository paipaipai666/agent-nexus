import operator
from typing import Annotated, Literal, Optional, TypedDict

from pydantic import ValidationError


class AgentState(TypedDict):
    task: str
    trace_id: str
    plan: list[str]
    research_result: str
    research_status: Literal["ok", "error", ""]
    code_result: str
    code_status: Literal["ok", "error", ""]
    analysis: str
    critique_score: float
    critique_feedback: str
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
    expected_output: str
    coder_truncated: bool
    messages: Annotated[list, operator.add]


def validate_state(state: dict, node_name: str) -> None:
    """防御性类型检查 — TypedDict 无运行时校验，在关键节点入口做显式验证。"""
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
                "msg": f"Expected {expected}, got {type(val).__name__} (value={str(val)[:100]})",
                "input": val,
            }],
        )
