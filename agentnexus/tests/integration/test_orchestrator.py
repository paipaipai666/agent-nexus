from unittest.mock import MagicMock

from agentnexus.agents.multi_agent.orchestrator import (
    MAX_RETRIES,
    plan_node,
    route_after_execute,
)
from agentnexus.agents.multi_agent.state import AgentState


def _default_state(**overrides) -> AgentState:
    s: AgentState = {
        "task": "test",
        "trace_id": "t1",
        "plan": [],
        "research_result": "",
        "research_status": "",
        "code_result": "",
        "code_status": "",
        "analysis": "",
        "critique_score": 0.0,
        "critique_feedback": "",
        "retry_count": 0,
        "research_query": "",
        "code_spec": "",
        "code_specs": [],
        "research_claims": [],
        "exec_stdout": "",
        "exec_stderr": "",
        "exec_success": True,
        "exec_exception": "",
        "exec_diff": {},
        "expected_output": "",
        "coder_truncated": False,
        "messages": [],
    }
    s.update(overrides)
    return s


def _mock_planner(mocker, return_value: str):
    mock_llm_instance = MagicMock()
    mock_llm_instance.think.return_value = return_value
    mocker.patch(
        "agentnexus.agents.multi_agent.orchestrator._get_planner_llm",
        return_value=mock_llm_instance,
    )


class TestPlanNode:

    def test_parses_response_with_colon_lines(self, mocker):
        _mock_planner(mocker, "research: 搜索特斯拉财报\ncode: 分析财务数据\nanalyze: 写报告")
        state = _default_state(task="分析特斯拉财报")
        result = plan_node(state)
        plan = result["plan"]
        assert len(plan) == 3
        assert "research: 搜索特斯拉财报" in plan
        assert "code: 分析财务数据" in plan

    def test_fallback_plan_when_no_colon_lines(self, mocker):
        _mock_planner(mocker, "No structured plan here just free text")
        state = _default_state(task="分析数据")
        result = plan_node(state)
        plan = result["plan"]
        assert len(plan) == 1
        assert "research" in plan[0]


class TestRouteAfterExecute:

    def test_success_routes_to_analyst(self):
        state = _default_state(exec_success=True)
        assert route_after_execute(state) == "analyst"

    def test_failure_routes_to_code_first_retry(self):
        state = _default_state(exec_success=False, retry_count=0)
        assert route_after_execute(state) == "code"

    def test_max_retries_exceeded_routes_to_analyst(self):
        state = _default_state(exec_success=False, retry_count=MAX_RETRIES + 1)
        assert route_after_execute(state) == "analyst"

    def test_no_output_late_retry_goes_to_research(self):
        state = _default_state(exec_success=False, retry_count=2,
                               exec_exception="NO_OUTPUT: code executed without error")
        assert route_after_execute(state) == "research"
