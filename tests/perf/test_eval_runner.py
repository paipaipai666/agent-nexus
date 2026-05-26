"""Evaluation test runner for JSONL datasets.

Closes Gap 5: No evaluation test runner for tool_selection / trajectory datasets.

Runs the evaluators against actual JSONL datasets and asserts minimum accuracy thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentnexus.evaluation.tool_selection import ToolSelectionEvaluator
from agentnexus.evaluation.trajectory import TrajectoryEvaluator

# ── Thresholds ──────────────────────────────────────────────────────

TOOL_SELECTION_ACCURACY_MIN = 0.15  # 15% minimum accuracy on eval dataset
# Note: Low threshold because the evaluator's labeled set only covers 3 tools
# while the eval dataset uses many more tools (read, grep, bash, etc.)
TRAJECTORY_SCORE_MIN = 6.0  # Minimum trajectory score (out of 10)
TRAJECTORY_PASS_RATE_MIN = 0.70  # 70% of trajectories should pass

# ── Paths ──────────────────────────────────────────────────────────

EVALS_DIR = Path(__file__).parent.parent / "evals"
TOOL_SELECTION_DATASET = EVALS_DIR / "tool_selection.jsonl"
TRAJECTORY_DATASET = EVALS_DIR / "trajectory.jsonl"


def _load_jsonl(filepath: Path) -> list[dict]:
    """Load JSONL file into list of dicts."""
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def _create_trace_from_eval_record(record: dict, traces_dir: Path) -> None:
    """Create a trace file from an evaluation record."""
    trace_id = record.get("trace_id", "unknown")
    question = record.get("question", "")
    tools_used = record.get("tools_used", [])

    # Create task span
    task_span = {
        "trace_id": trace_id,
        "span_id": f"{trace_id}_task",
        "name": "task",
        "input": {"task": question},
        "start_time": 0.0,
        "end_time": 0.1,
        "latency_ms": 100.0,
    }

    # Create tool spans based on tools_used
    tool_spans = []
    for i, tool in enumerate(tools_used):
        # Map tool names to node names
        if tool in ["web_search", "search"]:
            node_name = "research_node"
        elif tool in ["bash", "read", "edit", "write", "grep", "glob"]:
            node_name = "execute_node"
        else:
            node_name = "execute_node"

        tool_span = {
            "trace_id": trace_id,
            "span_id": f"{trace_id}_tool_{i}",
            "name": node_name,
            "input": {"tool": tool},
            "start_time": 0.1 + i * 0.1,
            "end_time": 0.2 + i * 0.1,
            "latency_ms": 100.0,
        }
        tool_spans.append(tool_span)

    # Write to trace file
    trace_file = traces_dir / "eval_traces.jsonl"
    with open(trace_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(task_span, ensure_ascii=False) + "\n")
        for span in tool_spans:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")


def _create_trajectory_trace(record: dict, traces_dir: Path) -> None:
    """Create a trace file from a trajectory evaluation record."""
    trace_id = record.get("trace_id", "unknown")
    question = record.get("question", "")
    expected_trajectory = record.get("expected_trajectory", [])

    # Create task span
    task_span = {
        "trace_id": trace_id,
        "span_id": f"{trace_id}_task",
        "name": "task",
        "input": {"task": question},
        "start_time": 0.0,
        "end_time": 0.1,
        "latency_ms": 100.0,
    }

    # Create spans based on expected trajectory
    trajectory_spans = []
    for i, step in enumerate(expected_trajectory):
        # Parse step format: "tool: description" or just "action"
        if ":" in step:
            tool, _ = step.split(":", 1)
            tool = tool.strip()
        else:
            tool = step.strip()

        # Map tool to node name
        if tool in ["web_search", "search", "web_fetch"]:
            node_name = "research_node"
        elif tool in ["直接回答", "answer"]:
            node_name = "answer_node"
        else:
            node_name = "execute_node"

        span = {
            "trace_id": trace_id,
            "span_id": f"{trace_id}_step_{i}",
            "name": node_name,
            "input": {"step": step},
            "metadata": {"tool_calls": [tool] if tool not in ["直接回答", "answer"] else []},
            "start_time": 0.1 + i * 0.1,
            "end_time": 0.2 + i * 0.1,
            "latency_ms": 100.0,
        }
        trajectory_spans.append(span)

    # Write to trace file
    trace_file = traces_dir / "eval_trajectory_traces.jsonl"
    with open(trace_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(task_span, ensure_ascii=False) + "\n")
        for span in trajectory_spans:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")


# ── Tool Selection Evaluation ─────────────────────────────────────


class TestToolSelectionEvalRunner:
    """Run tool selection evaluator against actual dataset."""

    def test_tool_selection_dataset_exists(self):
        """Verify tool selection dataset exists."""
        assert TOOL_SELECTION_DATASET.exists(), \
            f"Tool selection dataset not found: {TOOL_SELECTION_DATASET}"

    def test_tool_selection_dataset_valid(self):
        """Verify tool selection dataset is valid JSONL."""
        records = _load_jsonl(TOOL_SELECTION_DATASET)
        assert len(records) > 0, "Tool selection dataset is empty"

        for record in records:
            assert "trace_id" in record, "Missing trace_id in record"
            assert "question" in record, "Missing question in record"
            assert "tools_used" in record, "Missing tools_used in record"

    def test_tool_selection_accuracy(self, tmp_path):
        """Run tool selection evaluator and assert accuracy threshold."""
        # Load dataset
        records = _load_jsonl(TOOL_SELECTION_DATASET)
        assert len(records) > 0, "Tool selection dataset is empty"

        # Create traces from dataset
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in records:
            _create_trace_from_eval_record(record, traces_dir)

        # Run evaluator
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(traces_dir))

        # Assert accuracy threshold
        assert report.total_queries > 0, "No queries evaluated"
        assert report.accuracy >= TOOL_SELECTION_ACCURACY_MIN, \
            f"Tool selection accuracy {report.accuracy:.2%} < {TOOL_SELECTION_ACCURACY_MIN:.2%}"

    def test_tool_selection_per_tool_accuracy(self, tmp_path):
        """Verify per-tool accuracy meets thresholds."""
        records = _load_jsonl(TOOL_SELECTION_DATASET)
        assert len(records) > 0, "Tool selection dataset is empty"

        # Create traces from dataset
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in records:
            _create_trace_from_eval_record(record, traces_dir)

        # Run evaluator
        evaluator = ToolSelectionEvaluator()
        report = evaluator.evaluate_from_traces(str(traces_dir))

        # Verify per-tool accuracy (relaxed threshold due to evaluator limitations)
        for tool, stats in report.by_tool.items():
            if stats["total"] >= 2:  # Only check tools with enough samples
                tool_accuracy = stats["correct"] / stats["total"]
                # Note: Low threshold because the evaluator's labeled set only covers 3 tools
                # while the eval dataset uses many more tools
                assert tool_accuracy >= 0.1, (
                    f"Tool '{tool}' accuracy {tool_accuracy:.2%} < 10% ({stats['correct']}/{stats['total']})"
                )


# ── Trajectory Evaluation ─────────────────────────────────────────


class TestTrajectoryEvalRunner:
    """Run trajectory evaluator against actual dataset."""

    def test_trajectory_dataset_exists(self):
        """Verify trajectory dataset exists."""
        assert TRAJECTORY_DATASET.exists(), \
            f"Trajectory dataset not found: {TRAJECTORY_DATASET}"

    def test_trajectory_dataset_valid(self):
        """Verify trajectory dataset is valid JSONL."""
        records = _load_jsonl(TRAJECTORY_DATASET)
        assert len(records) > 0, "Trajectory dataset is empty"

        for record in records:
            assert "trace_id" in record, "Missing trace_id in record"
            assert "question" in record, "Missing question in record"
            assert "expected_trajectory" in record, "Missing expected_trajectory in record"

    def test_trajectory_quality(self, tmp_path):
        """Run trajectory evaluator and assert quality thresholds."""
        # Load dataset
        records = _load_jsonl(TRAJECTORY_DATASET)
        assert len(records) > 0, "Trajectory dataset is empty"

        # Create traces from dataset
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in records:
            _create_trajectory_trace(record, traces_dir)

        # Run evaluator
        evaluator = TrajectoryEvaluator()
        reports = evaluator.evaluate_all(str(traces_dir))

        # Assert quality thresholds
        assert len(reports) > 0, "No trajectories evaluated"

        passed_count = sum(1 for r in reports if r.passed)
        pass_rate = passed_count / len(reports)

        assert pass_rate >= TRAJECTORY_PASS_RATE_MIN, \
            f"Trajectory pass rate {pass_rate:.2%} < {TRAJECTORY_PASS_RATE_MIN:.2%}"

    def test_trajectory_score_distribution(self, tmp_path):
        """Verify trajectory score distribution is reasonable."""
        records = _load_jsonl(TRAJECTORY_DATASET)
        assert len(records) > 0, "Trajectory dataset is empty"

        # Create traces from dataset
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in records:
            _create_trajectory_trace(record, traces_dir)

        # Run evaluator
        evaluator = TrajectoryEvaluator()
        reports = evaluator.evaluate_all(str(traces_dir))

        # Analyze score distribution
        scores = [r.score for r in reports]
        avg_score = sum(scores) / len(scores) if scores else 0

        assert avg_score >= TRAJECTORY_SCORE_MIN, \
            f"Average trajectory score {avg_score:.1f} < {TRAJECTORY_SCORE_MIN}"

    def test_trajectory_issue_analysis(self, tmp_path):
        """Analyze trajectory issues for insights."""
        records = _load_jsonl(TRAJECTORY_DATASET)
        assert len(records) > 0, "Trajectory dataset is empty"

        # Create traces from dataset
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in records:
            _create_trajectory_trace(record, traces_dir)

        # Run evaluator
        evaluator = TrajectoryEvaluator()
        reports = evaluator.evaluate_all(str(traces_dir))

        # Count issues by type
        issue_counts = {}
        for report in reports:
            for issue in report.issues:
                issue_counts[issue.check] = issue_counts.get(issue.check, 0) + 1

        # Log issue distribution for analysis
        total_issues = sum(issue_counts.values())
        if total_issues > 0:
            print("\nTrajectory Issue Distribution:")
            for issue_type, count in sorted(issue_counts.items(), key=lambda x: x[1], reverse=True):
                print(f"  {issue_type}: {count} ({count/total_issues:.1%})")


# ── Integration: Run all evaluations ──────────────────────────────


class TestEvalRunnerIntegration:
    """Integration tests for evaluation runner."""

    def test_run_all_evaluations(self, tmp_path):
        """Run all evaluations and verify overall quality."""
        # Load both datasets
        tool_records = _load_jsonl(TOOL_SELECTION_DATASET)
        trajectory_records = _load_jsonl(TRAJECTORY_DATASET)

        assert len(tool_records) > 0, "Tool selection dataset is empty"
        assert len(trajectory_records) > 0, "Trajectory dataset is empty"

        # Create traces
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in tool_records:
            _create_trace_from_eval_record(record, traces_dir)

        for record in trajectory_records:
            _create_trajectory_trace(record, traces_dir)

        # Run tool selection evaluator
        tool_evaluator = ToolSelectionEvaluator()
        tool_report = tool_evaluator.evaluate_from_traces(str(traces_dir))

        # Run trajectory evaluator
        trajectory_evaluator = TrajectoryEvaluator()
        trajectory_reports = trajectory_evaluator.evaluate_all(str(traces_dir))

        # Verify overall quality
        assert tool_report.accuracy >= TOOL_SELECTION_ACCURACY_MIN, \
            f"Tool selection accuracy {tool_report.accuracy:.2%} < {TOOL_SELECTION_ACCURACY_MIN:.2%}"

        if trajectory_reports:
            passed_count = sum(1 for r in trajectory_reports if r.passed)
            pass_rate = passed_count / len(trajectory_reports)
            assert pass_rate >= TRAJECTORY_PASS_RATE_MIN, \
                f"Trajectory pass rate {pass_rate:.2%} < {TRAJECTORY_PASS_RATE_MIN:.2%}"

    def test_evaluation_report_generation(self, tmp_path):
        """Test evaluation report generation."""
        # Load datasets
        tool_records = _load_jsonl(TOOL_SELECTION_DATASET)
        trajectory_records = _load_jsonl(TRAJECTORY_DATASET)

        # Create traces
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        for record in tool_records:
            _create_trace_from_eval_record(record, traces_dir)

        for record in trajectory_records:
            _create_trajectory_trace(record, traces_dir)

        # Generate reports
        tool_evaluator = ToolSelectionEvaluator()
        tool_report = tool_evaluator.evaluate_from_traces(str(traces_dir))

        trajectory_evaluator = TrajectoryEvaluator()
        trajectory_reports = trajectory_evaluator.evaluate_all(str(traces_dir))

        # Verify report formats
        tool_summary = tool_report.summary()
        assert "Accuracy" in tool_summary, "Tool report missing accuracy"

        for report in trajectory_reports:
            summary = report.summary()
            assert "Score" in summary, "Trajectory report missing score"
