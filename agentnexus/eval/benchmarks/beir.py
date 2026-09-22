"""BEIR dataset loader — download, cache, and parse the original release layout.

BEIR ships each dataset as a zip of ``corpus.jsonl`` / ``queries.jsonl`` /
``qrels/{train,dev,test}.tsv``. We use the test split with relevance >= 1,
matching the BEIR leaderboard evaluation protocol.

Cache layout::

    ~/.cache/agentnexus/benchmarks/beir/<dataset>.zip
    ~/.cache/agentnexus/benchmarks/beir/<dataset>/{corpus,queries}.jsonl, qrels/test.tsv

Download failures (e.g. behind a firewall) print the exact manual fallback:
drop the zip into the cache directory and re-run with ``--offline``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from .schema import BenchmarkData, BenchmarkDoc, BenchmarkQuery

logger = logging.getLogger(__name__)

BEIR_BASE_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets"

BEIR_LICENSE_NOTE = (
    "BEIR datasets are redistributed by UKP Lab for research evaluation; "
    "individual datasets carry their own licenses (e.g. SciFact for research "
    "use only). See https://github.com/beir-cellar/beir for per-dataset terms."
)


@dataclass(frozen=True)
class BEIRDatasetSpec:
    name: str  # zip/directory name, e.g. "nfcorpus"
    display: str


def cache_root() -> Path:
    """User-level cache shared across projects, overridable for tests."""
    override = os.environ.get("AGENTNEXUS_BENCHMARK_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "agentnexus" / "benchmarks" / "beir"


def ensure_dataset(spec: BEIRDatasetSpec, offline: bool = False) -> Path:
    """Return the extracted dataset directory, downloading the zip if needed."""
    root = cache_root()
    extract_dir = root / spec.name
    marker = extract_dir / ".complete"
    if marker.exists():
        return extract_dir

    if offline:
        raise FileNotFoundError(
            f"BEIR dataset '{spec.name}' not cached at {extract_dir}. "
            f"Download manually: {BEIR_BASE_URL}/{spec.name}.zip -> {root / (spec.name + '.zip')}, "
            f"then extract into {extract_dir}."
        )

    root.mkdir(parents=True, exist_ok=True)
    zip_path = root / f"{spec.name}.zip"
    if not zip_path.exists():
        _download(spec, zip_path)
    else:
        logger.info("Using cached BEIR zip: %s", zip_path)

    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    # BEIR zips nest everything under a top-level "<name>/" directory — flatten it.
    nested = extract_dir / spec.name
    if (nested / "corpus.jsonl").exists():
        for item in nested.iterdir():
            shutil.move(str(item), extract_dir / item.name)
        nested.rmdir()
    marker.touch()
    return extract_dir


def _download(spec: BEIRDatasetSpec, zip_path: Path) -> None:
    url = f"{BEIR_BASE_URL}/{spec.name}.zip"
    logger.info("Downloading %s ...", url)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as response:
            response.raise_for_status()
            tmp_path = zip_path.with_suffix(".zip.part")
            with open(tmp_path, "wb") as fh:
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
            tmp_path.replace(zip_path)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Failed to download {url}: {exc}. "
            f"Download it manually into {cache_root()} and re-run with --offline."
        ) from exc


def _iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_beir_dataset(spec: BEIRDatasetSpec, offline: bool = False) -> BenchmarkData:
    """Parse a BEIR dataset into the standard benchmark schema."""
    data_dir = ensure_dataset(spec, offline=offline)

    corpus_path = data_dir / "corpus.jsonl"
    queries_path = data_dir / "queries.jsonl"
    qrels_path = data_dir / "qrels" / "test.tsv"
    for required in (corpus_path, queries_path, qrels_path):
        if not required.exists():
            raise FileNotFoundError(
                f"BEIR dataset '{spec.name}' is incomplete: missing {required}. "
                f"Delete {data_dir} and re-download."
            )

    docs = [
        BenchmarkDoc(doc_id=str(item["_id"]), title=str(item.get("title") or ""), text=str(item.get("text") or ""))
        for item in _iter_jsonl(corpus_path)
    ]

    queries = {
        str(item["_id"]): str(item.get("text") or "")
        for item in _iter_jsonl(queries_path)
    }

    qrels: dict[str, dict[str, int]] = {}
    with open(qrels_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.lower().startswith("query-id"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                parts = line.split()  # tolerate whitespace-separated variants
            if len(parts) < 3:
                continue
            qid, doc_id, rel = parts[0], parts[1], parts[2]
            try:
                rel_int = int(rel)
            except ValueError:
                continue
            if rel_int < 1:
                continue
            qrels.setdefault(qid, {})[doc_id] = max(qrels.setdefault(qid, {}).get(doc_id, 0), rel_int)

    # Only evaluate queries that have at least one relevant doc (BEIR protocol).
    eval_queries = [
        BenchmarkQuery(query_id=qid, text=text)
        for qid, text in queries.items()
        if qid in qrels and qrels[qid] and text.strip()
    ]

    return BenchmarkData(
        suite="beir",
        dataset=spec.display,
        docs=docs,
        queries=eval_queries,
        qrels=qrels,
        license_note=BEIR_LICENSE_NOTE,
    )
