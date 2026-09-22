"""BEIR loader tests against a synthetic mini zip in an isolated cache root."""

import json
import zipfile

import pytest

from agentnexus.eval.benchmarks import beir


@pytest.fixture
def fake_beir(tmp_path, monkeypatch):
    cache = tmp_path / "beir-cache"
    monkeypatch.setenv("AGENTNEXUS_BENCHMARK_CACHE", str(cache))

    corpus = [
        {"_id": "d1", "title": "Alpha", "text": "first document"},
        {"_id": "d2", "title": "Beta", "text": "second document"},
    ]
    queries = [
        {"_id": "q1", "text": "find first"},
        {"_id": "q2", "text": "find second"},
        {"_id": "q3", "text": "no relevant docs for this one"},
    ]
    qrels_tsv = "query-id\tcorpus-id\tscore\nq1\td1\t1\nq2\td2\t0\nq3\td1\t1\n"

    spec = beir.BEIRDatasetSpec(name="minicorpus", display="MiniCorpus")
    zip_path = cache / "minicorpus.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        # real BEIR zips nest everything under a top-level "<name>/" directory
        zf.writestr("minicorpus/corpus.jsonl", "\n".join(json.dumps(item) for item in corpus))
        zf.writestr("minicorpus/queries.jsonl", "\n".join(json.dumps(item) for item in queries))
        zf.writestr("minicorpus/qrels/test.tsv", qrels_tsv)
    return spec


def test_load_beir_dataset_parses_and_filters(fake_beir):
    data = beir.load_beir_dataset(fake_beir)

    assert data.suite == "beir"
    assert data.dataset == "MiniCorpus"
    assert [doc.doc_id for doc in data.docs] == ["d1", "d2"]
    # q2's only qrel is rel=0 -> filtered out -> q2 excluded from evaluation
    assert {query.query_id for query in data.queries} == {"q1", "q3"}
    assert data.qrels == {"q1": {"d1": 1}, "q3": {"d1": 1}}
    assert data.docs[0].indexed_text == "Alpha first document"


def test_ensure_dataset_uses_marker_after_first_load(fake_beir, monkeypatch):
    first = beir.ensure_dataset(fake_beir)
    assert (first / ".complete").exists()

    def _boom(*args, **kwargs):
        raise AssertionError("network must not be touched on cache hit")

    monkeypatch.setattr(beir.httpx, "stream", _boom)
    second = beir.ensure_dataset(fake_beir)
    assert second == first


def test_offline_mode_requires_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTNEXUS_BENCHMARK_CACHE", str(tmp_path / "empty"))
    spec = beir.BEIRDatasetSpec(name="never-downloaded", display="Nope")
    with pytest.raises(FileNotFoundError, match="--offline|manually"):
        beir.ensure_dataset(spec, offline=True)


def test_suite_registry_lists_and_filters():
    from agentnexus.eval.benchmarks import get_suite, list_suites, load_suite_datasets

    suites = {suite.name for suite in list_suites()}
    assert "beir-lite" in suites

    suite = get_suite("beir-lite")
    assert len(suite.datasets) == 3

    with pytest.raises(KeyError, match="Known suites"):
        get_suite("does-not-exist")

    # Filtering by display name without touching the network: unknown name -> loud error
    with pytest.raises(ValueError, match="Available"):
        load_suite_datasets(suite, only=("not-a-dataset",), offline=True)
