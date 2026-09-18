"""ReActAgentRunner transcript 收集的行为测试。

核心回归点: 修复前 _collect_transcript 用 ``i * 2.0`` 编造时间戳,
且 eval 跑 agent 时根本没有 trace 上下文。修复后 trial 必须产出
真实墙钟时间的 spans, 并保持 grader 协议的 span 命名 ("llm")。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agentnexus.evaluation.runner import ReActAgentRunner
from agentnexus.observability.tracer import trace_manager


@pytest.fixture
def clean_trace():
    """确保每个测试后线程本地 trace 被清理。"""
    yield
    # active 为 None 时 end_trace 是 no-op, 直接调用即可
    trace_manager.end_trace()


class _FakeAgent:
    """模拟 ReActAgent: 在 active trace 下产生 plan_node/tool/final_answer spans。"""

    def __init__(self, llm_client=None, tool_executor=None, max_steps=5):
        self._total_usage = {"input_tokens": 10, "output_tokens": 5}

    def run(self, question: str):
        assert trace_manager.active is not None, "runner 必须为 trial 建立 trace 上下文"
        with trace_manager.span("plan_node", {"step_index": 0, "strategy": "NATIVE_TOOLS"}):
            time.sleep(0.01)  # 真实耗时, 使 latency 可测
        with trace_manager.span("tool", {"tool_name": "grep_search", "params": {"q": "x"}}):
            time.sleep(0.005)
        with trace_manager.span("final_answer", {}):
            pass
        return SimpleNamespace(answer="the answer", steps=[])


def _make_task():
    return SimpleNamespace(id="task-1", input={"prompt": "do something"}, max_turns=None)


def _patch_agent_deps(monkeypatch):
    monkeypatch.setattr("agentnexus.agents.re_act_agent.ReActAgent", _FakeAgent)
    monkeypatch.setattr("agentnexus.core.llm.AgentLLM", lambda: object())
    monkeypatch.setattr("agentnexus.tools.registry.ToolRegistry", lambda: object())
    # runner 内会 from agentnexus.tools import register_all_tools —— patch 源头模块
    monkeypatch.setattr("agentnexus.tools.register_all_tools", lambda *a, **kw: None)
    monkeypatch.setattr(
        "agentnexus.core.config.get_settings",
        lambda: SimpleNamespace(max_agent_steps=5, llm_model_id="fake-model"),
    )


def test_transcript_uses_real_clock(monkeypatch, clean_trace):
    """trial 的 transcript 必须携带真实墙钟时间, 不再是编造的 i*2.0。"""
    _patch_agent_deps(monkeypatch)
    runner = ReActAgentRunner()

    result = runner(_make_task(), trial_index=0)

    assert result.error is None
    assert result.transcript, "transcript 不应为空"
    timed = [s for s in result.transcript if "start_time" in s]
    assert timed, "真实 trace 路径的 spans 必须带 start_time"
    for span in timed:
        assert span["start_time"] > 0
        assert span["end_time"] >= span["start_time"]
        assert span["latency_ms"] >= 0
    # 真实 sleep 至少 10ms, 编造的 1.0s 固定值不可能出现
    plan = next(s for s in result.transcript if s["name"] == "llm")
    assert plan["latency_ms"] >= 5, f"plan_node 应有真实耗时, 得到 {plan['latency_ms']}ms"


def test_span_names_match_grader_protocol(monkeypatch, clean_trace):
    """agent 埋点名 plan_node 必须映射为 grader 协议名 "llm"。"""
    _patch_agent_deps(monkeypatch)
    runner = ReActAgentRunner()

    result = runner(_make_task(), trial_index=0)

    names = [s["name"] for s in result.transcript]
    assert "llm" in names
    assert "plan_node" not in names
    assert "tool" in names
    assert "final_answer" in names
    assert "task" in names  # 根 span, failure_attribution 依赖


def test_trace_id_recorded_in_metadata(monkeypatch, clean_trace):
    """metadata 必须带 trace_id, 供 replay/审计回溯。"""
    _patch_agent_deps(monkeypatch)
    runner = ReActAgentRunner()

    result = runner(_make_task(), trial_index=0)

    assert result.metadata.get("trace_id")
    assert result.metadata["model"] == "fake-model"


def test_does_not_hijack_existing_trace(monkeypatch, clean_trace):
    """已有 active trace 时不得覆盖, 也不得替别人 end_trace。"""
    _patch_agent_deps(monkeypatch)
    upstream = trace_manager.start_trace("upstream-task")
    runner = ReActAgentRunner()

    try:
        assert runner._start_trace(_make_task()) is None
        assert trace_manager.active is upstream

        # None 上下文不得触发 end_trace —— 上游 trace 结束后 active 才被清
        runner._end_trace(None)
        assert trace_manager.active is upstream
    finally:
        trace_manager.end_trace()
    assert trace_manager.active is None


def test_fallback_marks_reconstructed_without_fake_times(monkeypatch, clean_trace):
    """无 trace 可用时, 重建 spans 不得编造时间, 必须显式标注 reconstructed。"""
    _patch_agent_deps(monkeypatch)
    runner = ReActAgentRunner()

    step = SimpleNamespace(
        step_id=0,
        strategy_used=None,
        content="thinking",
        reasoning_content="",
        error_message=None,
        tool_calls=[{"name": "shell", "arguments": {"cmd": "ls"}}],
        tool_outputs=[{"output": "file.txt", "error": None}],
    )
    result_obj = SimpleNamespace(answer="ok", steps=[step])

    spans = runner._collect_transcript(object(), result_obj, trace_ctx=None)

    assert spans
    for span in spans:
        assert "start_time" not in span, "fallback 不允许编造时间戳"
        assert span["metadata"]["reconstructed"] is True
    # 插入序即真实步骤顺序: llm 在前, tool 跟随, final_answer 收尾
    assert spans[0]["name"] == "llm"
    assert spans[1]["name"] == "tool"
    assert spans[-1]["name"] == "final_answer"
