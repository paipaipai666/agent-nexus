"""Tests for agentnexus.evaluation.coherence — CoherenceEvaluator."""

import json
from unittest.mock import patch

from agentnexus.evaluation.coherence import CoherenceEvaluator, CoherenceReport


class TestCoherenceReport:
    def test_defaults(self):
        report = CoherenceReport(trace_id="t1")
        assert report.trace_id == "t1"
        assert report.total_steps == 0
        assert report.coherence_score == 0.0
        assert report.issues == ""

    def test_passed_short_trace(self):
        """Traces with fewer than 4 steps always pass."""
        report = CoherenceReport(trace_id="t1", total_steps=3, coherence_score=0.0)
        assert report.passed is True

    def test_passed_long_trace_high_score(self):
        report = CoherenceReport(trace_id="t1", total_steps=5, coherence_score=9.0)
        assert report.passed is True

    def test_passed_long_trace_low_score(self):
        report = CoherenceReport(trace_id="t1", total_steps=5, coherence_score=7.0)
        assert report.passed is False

    def test_passed_long_trace_edge_score(self):
        report = CoherenceReport(trace_id="t1", total_steps=4, coherence_score=8.5)
        assert report.passed is True

    def test_summary_format(self):
        report = CoherenceReport(trace_id="t1", total_steps=5, coherence_score=8.3)
        s = report.summary()
        assert "t1" in s
        assert "5" in s
        assert "8.3" in s or "8.3/10" in s


class TestCoherenceEvaluatorParseScore:
    def test_chinese_format(self):
        score = CoherenceEvaluator._parse_score("连贯性分数: 8.5")
        assert score == 8.5

    def test_score_label(self):
        score = CoherenceEvaluator._parse_score("Score: 7.2")
        assert score == 7.2

    def test_score_traditional_chinese(self):
        score = CoherenceEvaluator._parse_score("連貫性分數： 9.0")
        assert score == 9.0

    def test_number_with_score_suffix(self):
        score = CoherenceEvaluator._parse_score("7.5 分")
        assert score == 7.5

    def test_bare_number(self):
        score = CoherenceEvaluator._parse_score("The result is 6.8 out of 10")
        assert score == 6.8

    def test_clamps_above_ten(self):
        score = CoherenceEvaluator._parse_score("Score: 15.0")
        assert score == 10.0

    def test_negative_number_becomes_positive(self):
        score = CoherenceEvaluator._parse_score("Score: -3.0")
        # Regex captures "3.0" without the minus sign
        assert score == 3.0

    def test_no_match_returns_default(self):
        score = CoherenceEvaluator._parse_score("no numbers here at all")
        assert score == 5.0

    def test_empty_string_returns_default(self):
        score = CoherenceEvaluator._parse_score("")
        assert score == 5.0


class TestCoherenceEvaluatorEvaluateOne:
    def test_less_than_two_steps(self):
        evaluator = CoherenceEvaluator()
        spans = [{"name": "task"}, {"name": "llm", "start_time": 1}]
        report = evaluator._evaluate_one("t1", spans)
        assert report.total_steps == 1  # only "llm" counted (task filtered)
        assert report.coherence_score == 10.0

    def test_with_judge_llm(self):
        evaluator = CoherenceEvaluator()
        spans = [
            {"name": "task"},
            {"name": "llm", "start_time": 1, "output": "first step",
             "metadata": {"status": "ok"}},
            {"name": "tool", "start_time": 2, "output": "second step",
             "metadata": {"status": "ok"}},
        ]
        with patch("agentnexus.core.judge_llm.get_judge_llm") as mock_get:
            mock_judge = mock_get.return_value
            mock_judge.think.return_value = "连贯性分数: 9.0\n主要问题: 无"
            report = evaluator._evaluate_one("t1", spans)
        assert report.total_steps == 2  # task filtered out
        assert report.coherence_score == 9.0
        assert "主要问题" in report.issues

    def test_judge_fallback_on_exception(self):
        evaluator = CoherenceEvaluator()
        spans = [
            {"name": "llm", "start_time": 1, "output": "first",
             "metadata": {"status": "ok"}},
            {"name": "tool", "start_time": 2, "output": "second",
             "metadata": {"status": "ok"}},
        ]
        with patch("agentnexus.core.judge_llm.get_judge_llm",
                   side_effect=ImportError("no judge")):
            report = evaluator._evaluate_one("t1", spans)
        assert report.total_steps == 2
        assert report.coherence_score == 0.0  # default on exception

    def test_parse_score_on_judge_output(self):
        evaluator = CoherenceEvaluator()
        spans = [
            {"name": "step1", "start_time": 1, "output": "a",
             "metadata": {"status": "ok"}},
            {"name": "step2", "start_time": 2, "output": "b",
             "metadata": {"status": "ok"}},
            {"name": "step3", "start_time": 3, "output": "c",
             "metadata": {"status": "ok"}},
        ]
        with patch("agentnexus.core.judge_llm.get_judge_llm") as mock_get:
            mock_judge = mock_get.return_value
            mock_judge.think.return_value = "连贯性分数: 7.5"
            report = evaluator._evaluate_one("t1", spans)
        assert report.total_steps == 3
        assert report.coherence_score == 7.5


