from unittest.mock import MagicMock

from agentnexus.agents.multi_agent.orchestrator import (
    MAX_RETRIES,
    PASS_THRESHOLD,
    continue_to_agents,
    plan_node,
    should_retry,
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

    def test_plan_node_returns_retry_count(self, mocker):
        _mock_planner(mocker, "research: 搜索\ncode: 开发")
        state = _default_state(task="test", retry_count=2)
        result = plan_node(state)
        assert result.get("retry_count") is None  # plan_node doesn't return retry_count


class TestShouldRetry:

    def test_approved_when_score_above_threshold(self):
        state = _default_state(critique_score=8.0)
        assert should_retry(state) == "approved"

    def test_approved_when_score_equal_to_threshold(self):
        state = _default_state(critique_score=PASS_THRESHOLD)
        assert should_retry(state) == "approved"

    def test_retry_when_score_below_threshold(self):
        state = _default_state(critique_score=5.0, critique_feedback="不够好")
        assert should_retry(state) == "retry"

    def test_approved_when_max_retries_reached(self):
        state = _default_state(critique_score=3.0, retry_count=MAX_RETRIES)
        assert should_retry(state) == "approved"

    def test_approved_when_retries_exceed_max(self):
        state = _default_state(critique_score=1.0, retry_count=99)
        assert should_retry(state) == "approved"
