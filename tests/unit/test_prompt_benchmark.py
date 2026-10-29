import pytest

from agentnexus.evaluation.prompt_benchmark import (
    RepairWorkspace,
    compare_runs,
    contract_probe,
    grade_delivery,
    load_results,
    run_live,
    summarize,
    write_json,
)


def test_repair_verification_requires_a_successful_check_after_the_last_edit(tmp_path):
    workspace = RepairWorkspace(tmp_path, "absolute")
    assert workspace.run_checks()["passed"] is False
    workspace.edit_file("return value", "return abs(value)")
    assert workspace.grade()["passed"] is False
    assert workspace.run_checks()["passed"] is True
    assert workspace.grade()["passed"] is True
    workspace.edit_file("abs(value)", "value")
    assert workspace.grade()["passed"] is False


def test_reproduction_must_precede_the_first_edit(tmp_path):
    workspace = RepairWorkspace(tmp_path, "absolute")
    workspace.edit_file("return value", "return value * 2")
    assert workspace.run_checks()["passed"] is False
    workspace.edit_file("value * 2", "abs(value)")
    assert workspace.run_checks()["passed"] is True
    assert workspace.grade()["reproduced"] is False


def test_missing_crash_telemetry_is_not_reported_as_zero_cost():
    report = summarize([
        {"category": "profile", "passed": False, "api_error": "", "input_tokens": None},
        {"category": "profile", "passed": True, "api_error": "", "input_tokens": 100},
    ])
    assert report["mean_input_tokens"] == 100
    assert report["input_tokens_samples"] == 1


def test_delivery_review_preserves_artifact_success_but_rejects_incomplete_answer(tmp_path):
    import hashlib

    answer = "接下来给出最终答案。"
    write_json(tmp_path / "attempts" / "a.json",
               {"case_id": "a", "repeat": 0, "category": "repair", "passed": True,
                "artifact_passed": True, "answer": answer})
    write_json(tmp_path / "delivery-audit.json", {"judgments": [{
        "case_id": "a", "repeat": 0, "delivery_passed": False,
        "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(), "reason": "Only future intent",
    }]})
    row = load_results(tmp_path)[0]
    assert row["artifact_passed"] is True
    assert row["passed"] is False


def test_delivery_review_rejects_a_changed_answer(tmp_path):
    write_json(tmp_path / "attempts" / "a.json",
               {"case_id": "a", "repeat": 0, "category": "repair", "passed": True, "answer": "changed"})
    write_json(tmp_path / "delivery-audit.json", {"judgments": [{
        "case_id": "a", "repeat": 0, "delivery_passed": False, "answer_sha256": "0" * 64, "reason": "review",
    }]})
    with pytest.raises(ValueError, match="fingerprint"):
        load_results(tmp_path)


def test_comparison_separates_cheap_failures_from_common_success_costs():
    before = [
        {"case_id": "p", "repeat": 0, "category": "profile", "passed": False, "input_tokens": 100},
        {"case_id": "q", "repeat": 0, "category": "lookup", "passed": True, "input_tokens": 200},
    ]
    after = [
        {"case_id": "p", "repeat": 0, "category": "profile", "passed": True, "input_tokens": 20},
        {"case_id": "q", "repeat": 0, "category": "lookup", "passed": False, "input_tokens": 10},
    ]
    report = compare_runs(before, after)
    assert report["non_profile"]["before_passed"] == 1
    assert report["non_profile"]["after_passed"] == 0
    assert report["common_success_metrics"]["input_tokens"]["samples"] == 0


def test_summary_keeps_api_errors_out_of_behavioral_denominator():
    result = summarize([
        {"category": "repair", "passed": True, "api_error": "", "input_tokens": 100},
        {"category": "repair", "passed": False, "api_error": "", "input_tokens": 200},
        {"category": "repair", "passed": False, "api_error": "429", "input_tokens": 0},
    ])
    assert result["attempts"] == 3
    assert result["valid_attempts"] == 2
    assert result["api_errors"] == 1
    assert result["pass_rate"] == 0.5
    assert result["mean_input_tokens"] == 150


def test_comparison_pairs_cases_and_repeats_instead_of_aggregate_denominators():
    old = [{"case_id": "a", "category": "repair", "repeat": 0, "passed": False, "api_error": ""}]
    new = [{"case_id": "a", "category": "repair", "repeat": 0, "passed": True, "api_error": ""}]
    assert compare_runs(old, new)["improved"] == 1
    assert compare_runs(old, new)["regressed"] == 0
    assert compare_runs(old, new)["paired_delta"] == 1