class TestCoherenceEvaluatorFileMethods:
    def test_evaluate_all_no_files(self, tmp_path):
        evaluator = CoherenceEvaluator()
        reports = evaluator.evaluate_all(str(tmp_path))
        assert reports == []

    def test_evaluate_all_fewer_than_3_spans_skipped(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task"},
            {"trace_id": "t1", "name": "llm"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        with patch("agentnexus.core.judge_llm.get_judge_llm") as mock_get:
            mock_judge = mock_get.return_value
            mock_judge.think.return_value = "连贯性分数: 9.0"
            reports = CoherenceEvaluator().evaluate_all(str(tmp_path))
        assert len(reports) == 0  # fewer than 3 spans

    def test_evaluate_all_with_traces(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task"},
            {"trace_id": "t1", "name": "llm", "start_time": 1, "output": "a",
             "metadata": {"status": "ok"}},
            {"trace_id": "t1", "name": "tool", "start_time": 2, "output": "b",
             "metadata": {"status": "ok"}},
            {"trace_id": "t1", "name": "analyst", "start_time": 3, "output": "c",
             "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        with patch("agentnexus.core.judge_llm.get_judge_llm") as mock_get:
            mock_judge = mock_get.return_value
            mock_judge.think.return_value = "连贯性分数: 9.5"
            reports = CoherenceEvaluator().evaluate_all(str(tmp_path))
        assert len(reports) == 1
        assert reports[0].trace_id == "t1"
        assert reports[0].coherence_score == 9.5

    def test_evaluate_trace_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "llm", "start_time": 1, "output": "a",
             "metadata": {"status": "ok"}},
            {"trace_id": "t2", "name": "step1", "start_time": 1, "output": "first",
             "metadata": {"status": "ok"}},
            {"trace_id": "t2", "name": "step2", "start_time": 2, "output": "second",
             "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        with patch("agentnexus.core.judge_llm.get_judge_llm") as mock_get:
            mock_judge = mock_get.return_value
            mock_judge.think.return_value = "连贯性分数: 8.0"
            report = CoherenceEvaluator().evaluate_trace("t2", str(tmp_path))
        assert report is not None
        assert report.trace_id == "t2"
        assert report.coherence_score == 8.0

    def test_evaluate_trace_not_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "llm", "output": "a",
             "metadata": {"status": "ok"}},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        report = CoherenceEvaluator().evaluate_trace("nonexistent", str(tmp_path))
        assert report is None

    def test_bad_json_lines_skipped_in_load_traces(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            "not json",
            '{"trace_id": "t1", "name": "llm", "start_time": 1, "output": "a", "metadata": {"status": "ok"}}',
            '{"trace_id": "t1", "name": "tool", "start_time": 2, "output": "b", "metadata": {"status": "ok"}}',
            '{"trace_id": "t1", "name": "analyst", "start_time": 3, "output": "c", "metadata": {"status": "ok"}}',
        ]
        trace_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        traces = CoherenceEvaluator._load_traces(str(trace_file))
        assert "t1" in traces
        assert len(traces["t1"]) == 3
