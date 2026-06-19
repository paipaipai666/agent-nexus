"""Threshold calibration for the mechanical verifier.

Run once before launch (`nexus wiki calibrate`), and again when wiki规模
exceeds the retrigger threshold. This is NOT training — it's one-time
engineering calibration against a human-labeled sample.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .models import SynthesisLevel, WikiStatement
from .store import WikiStore
from .verifier import MechanicalVerifier

logger = logging.getLogger(__name__)

# Default thresholds before calibration
DEFAULT_THRESHOLDS = {
    "jaccard_direct_quote": 0.6,
    "jaccard_paraphrase": 0.4,
    "cosine_paraphrase": 0.7,
    "cosine_source": 0.35,
}


@dataclass
class CalibrationSample:
    """A single sample for calibration — statement + human label."""

    statement_id: str
    text: str
    source_chunk_ids: list[str]
    source_texts: list[str]
    human_label: str  # ground truth synthesis_level


@dataclass
class ConfusionMatrix:
    """Confusion matrix for synthesis_level classification."""

    matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    labels: list[str] = field(default_factory=lambda: [
        SynthesisLevel.DIRECT_QUOTE.value,
        SynthesisLevel.PARAPHRASE.value,
        SynthesisLevel.CROSS_REFERENCE.value,
        SynthesisLevel.SYNTHESIS.value,
    ])

    def __post_init__(self):
        if not self.matrix:
            self.matrix = {actual: {predicted: 0 for predicted in self.labels} for actual in self.labels}

    def add(self, actual: str, predicted: str):
        if actual in self.matrix and predicted in self.labels:
            self.matrix[actual][predicted] += 1

    def false_degradation_rate(self) -> float:
        """Rate of statements incorrectly downgraded (high→low)."""
        total = sum(self.matrix[a][p] for a in self.matrix for p in self.matrix[a])
        if total == 0:
            return 0.0
        # Degradation: actual is higher trust than predicted
        trust_order = {
            SynthesisLevel.DIRECT_QUOTE.value: 3,
            SynthesisLevel.PARAPHRASE.value: 2,
            SynthesisLevel.CROSS_REFERENCE.value: 1,
            SynthesisLevel.SYNTHESIS.value: 0,
        }
        degraded = 0
        for actual in self.labels:
            for predicted in self.labels:
                if trust_order.get(actual, 0) > trust_order.get(predicted, 0):
                    degraded += self.matrix[actual][predicted]
        return degraded / total

    def miss_rate(self) -> float:
        """Rate of statements that should have been downgraded but weren't."""
        total = sum(self.matrix[a][p] for a in self.matrix for p in self.matrix[a])
        if total == 0:
            return 0.0
        trust_order = {
            SynthesisLevel.DIRECT_QUOTE.value: 3,
            SynthesisLevel.PARAPHRASE.value: 2,
            SynthesisLevel.CROSS_REFERENCE.value: 1,
            SynthesisLevel.SYNTHESIS.value: 0,
        }
        missed = 0
        for actual in self.labels:
            for predicted in self.labels:
                if trust_order.get(actual, 0) < trust_order.get(predicted, 0):
                    missed += self.matrix[actual][predicted]
        return missed / total

    def to_dict(self) -> dict:
        return {
            "matrix": self.matrix,
            "false_degradation_rate": round(self.false_degradation_rate(), 4),
            "miss_rate": round(self.miss_rate(), 4),
        }


def evaluate_thresholds(
    samples: list[CalibrationSample],
    thresholds: dict[str, float],
) -> ConfusionMatrix:
    """Run the verifier at given thresholds against labeled samples."""
    verifier = MechanicalVerifier(thresholds=thresholds)
    cm = ConfusionMatrix()

    for sample in samples:
        chunk_texts = dict(zip(sample.source_chunk_ids, sample.source_texts))
        stmt = WikiStatement(
            statement_id=sample.statement_id,
            text=sample.text,
            synthesis_level=sample.human_label,  # start from human-assigned
            source_chunk_ids=sample.source_chunk_ids,
        )
        predicted = verifier.verify_statement(stmt, chunk_texts)
        cm.add(sample.human_label, predicted)

    return cm