def test_comparison_rejects_duplicate_attempts():
    rows = [{"case_id": "a", "repeat": 0, "passed": True, "api_error": ""}]
    with pytest.raises(ValueError, match="duplicate"):
        compare_runs(rows + rows, rows)


def test_comparison_rejects_partial_fixture_coverage():
    old = [{"case_id": "a", "repeat": 0, "passed": True, "api_error": ""}]
    with pytest.raises(ValueError, match="coverage"):
        compare_runs(old, [])


def test_live_attempt_preserves_strategy_and_artifact_in_json(tmp_path):
    from types import SimpleNamespace

    from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker

    llm = SimpleNamespace(
        model="fixture", capabilities=ModelCapabilities(supports_tool_calling=True),
        session_tracker=SessionCapabilityTracker(), last_error="", last_truncated=False,
        last_reasoning_content="", last_tool_calls=[], last_usage={}, total_usage={},
        think=lambda **kw: "未执行修复。",
    )
    row = run_live({"category": "repair", "kind": "absolute", "question": "修复文件"}, llm, tmp_path, 3)
    write_json(tmp_path / "attempt.json", row)
    import json

    saved = json.loads((tmp_path / "attempt.json").read_text(encoding="utf-8"))
    assert saved["passed"] is False
    assert saved["steps"][0]["strategy_used"] == "NATIVE_TOOLS"


@pytest.mark.parametrize("case_id", [
    "custom-profile-False", "custom-profile-True",
    "neutral-observation-False", "neutral-observation-True",
])
def test_agent_request_respects_prompt_contracts(case_id):
    row = next(row for row in contract_probe() if row["case_id"] == case_id)
    assert row["passed"], row["requests"]


def test_live_cli_keeps_reports_outside_the_disposable_workspace(tmp_path, monkeypatch):
    import json
    import sys

    from agentnexus.core.config import LLMProvider, ModelEntry, get_settings
    from agentnexus.core.llm import AgentLLM
    from agentnexus.core.providers.base import StreamResult
    from agentnexus.evaluation.prompt_benchmark import main

    settings = get_settings()
    settings.llm_providers = [LLMProvider(name="agnes", base_url="https://example.invalid/v1",
                                        api_key="fixture", models=[ModelEntry(model_id="agnes-3.0-flash")])]
    monkeypatch.setattr(AgentLLM, "_call_via_provider", lambda *a, **kw: StreamResult(
        text='{"answer":"未完成"}', usage={"input_tokens": 10, "output_tokens": 5}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["benchmark", "run", "--output", "reports", "--limit", "1", "--repeats", "1"])
    main()
    summary = json.loads((tmp_path / "reports" / "summary.json").read_text(encoding="utf-8"))
    assert summary["attempts"] == 1
    assert summary["valid_attempts"] == 1
    attempt = json.loads(next((tmp_path / "reports" / "attempts").glob("*.json")).read_text(encoding="utf-8"))
    assert attempt["input_tokens"] == 10
    assert attempt["requests"][0]["tools"][0]["function"]["name"] == "todo_add"
    assert attempt["requests"][0]["temperature"] == 0


def test_budget_exhaustion_is_not_complete_delivery_even_if_the_artifact_passes():
    row = grade_delivery({"passed": True, "answer": "（已达到最大步数 10，共执行 10 步，任务未完成。）",
                          "steps": [{"tool_calls": [], "tool_outputs": [{"tool": "file_read"}]}]})
    assert row["artifact_passed"] is True
    assert row["passed"] is False
    assert row["budget_exhausted"] is True
    assert row["tool_calls"] == 1


def test_compare_cli_rejects_unrelated_runtime_drift(tmp_path, monkeypatch):
    import sys

    from agentnexus.evaluation.prompt_benchmark import main

    controls = {"model": "agnes/fixture", "repeats": 1, "rpm": 6, "max_steps": 12,
                "dataset_sha256": "fixture", "temperature": 0, "protocol": "native",
                "thinking_effort": "none", "max_output_tokens": 2048,
                "dataset": [{"id": "a"}], "runtime_sha256": {"agentnexus/core/config.py": "old"}}
    for phase in ("before", "after"):
        manifest = {**controls, "runtime_sha256": {"agentnexus/core/config.py": phase}}
        write_json(tmp_path / phase / "manifest.json", {"experiment": manifest})
        write_json(tmp_path / phase / "attempts" / "a.json",
                   {"case_id": "a", "category": "repair", "repeat": 0, "passed": True, "api_error": ""})
    monkeypatch.setattr(sys, "argv", ["benchmark", "compare", "--before", str(tmp_path / "before"),
                                     "--after", str(tmp_path / "after"), "--output", str(tmp_path / "result.json")])
    with pytest.raises(ValueError, match="runtime"):
        main()


@pytest.mark.parametrize("frozen_evaluator", [False, True])
def test_snapshot_replay_uses_the_selected_evaluator(tmp_path, monkeypatch, frozen_evaluator):
    import sys
    from pathlib import Path

    import agentnexus.evaluation.prompt_benchmark as benchmark

    snapshot = tmp_path / "sources"
    for name in benchmark.SOURCE_PATHS:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("frozen fixture", encoding="utf-8")
    observed = []

    def execute(command, *, cwd, env, check):
        staged = Path(cwd)
        observed.append((staged / "agentnexus/agents/re_act_agent.py").read_text(encoding="utf-8"))
        observed.append((staged / "agentnexus/evaluation/prompt_benchmark.py").read_text(encoding="utf-8"))
        assert env["PYTHONPATH"] == str(staged)
        assert "native" in command

    monkeypatch.setattr(benchmark.subprocess, "run", execute)
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *a, **kw: "fixture-revision\n")
    monkeypatch.setattr(sys, "argv", ["benchmark", "run", "--output", str(tmp_path / "run"),
                                     "--source-snapshot", str(snapshot)]
                        + (["--frozen-evaluator"] if frozen_evaluator else []))
    benchmark.main()
    assert observed[0] == "frozen fixture"
    assert observed[1] == ("frozen fixture" if frozen_evaluator else Path(benchmark.__file__).read_text(encoding="utf-8"))


