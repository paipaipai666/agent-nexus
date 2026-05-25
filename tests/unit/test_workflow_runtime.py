from unittest.mock import MagicMock

from agentnexus.observability.tracer import trace_manager
from agentnexus.skills.runtime import WorkflowRunState, WorkflowRuntime
from agentnexus.skills.workflow import Workflow
from agentnexus.tools.tool_executor import ToolExecutor


def _profile(steps, tool_policy=None):
    return Workflow.model_validate({
        "id": "review",
        "version": "1",
        "display_name": "Review",
        "description": "Review workflow",
        "prompt_profile": {"system": "react", "variables": {"target": "diff"}},
        "tool_policy": tool_policy or {"max_risk": "low"},
        "retrieval_policy": {"namespace": "docs", "top_k": 2, "view": "chunk", "filters": {"block_type": "code"}},
        "steps": steps,
        "success_criteria": ["Mention {target}."],
    }).to_session_profile()


def test_prepare_none_profile_returns_original_question():
    result = WorkflowRuntime().prepare("hello", None)
    assert result.enhanced_question == "hello"
    assert result.workflow_context == ""
    assert result.events == []


def test_prompt_checkpoint_finalize_steps_build_context():
    profile = _profile([
        {"type": "prompt", "id": "inspect", "prompt": "Inspect {target}."},
        {"type": "checkpoint", "id": "mark", "prompt": "Checkpoint {target}."},
        {"type": "finalize", "id": "done", "prompt": "Finish {target}."},
    ])
    result = WorkflowRuntime().prepare("question", profile)

    assert "Workflow: review" in result.workflow_context
    assert "Inspect diff." in result.workflow_context
    assert "Checkpoint diff." in result.workflow_context
    assert "Finish diff." in result.workflow_context
    assert "Mention diff." in result.workflow_context
    assert result.enhanced_question.endswith("== User Question ==\nquestion")
    assert [event.status for event in result.events] == ["ok", "ok", "ok"]
    assert result.state is not None
    assert result.state.status == "completed"
    assert result.state.ok_count == 3
    assert result.events[0].run_id == result.state.run_id


def test_start_advance_render_context_state_machine():
    profile = _profile([
        {"type": "prompt", "id": "inspect", "prompt": "Inspect {target}."},
        {"type": "finalize", "id": "done", "prompt": "Finish."},
    ])
    runtime = WorkflowRuntime()

    state = runtime.start("question", profile)
    assert isinstance(state, WorkflowRunState)
    assert state.status == "running"
    assert [step.status for step in state.steps] == ["pending", "pending"]

    state = runtime.advance(state, profile)
    context = runtime.render_context(state)

    assert state.status == "completed"
    assert [step.status for step in state.steps] == ["ok", "ok"]
    assert "Run: workflow_" in context
    assert "Inspect diff." in context


def test_retrieve_uses_visible_kb_search_tool():
    executor = ToolExecutor()
    seen = {}

    def kb_search(**kwargs):
        seen.update(kwargs)
        return "retrieved docs"

    executor.registerTool("kb_search", "search kb", kb_search, risk_level="low")
    profile = _profile([{"type": "retrieve", "id": "docs", "prompt": "Find {target}."}])

    result = WorkflowRuntime().prepare("question", profile, tool_executor=executor)

    assert "retrieved docs" in result.workflow_context
    assert seen == {
        "query": "Find diff.",
        "namespace": "docs",
        "top_k": 2,
        "view": "chunk",
        "block_type": "code",
    }


def test_retrieve_falls_back_to_memory_manager():
    memory = MagicMock()
    memory.init_session.return_value = "memory result"
    profile = _profile([{"type": "retrieve", "id": "memory", "prompt": "Find {target}."}])

    result = WorkflowRuntime().prepare("question", profile, memory_manager=memory)

    assert "memory result" in result.workflow_context
    memory.init_session.assert_called_once_with("Find diff.")


def test_tool_call_invokes_visible_tool_with_formatted_arguments():
    executor = ToolExecutor()
    seen = {}

    def echo(message):
        seen["message"] = message
        return "ok"

    executor.registerTool(
        "echo",
        "echo",
        echo,
        param_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        risk_level="low",
    )
    profile = _profile([
        {"type": "tool_call", "id": "echo", "tool": "echo", "arguments": {"message": "Check {target}"}}
    ], tool_policy={"allow": ["echo"], "max_risk": "low"})

    result = WorkflowRuntime().prepare("question", profile, tool_executor=executor)

    assert seen["message"] == "Check diff"
    assert "echo: ok" in result.workflow_context


def test_tool_call_denied_by_policy_records_error_event():
    executor = ToolExecutor()
    executor.registerTool("shell_exec", "shell", lambda command: "ok", risk_level="high")
    profile = _profile([
        {"type": "tool_call", "id": "shell", "tool": "shell_exec", "arguments": {"command": "pwd"}}
    ], tool_policy={"allow": ["shell_exec"], "max_risk": "low"})

    result = WorkflowRuntime().prepare("question", profile, tool_executor=executor)

    assert result.events[0].status == "error"
    assert "not visible" in result.workflow_context
    assert result.state is not None
    assert result.state.status == "completed"
    assert result.state.error_count == 1


def test_workflow_steps_are_traced():
    profile = _profile([
        {"type": "prompt", "id": "inspect", "prompt": "Inspect {target}."},
    ])
    trace_manager.start_trace("workflow trace")
    try:
        result = WorkflowRuntime().prepare("question", profile)
        spans = list(trace_manager.active.spans)
    finally:
        trace_manager.end_trace()

    workflow_spans = [span for span in spans if span.name == "workflow_step"]
    assert len(workflow_spans) == 1
    span = workflow_spans[0]
    assert span.input["run_id"] == result.state.run_id
    assert span.input["workflow_id"] == "review"
    assert span.input["step_id"] == "inspect"
    assert span.output["status"] == "ok"
    assert span.metadata["status"] == "ok"
