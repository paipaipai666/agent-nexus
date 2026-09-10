"""Pytest runner for the memory evaluation suite.

Runs the deterministic probe suite in-process (synthetic vectors, no LLM),
so CI executes the whole memory eval framework as a normal test.
"""

from __future__ import annotations

import pytest

from agentnexus.evaluation.memory_eval import (
    DEFAULT_THRESHOLDS,
    DIMENSIONS,
    MemoryEvaluator,
    default_cases,
)


@pytest.fixture(scope="module")
def report():
    return MemoryEvaluator().run()


def test_suite_passes(report):
    assert report.passed, "\n" + report.summary()


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_dimension_meets_threshold(report, dimension):
    rate = report.dimension_rate(dimension)
    threshold = DEFAULT_THRESHOLDS[dimension]
    assert rate >= threshold, f"{dimension}: {rate:.2f} < {threshold}"


def test_default_cases_cover_all_dimensions():
    # Judge probes are opt-in; the full dimension set is covered with them enabled.
    covered = {c.dimension for c in default_cases(include_judge=True)}
    assert covered == set(DIMENSIONS)
    # Default (offline) suite excludes the judge dimension.
    offline = {c.dimension for c in default_cases()}
    assert "quality" not in offline


def test_every_layer_covered():
    covered = {c.layer for c in default_cases()}
    assert {"ltm", "project", "stm", "admission"} <= covered
