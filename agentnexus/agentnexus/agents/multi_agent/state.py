from typing import Annotated, TypedDict
import operator


class AgentState(TypedDict):
    task: str
    plan: list[str]
    research_result: str
    code_result: str
    analysis: str
    critique_score: float
    critique_feedback: str
    retry_count: int
    research_query: str
    code_spec: str
    messages: Annotated[list, operator.add]
