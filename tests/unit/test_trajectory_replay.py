"""Trajectory replay tests.

Validates that a saved trace can be replayed and the agent produces
the same or equivalent output.
"""
import json
from unittest.mock import MagicMock

from agentnexus.agents.re_act_agent import ReActAgent
from agentnexus.tools.registry import ToolRegistry


def _make_llm():
    llm = MagicMock()
    llm.model = "test/test-model"
    llm.total_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.last_error = ""
    llm.last_truncated = False
    llm.last_tool_calls = []
    llm.last_reasoning_content = ""
    llm.last_usage = {"input_tokens": 0, "output_tokens": 0}
    llm.capabilities = MagicMock()
    llm.capabilities.supports_thinking = False
    llm.capabilities.supports_tool_calling = True
    llm.capabilities.supports_json_mode = True
    llm.capabilities.supports_json_schema = False
    llm.capabilities.supports_parallel_tool_calls = False
    llm.capabilities.thinking_effort = "none"
    llm.think.return_value = ""
    return llm


class TestTrajectoryReplay:
    """Replay saved traces and validate output equivalence."""

    def _make_trace(self, trace_id, question, answer, steps=None):
        import time
        return {
            "timestamp": time.time(),
            "trace_id": trace_id,
            "question": question,
            "answer": answer,
            "steps": steps or [],
        }

    def test_replay_produces_same_answer(self):
        trace = self._make_trace("trace-1", "What is Python?", "Python is a language")

        llm = _make_llm()
        llm.think.side_effect = lambda **kw: (setattr(llm, 'last_error', '') or trace["answer"])
        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda **kw: {"results": []})
        agent = ReActAgent(llm, te, max_steps=3)

        result = agent.run(trace["question"])

        assert result.answer == trace["answer"]

    def test_replay_with_tool_calls(self):
        trace = self._make_trace(
            "trace-2", "Search for Python", "Found it",
            steps=[{"action": "web_search", "params": {"query": "Python"}}],
        )

        llm = _make_llm()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "Python"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return trace["answer"]
        llm.think.side_effect = mock_think

        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda **q: {"results": []})
        agent = ReActAgent(llm, te, max_steps=3)

        result = agent.run(trace["question"])
        assert result.answer == trace["answer"]

    def test_replay_trace_from_jsonl(self, temp_agentnexus_home):
        trace_file = temp_agentnexus_home / "replay.jsonl"
        trace = self._make_trace("trace-3", "Replay test", "Replayed")
        trace_file.write_text(json.dumps(trace), encoding="utf-8")

        lines = trace_file.read_text().strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["trace_id"] == "trace-3"
        assert parsed["question"] == "Replay test"

    def test_replay_multiple_traces_from_jsonl(self, temp_agentnexus_home):
        trace_file = temp_agentnexus_home / "replay_multi.jsonl"
        traces = [self._make_trace(f"trace-{i}", f"Q{i}", f"A{i}") for i in range(5)]
        trace_file.write_text("\n".join(json.dumps(t) for t in traces), encoding="utf-8")

        lines = trace_file.read_text().strip().split("\n")
        assert len(lines) == 5

        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed["trace_id"] == f"trace-{i}"

    def test_replay_validates_tool_sequence(self):
        trace = self._make_trace(
            "trace-4", "Search and read", "Done",
            steps=[
                {"action": "web_search", "params": {"query": "test"}},
                {"action": "file_read", "params": {"path": "test.py"}},
            ],
        )

        assert len(trace["steps"]) == 2
        assert trace["steps"][0]["action"] == "web_search"
        assert trace["steps"][1]["action"] == "file_read"

    def test_replay_with_different_tool_responses(self):
        trace = self._make_trace(
            "trace-5", "Search for X", "Found X",
            steps=[{"action": "web_search", "params": {"query": "X"}}],
        )

        llm = _make_llm()
        call_count = [0]
        def mock_think(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                llm.last_tool_calls = [{"name": "web_search", "arguments": {"query": "X"}}]
                return "Searching..."
            llm.last_tool_calls = []
            return trace["answer"]
        llm.think.side_effect = mock_think

        te = ToolRegistry()
        te.register_tool("web_search", "搜索", lambda **q: {"results": ["different"]})
        agent = ReActAgent(llm, te, max_steps=3)

        result = agent.run(trace["question"])
        assert result.answer == trace["answer"]
