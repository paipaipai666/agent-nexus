"""Benchmark suite registry.

A suite is a named collection of datasets sharing one loader. Registration is
explicit: no dynamic discovery, so CI and docs can enumerate exactly what the
runner supports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .beir import BEIRDatasetSpec, load_beir_dataset
from .multihop_loader import load_multihop
from .schema import BenchmarkData

LoaderFn = Callable[..., BenchmarkData]


@dataclass(frozen=True)
class DatasetRef:
    loader: str  # key into LOADER_REGISTRY
    spec: object  # loader-specific spec (e.g. BEIRDatasetSpec)
    display: str


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    description: str
    datasets: tuple[DatasetRef, ...]
    notes: tuple[str, ...] = ()


@dataclass
class LoadedDataset:
    ref: DatasetRef
    data: BenchmarkData


LOADER_REGISTRY: dict[str, LoaderFn] = {
    "beir": load_beir_dataset,
    "multihop": load_multihop,
}


def _load(ref: DatasetRef, offline: bool = False) -> LoadedDataset:
    loader = LOADER_REGISTRY[ref.loader]
    data = loader(ref.spec, offline=offline)
    return LoadedDataset(ref=ref, data=data)


# ── Suites ────────────────────────────────────────────────────────────
# beir-lite: small corpus footprint, research-friendly licenses, and each
# stresses a different retrieval shape (domain vocabulary, claim-evidence
# ranking, argument similarity). Big corpora (DBPedia, MSMARCO, Quora,
# TREC-COVID) are deliberately excluded from the lite suite.

SUITES: dict[str, SuiteSpec] = {
    "beir-lite": SuiteSpec(
        name="beir-lite",
        description=(
            "Small BEIR retrieval benchmark (NFCorpus / SciFact / ArguAna). "
            "Official protocol: document-level indexing, dense retrieval, "
            "NDCG@10 primary. No LLM calls."
        ),
        datasets=(
            DatasetRef(loader="beir", spec=BEIRDatasetSpec(name="nfcorpus", display="NFCorpus"), display="NFCorpus"),
            DatasetRef(loader="beir", spec=BEIRDatasetSpec(name="scifact", display="SciFact"), display="SciFact"),
            DatasetRef(loader="beir", spec=BEIRDatasetSpec(name="arguana", display="ArguAna"), display="ArguAna"),
        ),
        notes=(
            "dense mode is leaderboard-comparable; hybrid mode (RRF over dense+BM25) is this project's own stack and reported separately",
            "default embedding model is zh-focused; pass --embedding-model with an English model for meaningful BEIR scores",
        ),
    ),
    "multihop": SuiteSpec(
        name="multihop",
        description=(
            "MultiHop-RAG (arXiv 2401.15391): 2556 multi-hop queries over a 609-article "
            "evidence-connected news corpus. Retrieval really matters here — each query "
            "needs 2-3 evidence articles surfaced from the full corpus. No LLM calls."
        ),
        datasets=(
            DatasetRef(loader="multihop", spec=None, display="MultiHopRAG"),
        ),
        notes=(
            "evidence qrels keyed by article url — exact doc-id matching",
            "null_query rows have empty qrels and are skipped by the metric average",
        ),
    ),
}


def list_suites() -> list[SuiteSpec]:
    return list(SUITES.values())


def get_suite(name: str) -> SuiteSpec:
    try:
        return SUITES[name]
    except KeyError:
        known = ", ".join(sorted(SUITES))
        raise KeyError(f"Unknown benchmark suite '{name}'. Known suites: {known}") from None


def load_suite_datasets(
    suite: SuiteSpec,
    only: tuple[str, ...] = (),
    offline: bool = False,
) -> list[LoadedDataset]:
    """Load every (or selected) dataset of a suite, skipping unknown names loudly."""
    loaded: list[LoadedDataset] = []
    wanted = {d.lower() for d in only}
    for ref in suite.datasets:
        if wanted and ref.display.lower() not in wanted:
            continue
        loaded.append(_load(ref, offline=offline))
    if not loaded:
        available = ", ".join(ref.display for ref in suite.datasets)
        raise ValueError(f"No datasets matched (requested: {only}). Available in '{suite.name}': {available}")
    return loaded
