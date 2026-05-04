import operator
from typing import Annotated, Optional, TypedDict


class AgentState(TypedDict):
    task: str
    trace_id: str
    plan: list[str]
    research_result: str
    code_result: str
    analysis: str
    critique_score: float
    critique_feedback: str
    hard_verdict: Optional[dict]
    error_type: str
    retry_count: int
    retry_instruction: str
    research_query: str
    code_spec: str
    research_claims: list[dict]
    exec_stdout: str
    exec_stderr: str
    exec_success: bool
    exec_exception: str
    exec_diff: dict
    expected_output: str
    messages: Annotated[list, operator.add]
