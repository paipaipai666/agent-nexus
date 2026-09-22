"""Tests for the benchmark CI gate and the benchmark API endpoints."""

import json

import pytest
from typer.testing import CliRunner

from agentnexus.cli import app


def _write_report(path, suite, metrics_by_dataset):
    payload = {
        "suite": suite,
        "kind": "retrieval-benchmark",
        "run_at": "2026-09-21T00:00:00+00:00",
        "results": [
            {"dataset": ds, "mode": "dense", "metrics": {"ndcg@10": v}}
            for ds, v in metrics_by_dataset.items()
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def gated_reports(tmp_path, monkeypatch):
    from agentnexus.core.config import get_settings

    # traces_dir is the traces root; reports live under <traces_dir>/evals
    monkeypatch.setattr(get_settings(), "traces_dir", str(tmp_path))
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    return evals_dir


def test_gate_passes_when_all_datasets_above_min(gated_reports):
    report = _write_report(gated_reports / "benchmark-multihop-1.json", "multihop", {"MultiHopRAG": 0.70})
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "benchmark", "gate", "-s", "multihop", "-m", "ndcg@10", "--min", "0.66"])
    assert result.exit_code == 0, result.output
    assert "GATE PASSED" in result.output


def test_gate_fails_when_any_dataset_below_min(gated_reports):
    _write_report(gated_reports / "benchmark-multihop-1.json", "multihop", {"MultiHopRAG": 0.61})
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "benchmark", "gate", "-s", "multihop", "-m", "ndcg@10", "--min", "0.66"])
    assert result.exit_code == 1
    assert "GATE FAILED" in result.output


def test_gate_skips_hand_captured_baseline(gated_reports):
    # the manually captured comparison baseline must never satisfy a gate
    _write_report(gated_reports / "benchmark-multihop-baseline-20260920.json", "multihop", {"MultiHopRAG": 0.99})
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "benchmark", "gate", "-s", "multihop", "--min", "0.5"])
    assert result.exit_code == 2  # no eligible report found


def test_gate_explicit_report_path(gated_reports):
    report = _write_report(gated_reports / "custom.json", "beir-lite", {"NFCorpus": 0.35})
    runner = CliRunner()
    result = runner.invoke(
        app, ["eval", "benchmark", "gate", "-s", "beir-lite", "--min", "0.30", "-r", str(report)]
    )
    assert result.exit_code == 0, result.output


def test_gate_missing_metric_reports_failure(gated_reports):
    report = gated_reports / "benchmark-multihop-1.json"
    report.write_text(json.dumps({
        "suite": "multihop", "kind": "retrieval-benchmark", "run_at": "",
        "results": [{"dataset": "MultiHopRAG", "metrics": {"map": 0.5}}],
    }), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["eval", "benchmark", "gate", "-s", "multihop", "-m", "ndcg@10", "--min", "0.5"])
    assert result.exit_code == 1
    assert "missing" in result.output


def test_benchmark_api_suites_endpoint():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentnexus.server.routes.eval_routes import router

    api = FastAPI()
    api.include_router(router, prefix="/api/eval")
    client = TestClient(api)
    resp = client.get("/api/eval/benchmark/suites")
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()["suites"]}
    assert {"beir-lite", "multihop"} <= names


def test_benchmark_api_rejects_unknown_mode():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agentnexus.server.routes.eval_routes import router

    api = FastAPI()
    api.include_router(router, prefix="/api/eval")
    client = TestClient(api)
    resp = client.post("/api/eval/benchmark/run", json={"suite": "multihop", "mode": "bogus"})
    assert resp.status_code == 400
