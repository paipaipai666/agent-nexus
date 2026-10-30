"""Evaluation trials consume the selected engine and retained memory."""

from dataclasses import replace

import pytest

from agentnexus.core.memory_contracts import TurnMemory
from agentnexus.core.modules import ModuleFactories, ModuleHost, ModuleUnavailable


def block_default_engine(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "agentnexus.agents.re_act_agent":
            raise AssertionError("Evaluation reconstructed the default engine")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


def test_fine_grained_cases_use_selected_engine_and_memory(monkeypatch):
    from agentnexus.evaluation.fine_grained.cases import EvalCase
    from agentnexus.evaluation.fine_grained.runner import FineGrainedRunner

    contexts, memories = [], []
    retained = TurnMemory()

    class Engine:
        def run(self, question, *, memory_manager=None):
            if memory_manager is not None:
                memories.append(memory_manager.short_term)
            return "selected answer"

    def factory(context):
        contexts.append(context)
        return Engine()

    host = ModuleHost(factories=replace(ModuleFactories.disabled(), agent=factory,
                                        short_term=lambda context: retained))
    block_default_engine(monkeypatch)
    runner = FineGrainedRunner(generator=object(), run_memory=False)
    with host.activate():
        outcome = runner._run_agent_case(EvalCase("selected", "e2e_task", "question"))
        runner._run_multi_turn(EvalCase("chain", "multi_turn", "", chain=[{"user_input": "first"}, {"user_input": "second"}]))
    assert outcome.answer_preview == "selected answer"
    assert outcome.run.input_tokens == 0
    assert memories == [retained, retained]
    assert len(contexts) == 2
    assert contexts[0].max_steps == 6


@pytest.mark.parametrize("kind", ["fine_grained", "benchmark", "holdout"])
def test_evaluation_never_reconstructs_a_disabled_engine(kind, tmp_path, monkeypatch):
    from agentnexus.evaluation.fine_grained.runner import FineGrainedRunner
    from agentnexus.evaluation.prompt_benchmark import run_live
    from agentnexus.evaluation.prompt_holdout import run_case

    block_default_engine(monkeypatch)
    host = ModuleHost(factories=ModuleFactories.disabled())
    case = {"id": "selected", "category": "lookup", "key": "release", "expected": "value", "question": "question"}
    with host.activate(), pytest.raises(ModuleUnavailable, match="agent"):
        if kind == "fine_grained":
            FineGrainedRunner(generator=object())._make_agent(object())
        elif kind == "benchmark":
            run_live(case, object(), tmp_path, 3)
        else:
            run_case(case, object(), tmp_path, 3)


@pytest.mark.parametrize("kind", ["benchmark", "holdout"])
def test_prompt_evaluation_accepts_a_plain_selected_engine(kind, tmp_path, monkeypatch):
    from agentnexus.evaluation.prompt_benchmark import run_live
    from agentnexus.evaluation.prompt_holdout import run_case

    contexts = []

    class Engine:
        def run(self, question):
            return "selected answer"

    def factory(context):
        contexts.append(context)
        return Engine()

    host = ModuleHost(factories=replace(ModuleFactories.disabled(), agent=factory))
    block_default_engine(monkeypatch)
    case = {"id": "selected", "category": "lookup", "key": "release", "expected": "value", "question": "question"}
    with host.activate():
        row = run_live(case, object(), tmp_path, 3) if kind == "benchmark" else run_case(case, object(), tmp_path, 3)
    assert row["answer"] == "selected answer"
    assert row["steps"] == []
    assert "agent_error" not in row
    assert contexts[0].max_steps == 3


def test_contract_probe_uses_selected_engine_and_marks_unsupported_capabilities(monkeypatch):
    from agentnexus.evaluation.prompt_benchmark import contract_probe

    class Engine:
        def run(self, question):
            return "opaque provider answer"

    host = ModuleHost(factories=replace(ModuleFactories.disabled(), agent=lambda context: Engine()))
    block_default_engine(monkeypatch)
    with host.activate():
        rows = contract_probe()
    assert len(rows) == 4
    assert all(row["passed"] is False for row in rows)
    assert all("unsupported" in row for row in rows)


def test_profile_evaluation_reports_an_unsupported_selected_engine(tmp_path):
    from agentnexus.evaluation.prompt_benchmark import run_live

    class Engine:
        def run(self, question):
            return "unconfigured answer"

    host = ModuleHost(factories=replace(ModuleFactories.disabled(), agent=lambda context: Engine()))
    case = {"id": "profile", "category": "profile", "expected": "profile answer", "question": "question"}
    with host.activate():
        row = run_live(case, object(), tmp_path, 3)
    assert row["passed"] is False
    assert "does not support session profiles" in row["agent_error"]


@pytest.mark.parametrize("kind", ["benchmark", "holdout"])
@pytest.mark.parametrize("custom", [False, True])
def test_default_prompt_experiments_reject_other_engine_selections(kind, custom, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from agentnexus.evaluation.prompt_benchmark import main
    from agentnexus.evaluation.prompt_holdout import run_child

    factories = ModuleFactories.disabled()
    if custom:
        factories = replace(factories, agent=lambda context: object())
    host = ModuleHost(factories=factories)
    block_default_engine(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["benchmark", "run", "--output", str(tmp_path), "--limit", "1"])
    error = ValueError if custom else ModuleUnavailable
    message = "default engine" if custom else "agent"
    with host.activate(), pytest.raises(error, match=message):
        if kind == "benchmark":
            main()
        else:
            run_child(SimpleNamespace(output=tmp_path))


@pytest.mark.parametrize("observed", [False, True])
@pytest.mark.parametrize("task", ["empty", "answered", "aborted"])
def test_eval_ci_ignores_diagnostics_and_keeps_real_task_failures(observed, task, tmp_path):
    import json

    from typer.testing import CliRunner

    from agentnexus.cli import app
    from agentnexus.core.config import Settings
    from agentnexus.core.modules import DEFAULT

    rows = [{"trace_id": "maintenance", "name": "hook_fire", "metadata": {"status": "ok"}}]
    if task != "empty":
        rows.append({"trace_id": "saved-task", "name": "task", "input": {"task": "saved question"}})
    if task == "answered":
        rows.append({"trace_id": "saved-task", "name": "llm", "metadata": {"status": "ok", "tool_calls": []}})
    (tmp_path / "record.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    factories = replace(ModuleFactories.disabled(), evaluation=DEFAULT, observability=DEFAULT if observed else None)
    host = ModuleHost(Settings(traces_dir=str(tmp_path)), factories=factories)
    try:
        with host.activate():
            result = CliRunner().invoke(app, ["eval", "ci", "-d", "1"])
        assert result.exit_code == (1 if task == "aborted" else 0), result.stdout
        if task == "empty":
            assert "No traces to evaluate" in result.stdout
        else:
            assert "1 条 trace" in result.stdout
    finally:
        host.close()
