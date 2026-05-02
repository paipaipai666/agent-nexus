from unittest.mock import patch, MagicMock

from agentnexus.agents.multi_agent.orchestrator import (
    plan_node,
    should_retry,
    continue_to_agents,
    PASS_THRESHOLD,
    MAX_RETRIES,
)
from agentnexus.agents.multi_agent.state import AgentState


class TestPlanNode:

    def test_parses_response_with_colon_lines(self, mocker):
        mock_llm = mocker.patch(
            "agentnexus.agents.multi_agent.orchestrator._planner_llm.think",
            return_value="research: 搜索特斯拉财报\ncode: 分析财务数据\nanalyze: 写报告",
        )
        state: AgentState = {
            "task": "分析特斯拉财报",
            "trace_id": "test-001",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 0.0,
            "critique_feedback": "",
            "retry_count": 0,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        result = plan_node(state)
        plan = result["plan"]
        assert len(plan) == 3
        assert "research: 搜索特斯拉财报" in plan
        assert "code: 分析财务数据" in plan

    def test_fallback_plan_when_no_colon_lines(self, mocker):
        mock_llm = mocker.patch(
            "agentnexus.agents.multi_agent.orchestrator._planner_llm.think",
            return_value="No structured plan here just free text",
        )
        state: AgentState = {
            "task": "分析数据",
            "trace_id": "test-002",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 0.0,
            "critique_feedback": "",
            "retry_count": 0,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        result = plan_node(state)
        plan = result["plan"]
        assert len(plan) == 1
        assert "research" in plan[0]

    def test_plan_node_returns_retry_count(self, mocker):
        mock_llm = mocker.patch(
            "agentnexus.agents.multi_agent.orchestrator._planner_llm.think",
            return_value="research: 搜索\ncode: 开发",
        )
        state: AgentState = {
            "task": "test",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 0.0,
            "critique_feedback": "",
            "retry_count": 2,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        result = plan_node(state)
        assert result["retry_count"] == 2


class TestShouldRetry:

    def test_approved_when_score_above_threshold(self):
        state: AgentState = {
            "task": "t",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 8.0,
            "critique_feedback": "",
            "retry_count": 0,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        assert should_retry(state) == "approved"

    def test_approved_when_score_equal_to_threshold(self):
        state: AgentState = {
            "task": "t",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": PASS_THRESHOLD,
            "critique_feedback": "",
            "retry_count": 0,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        assert should_retry(state) == "approved"

    def test_retry_when_score_below_threshold(self):
        state: AgentState = {
            "task": "t",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 5.0,
            "critique_feedback": "不够好",
            "retry_count": 0,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        assert should_retry(state) == "retry"

    def test_approved_when_max_retries_reached(self):
        state: AgentState = {
            "task": "t",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 3.0,
            "critique_feedback": "",
            "retry_count": MAX_RETRIES,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        assert should_retry(state) == "approved"

    def test_approved_when_retries_exceed_max(self):
        state: AgentState = {
            "task": "t",
            "trace_id": "t1",
            "plan": [],
            "research_result": "",
            "code_result": "",
            "analysis": "",
            "critique_score": 1.0,
            "critique_feedback": "",
            "retry_count": 99,
            "research_query": "",
            "code_spec": "",
            "messages": [],
        }
        assert should_retry(state) == "approved"