def test_custom_template_keeps_its_own_tool_heading(monkeypatch):
    from types import SimpleNamespace

    from agentnexus.agents.re_act_agent import ReActAgent
    from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker
    from agentnexus.skills.workflow import Workflow
    from agentnexus.tools.registry import ToolRegistry

    requests = []

    def respond(**kw):
        requests.append(kw["messages"])
        return "完成。"

    llm = SimpleNamespace(model="fixture", capabilities=ModelCapabilities(supports_tool_calling=True),
                          session_tracker=SessionCapabilityTracker(), last_error="", last_truncated=False,
                          last_reasoning_content="", last_tool_calls=[], last_usage={}, total_usage={}, think=respond)
    template = "专用工作流\n== 可用工具 ==\n只能引用工具中实际返回的证据。"
    monkeypatch.setattr("agentnexus.skills.profile.load_prompt", lambda _: template)
    profile = Workflow.model_validate({"id": "custom", "version": "1", "display_name": "custom",
                                       "prompt_profile": {"system": "custom"}, "tool_policy": {},
                                       "steps": [], "success_criteria": []}).to_session_profile()
    registry = ToolRegistry()
    try:
        agent = ReActAgent(llm, registry, max_steps=2, output=lambda *_: None)
        agent.set_session_profile(profile)
        agent.run("执行当前工作流")
        assert requests[0][0] == {"role": "system", "content": template}
    finally:
        registry.close(wait=True)


def test_agent_crash_is_recorded_as_failed_delivery(tmp_path):
    from types import SimpleNamespace

    from agentnexus.core.capabilities import ModelCapabilities, SessionCapabilityTracker

    llm = SimpleNamespace(model="fixture", capabilities=ModelCapabilities(supports_tool_calling=True),
                          session_tracker=SessionCapabilityTracker(), last_error="", last_truncated=False,
                          last_reasoning_content="", last_tool_calls=[], last_usage={}, total_usage={})
    def respond(**kw):
        raise RuntimeError("fixture agent failure")

    llm.think = respond
    row = run_live({"category": "profile", "expected": "WORKFLOW-821", "question": "输出校验码"},
                   llm, tmp_path, 3)
    assert row["passed"] is False
    assert row["agent_error"] == "RuntimeError: fixture agent failure"
