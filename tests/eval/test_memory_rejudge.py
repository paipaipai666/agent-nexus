"""Offline test for decoupled judging (re-judge over stored answers)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from agentnexus.cli import eval_app

runner = CliRunner()


class _FakeJudge:
    model = "fake/judge-v1"

    def __init__(self):
        self.calls = 0

    def think(self, messages, silent=True):
        self.calls += 1
        # judge "correct" unless gold says 无法确定 and the answer still claims a fact
        prompt = messages[-1]["content"]
        gold = prompt.split("正确答案应包含: ")[1].split("\n")[0]
        answer = prompt.split("模型回答: ")[1]
        if gold == "无法确定" and "无法" not in answer:
            return "错误"
        return "正确"


def _write_report(tmp_path):
    report = {
        "backend": "nexus",
        "dataset": "sample",
        "overall_f1": 0.5,
        "judge_accuracy": None,
        "results": [
            {"question_id": "q1", "question": "db?", "gold": "PostgreSQL",
             "answer": "PostgreSQL", "f1": 1.0, "judge_correct": None, "error": ""},
            {"question_id": "q2", "question": "cloud?", "gold": "无法确定",
             "answer": "部署在 AWS 上", "f1": 0.0, "judge_correct": None, "error": ""},
            {"question_id": "q3", "question": "x?", "gold": "y",
             "answer": "", "f1": 0.0, "judge_correct": None, "error": "ingest: boom"},
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def test_rejudge_updates_report(tmp_path, monkeypatch):
    judge = _FakeJudge()
    monkeypatch.setattr(
        "agentnexus.core.judge_llm.get_judge_llm", lambda: judge)

    path = _write_report(tmp_path)
    result = runner.invoke(eval_app, ["memory-bench", "re-judge", "-i", str(path)])
    assert result.exit_code == 0, result.output

    data = json.loads(path.read_text(encoding="utf-8"))
    # q1 correct, q2 wrong (fabricated on an abstention question), q3 skipped (error)
    assert data["judge_accuracy"] == 0.5
    assert judge.calls == 2
    assert data["judge_model"] == "fake/judge-v1"
    by_id = {r["question_id"]: r for r in data["results"]}
    assert by_id["q3"]["judge_correct"] is None


def test_rejudge_skip_judged_flag(tmp_path, monkeypatch):
    judge = _FakeJudge()
    monkeypatch.setattr(
        "agentnexus.core.judge_llm.get_judge_llm", lambda: judge)
    path = _write_report(tmp_path)
    # pre-judge q1
    data = json.loads(path.read_text(encoding="utf-8"))
    data["results"][0]["judge_correct"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = runner.invoke(
        eval_app, ["memory-bench", "re-judge", "-i", str(path), "--skip-judged"])
    assert result.exit_code == 0, result.output
    assert judge.calls == 1  # only q2 judged