def suggest_threshold_adjustments(cm: ConfusionMatrix, current: dict[str, float]) -> dict[str, float]:
    """Suggest threshold adjustments based on confusion matrix analysis.

    Rules:
    - If direct_quote is being missed (predicted as paraphrase): lower jaccard_direct_quote
    - If paraphrase is being degraded to cross_reference: lower cosine_paraphrase
    - If cross_reference is being degraded to synthesis: lower cosine_source
    - If false positives in direct_quote: raise jaccard_direct_quote
    """
    adjusted = dict(current)
    matrix = cm.matrix

    # Check direct_quote miss rate
    dq_actual = matrix.get(SynthesisLevel.DIRECT_QUOTE.value, {})
    dq_missed_as_para = dq_actual.get(SynthesisLevel.PARAPHRASE.value, 0)
    dq_total = sum(dq_actual.values())
    if dq_total > 0 and dq_missed_as_para / dq_total > 0.3:
        adjusted["jaccard_direct_quote"] = max(0.3, current["jaccard_direct_quote"] - 0.1)
        logger.info("Lowering jaccard_direct_quote to %s", adjusted['jaccard_direct_quote'])

    # Check paraphrase degradation rate
    para_actual = matrix.get(SynthesisLevel.PARAPHRASE.value, {})
    para_degraded = para_actual.get(SynthesisLevel.CROSS_REFERENCE.value, 0) + para_actual.get(SynthesisLevel.SYNTHESIS.value, 0)
    para_total = sum(para_actual.values())
    if para_total > 0 and para_degraded / para_total > 0.3:
        adjusted["cosine_paraphrase"] = max(0.3, current["cosine_paraphrase"] - 0.1)
        logger.info("Lowering cosine_paraphrase to %s", adjusted['cosine_paraphrase'])

    # Check cross_reference degradation
    cr_actual = matrix.get(SynthesisLevel.CROSS_REFERENCE.value, {})
    cr_degraded = cr_actual.get(SynthesisLevel.SYNTHESIS.value, 0)
    cr_total = sum(cr_actual.values())
    if cr_total > 0 and cr_degraded / cr_total > 0.3:
        adjusted["cosine_source"] = max(0.15, current["cosine_source"] - 0.05)
        logger.info("Lowering cosine_source to %s", adjusted['cosine_source'])

    # Check false positive rate in direct_quote
    false_dq = sum(
        matrix.get(lvl, {}).get(SynthesisLevel.DIRECT_QUOTE.value, 0)
        for lvl in [SynthesisLevel.PARAPHRASE.value, SynthesisLevel.CROSS_REFERENCE.value, SynthesisLevel.SYNTHESIS.value]
    )
    if dq_total > 0 and false_dq / (dq_total + false_dq) > 0.2:
        adjusted["jaccard_direct_quote"] = min(0.8, current["jaccard_direct_quote"] + 0.05)
        logger.info("Raising jaccard_direct_quote to %s", adjusted['jaccard_direct_quote'])

    return adjusted


def run_calibration(
    store: WikiStore,
    samples: list[CalibrationSample],
    max_rounds: int = 3,
) -> dict[str, Any]:
    """Run calibration: evaluate → adjust → re-evaluate.

    Args:
        store: WikiStore for persisting results.
        samples: Human-labeled calibration samples.
        max_rounds: Maximum adjustment rounds.

    Returns:
        Dict with final thresholds, confusion matrix, and sample size.
    """
    thresholds = dict(DEFAULT_THRESHOLDS)
    best_cm = None
    best_thresholds = thresholds
    best_score = float("inf")

    for round_num in range(max_rounds):
        cm = evaluate_thresholds(samples, thresholds)
        score = cm.false_degradation_rate() + cm.miss_rate()
        logger.info("Calibration round %d: score=%.4f (degradation=%.4f, miss=%.4f)",
                     round_num + 1, score, cm.false_degradation_rate(), cm.miss_rate())

        if score < best_score:
            best_score = score
            best_cm = cm
            best_thresholds = dict(thresholds)

        # If score is good enough, stop
        if score < 0.1:
            logger.info("Calibration converged — score below 0.1")
            break

        # Suggest adjustments for next round
        thresholds = suggest_threshold_adjustments(cm, thresholds)

    # Save to store
    store.save_calibration(best_thresholds, best_cm.to_dict(), len(samples))

    return {
        "thresholds": best_thresholds,
        "confusion_matrix": best_cm.to_dict(),
        "sample_size": len(samples),
        "rounds": round_num + 1,
    }
