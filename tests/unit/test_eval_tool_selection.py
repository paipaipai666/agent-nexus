"""Tests for agentnexus.evaluation.tool_selection — ToolSelectionEvaluator."""

import json

from agentnexus.evaluation.tool_selection import (
    ToolSelectionEvaluator,
    ToolSelectionReport,
)


class TestToolSelectionReport:
    def test_defaults(self):
        report = ToolSelectionReport()
        assert report.total_queries == 0
        assert report.correct == 0
        assert report.accuracy == 0.0
        assert report.by_tool == {}
        assert report.mismatches == []
        assert report.passed is False  # 0.0 >= 0.92 → False

    def test_passed_high_accuracy(self):
        report = ToolSelectionReport(total_queries=100, correct=95, accuracy=0.95)
        assert report.passed is True

    def test_passed_edge(self):
        report = ToolSelectionReport(total_queries=100, correct=92, accuracy=0.92)
        assert report.passed is True

    def test_passed_below_threshold(self):
        report = ToolSelectionReport(total_queries=100, correct=80, accuracy=0.80)
        assert report.passed is False

    def test_summary_format(self):
        report = ToolSelectionReport(
            total_queries=10, correct=9, accuracy=0.9,
            by_tool={"web_search": {"total": 5, "correct": 5},
                     "python_execute": {"total": 5, "correct": 4}},
        )
        s = report.summary()
        assert "90.0%" in s or "90%" in s
        assert "9/10" in s
        assert "web_search" in s
        assert "python_execute" in s

    def test_summary_zero_tool_count(self):
        report = ToolSelectionReport(
            total_queries=0, correct=0, accuracy=0.0,
            by_tool={"web_search": {"total": 0, "correct": 0}},
        )
        s = report.summary()
        assert "0.0%" in s or "0%" in s


class TestToolSelectionEvaluatorClassifyQuery:
    def setup_method(self):
        self.evaluator = ToolSelectionEvaluator()

    def test_web_search_keyword(self):
        assert self.evaluator._classify_query("搜索最新的AI新闻") == "web_search"
        assert self.evaluator._classify_query("search for python tutorials") == "web_search"
        assert self.evaluator._classify_query("查询今天的天气") == "web_search"
        assert self.evaluator._classify_query("最新科技动态") == "web_search"

    def test_python_execute_keyword(self):
        assert self.evaluator._classify_query("写一段代码计算斐波那契") == "python_execute"
        assert self.evaluator._classify_query("code a sorting algorithm") == "python_execute"
        assert self.evaluator._classify_query("计算1到100的和") == "python_execute"
        assert self.evaluator._classify_query("运行这个python脚本") == "python_execute"
        assert self.evaluator._classify_query("生成图表展示数据") == "python_execute"

    def test_memory_search_keyword(self):
        assert self.evaluator._classify_query("记忆中有没有相关讨论") == "memory_search"
        assert self.evaluator._classify_query("我的偏好设置是什么") == "memory_search"
        assert self.evaluator._classify_query("记住这个配置") == "memory_search"
        assert self.evaluator._classify_query("之前说过什么") == "memory_search"

    def test_default_maps_to_web_search(self):
        assert self.evaluator._classify_query("hello world") == "web_search"
        assert self.evaluator._classify_query("unknown task") == "web_search"

    def test_custom_eval_set(self):
        custom = {"custom_keyword": "custom_tool"}
        evaluator = ToolSelectionEvaluator(eval_set=custom)
        assert evaluator._classify_query("custom_keyword test") == "custom_tool"
        assert evaluator._classify_query("something else") == "web_search"


class TestToolSelectionEvaluatorGetTaskFromTrace:
    def test_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task", "input": {"task": "搜索最新的AI新闻"}},
            {"trace_id": "t1", "name": "research_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        result = ToolSelectionEvaluator._get_task_from_trace(str(tmp_path), "t1")
        assert result == "搜索最新的AI新闻"

    def test_not_found(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "research_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        result = ToolSelectionEvaluator._get_task_from_trace(str(tmp_path), "t1")
        assert result is None

    def test_empty_directory(self, tmp_path):
        result = ToolSelectionEvaluator._get_task_from_trace(str(tmp_path), "t1")
        assert result is None


class TestToolSelectionEvaluatorEvaluateFromTraces:
    def test_empty_directory(self, tmp_path):
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 0
        assert report.accuracy == 0.0

    def test_correct_tool_selection(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task", "input": {"task": "搜索最新的AI新闻"}},
            {"trace_id": "t1", "name": "research_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 1
        assert report.correct == 1
        assert report.accuracy == 1.0

    def test_incorrect_tool_selection(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task", "input": {"task": "搜索最新的AI新闻"}},
            {"trace_id": "t1", "name": "execute_node"},  # should be research_node
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 1
        assert report.correct == 0
        assert report.accuracy == 0.0
        assert len(report.mismatches) == 1
        assert report.mismatches[0]["expected"] == "web_search"
        assert report.mismatches[0]["actual"] == "python_execute"

    def test_multiple_traces(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task", "input": {"task": "搜索新闻"}},
            {"trace_id": "t1", "name": "research_node"},
            {"trace_id": "t2", "name": "task", "input": {"task": "写代码排序"}},
            {"trace_id": "t2", "name": "execute_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 2
        assert report.correct == 2
        assert report.accuracy == 1.0
        assert report.by_tool["web_search"]["total"] == 1
        assert report.by_tool["python_execute"]["total"] == 1
        assert report.by_tool["web_search"]["correct"] == 1
        assert report.by_tool["python_execute"]["correct"] == 1

    def test_skip_no_task(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "research_node"},
            {"trace_id": "t1", "name": "execute_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 0  # no task span found

    def test_skip_neither_research_nor_execute(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            {"trace_id": "t1", "name": "task", "input": {"task": "搜索新闻"}},
            {"trace_id": "t1", "name": "analyst_node"},
        ]
        trace_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                              encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 0

    def test_bad_json_lines_skipped(self, tmp_path):
        trace_file = tmp_path / "trace.jsonl"
        lines = [
            "not json",
            '{"trace_id": "t1", "name": "task", "input": {"task": "搜索新闻"}}',
            '{"trace_id": "t1", "name": "research_node"}',
        ]
        trace_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(tmp_path))
        assert report.total_queries == 1
        assert report.correct == 1
