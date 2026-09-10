"""Fine-grained eval framework — offline unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentnexus.evaluation.fine_grained.cases import (
    DATASET_PRIMARY_METRIC,
    SCOPE_DATASETS,
    EvalCase,
    cases_for_scope,
    datasets_for_scope,
)
from agentnexus.evaluation.fine_grained.report import (
    CaseOutcome,
    FineGrainedReport,
    RunMetrics,
)
from agentnexus.evaluation.fine_grained.runner import FineGrainedRunner


class TestScopeAssembly:
    def test_all_scopes_resolve(self):
        for scope in SCOPE_DATASETS:
            assert datasets_for_scope(scope)  # non-empty
            cases_for_scope(scope)  # no KeyError

    def test_unknown_scope_raises(self):
        with pytest.raises(ValueError, match="unknown scope"):
            cases_for_scope("nowhere")

    def test_full_covers_all_datasets(self):
        full = set(datasets_for_scope("full"))
        for scope in ("perception", "planning", "tools", "e2e", "memory"):
            assert set(datasets_for_scope(scope)) <= full

    def test_every_dataset_has_primary_metric(self):
        for scope_datasets in SCOPE_DATASETS.values():
            for ds in scope_datasets:
                assert ds in DATASET_PRIMARY_METRIC, ds


class TestPerceptionOffline:
    """技能路由评测确定性可跑（无需 LLM）。"""

    def test_perception_scope_produces_intent_metrics(self):
        runner = FineGrainedRunner(generator=MagicMock(), judge_llm=None)
        report = runner.run("perception")
        assert len(report.outcomes) == 14
        table = report.metrics_table()
        assert "perception_route" in table
        assert "intent_accuracy" in table["perception_route"]
        # 每个用例都有具体数值而非只有成败
        for o in report.outcomes:
            assert o.metric_values["intent_accuracy"] in (0.0, 1.0)
            assert "期望=" in o.reasoning and "实际=" in o.reasoning


class TestMemoryScopeOffline:
    def test_memory_scope_produces_dimension_metrics(self):
        runner = FineGrainedRunner(generator=MagicMock(), judge_llm=None)
        report = runner.run("memory")
        assert report.memory_dimensions
        assert all(0.0 <= v <= 1.0 for v in report.memory_dimensions.values())
        assert report.memory_probe_rows


class TestFrameworkSelfCheck:
    """框架自检：种入必失败的用例，框架必须报 fail——永远全绿的评测不可信。"""

    def test_wrong_expectation_produces_failure(self):
        case = EvalCase(id="SELF-001", dataset="perception_route",
                        user_input="你好", expected_skill="nonexistent_skill")
        runner = FineGrainedRunner(generator=MagicMock(), judge_llm=None)
        out = runner._run_perception(case)
        assert out.verdict == "fail"
        assert out.metric_values["intent_accuracy"] == 0.0
        assert "nonexistent_skill" in out.reasoning


class _SeqJudgeLLM:
    """按序返回罐装 judge 响应。"""

    def __init__(self, responses):
        self._responses = responses
        self._i = 0

    def think(self, messages, silent=False):
        r = self._responses[self._i % len(self._responses)]
        self._i += 1
        return r


class TestJudgeCalibration:
    def test_full_agreement(self):
        from agentnexus.evaluation.fine_grained.cases import JUDGE_CALIBRATION
        from agentnexus.evaluation.fine_grained.report import Judges

        canned = [
            '{"verdict": "fail", "reasoning": "r"}',      # task_completion fail
            '{"verdict": "pass", "reasoning": "r"}',      # task_completion pass
            '{"verdict": "fail", "reasoning": "r"}',      # instruction_following fail
            '{"verdict": "fail", "reasoning": "r"}',      # faithfulness fail
            '{"verdict": "pass", "reasoning": "r"}',      # faithfulness pass
            '{"behavior": "violated", "reasoning": "r"}',  # abnormal violated
            '{"behavior": "refused", "reasoning": "r"}',   # abnormal refused
        ]
        runner = FineGrainedRunner(generator=MagicMock(), judge_llm=None)
        runner.judges = Judges(_SeqJudgeLLM(canned))
        outcomes = runner._run_judge_calibration()
        assert len(outcomes) == len(JUDGE_CALIBRATION)
        assert all(o.verdict == "pass" for o in outcomes)

    def test_disagreement_detected(self):
        from agentnexus.evaluation.fine_grained.report import Judges

        # 第一个 judge 答错（期望 fail 给了 pass）
        canned = [
            '{"verdict": "pass", "reasoning": "r"}',      # 期望 fail → 不一致
            '{"verdict": "pass", "reasoning": "r"}',
            '{"verdict": "fail", "reasoning": "r"}',
            '{"verdict": "fail", "reasoning": "r"}',
            '{"verdict": "pass", "reasoning": "r"}',
            '{"behavior": "violated", "reasoning": "r"}',
            '{"behavior": "refused", "reasoning": "r"}',
        ]
        runner = FineGrainedRunner(generator=MagicMock(), judge_llm=None)
        runner.judges = Judges(_SeqJudgeLLM(canned))
        outcomes = runner._run_judge_calibration()
        assert outcomes[0].verdict == "fail"
        assert sum(o.metric_values["judge_agreement_rate"] for o in outcomes) == 6.0


class TestReportDataFirst:
    def _outcome(self, case_id, dataset, verdict, metrics, wall=100.0):
        return CaseOutcome(
            case_id=case_id, dataset=dataset, verdict=verdict,
            metric_values=metrics,
            run=RunMetrics(wall_ms=wall, steps=2, llm_calls=2,
                           input_tokens=100, output_tokens=50),
        )

    def test_metrics_table_has_counts(self):
        report = FineGrainedReport(scope="e2e", outcomes=[
            self._outcome("E-1", "e2e_task", "pass", {"task_completion": 1.0}),
            self._outcome("E-2", "e2e_task", "fail", {"task_completion": 0.0}),
            self._outcome("E-3", "e2e_task", "skip", {}),
        ])
        row = report.metrics_table()["e2e_task"]
        # skip 不计入分母：1/2 而非 1/3
        assert row["task_completion"] == "0.50 (1/2)"

    def test_cost_perf_aggregates(self):
        report = FineGrainedReport(scope="e2e", outcomes=[
            self._outcome("E-1", "e2e_task", "pass", {}),
            self._outcome("E-2", "e2e_task", "pass", {}),
        ])
        cp = report.cost_perf()
        assert cp["总输入tokens"] == 200
        assert cp["平均工具调用/用例"] == 0
        assert cp["用例数"] == 2

    def test_summary_is_data_first(self):
        report = FineGrainedReport(scope="e2e", outcomes=[
            self._outcome("E-1", "e2e_task", "fail", {"task_completion": 0.0},
                          ),
        ])
        text = report.summary()
        assert "0.00 (0/1)" in text          # 具体数值
        assert "用例结果分布" in text
        assert "PASS" not in text.split("质量")[0]  # 头部不是 PASS/FAIL 大字报

    def test_to_dict_roundtrip(self):
        report = FineGrainedReport(scope="e2e", outcomes=[
            self._outcome("E-1", "e2e_task", "pass", {"task_completion": 1.0}),
        ])
        d = report.to_dict()
        assert d["metrics"]["e2e_task"]["task_completion"] == "1.00 (1/1)"
        assert d["outcomes"][0]["run"]["input_tokens"] == 100
