> **[中文](RAG-System.md) | [English](RAG-System.en.md)**

# 📚 RAG Retrieval System

## Retrieval Pipeline

```
User query
    │
    ▼
1. Query Enhancement
    ├── [Optional] Query rewrite: conversational→keyword (default on)
    ├── [Optional] Multi-query expansion: N=3 semantic variants (default on)
    └── [Optional] HyDE: hypothetical document embedding (default off)
    │
    ▼
2. Dual-path Retrieval
    ├── Dense: SentenceTransformer → ChromaDB HNSW cosine
    └── Sparse: jieba tokenization → BM25Okapi (in-memory, rebuilt per session)
    │
    ▼
3. RRF Fusion: score = Σ 1/(k + rank), k=60
    │
    ▼
4. Structure Score Boost
    ├── Code blocks (code-related keywords→+0.02)
    ├── List blocks (+0.015)
    └── Heading blocks (+0.01, depth-weighted)
    │
    ▼
5. [Optional] CrossEncoder Reranking: BGE-Reranker-v2-m3
    │
    ▼
6. Context Expansion: Include neighboring blocks of hits
```

## ChromaDB Dual Clients

> ⚠ Two independent `PersistentClient` instances pointing to the same persistence directory

| Purpose | Client Location | Collection Name | Caching |
|------|-----------|--------|------|
| RAG | `rag/chroma_client.py` | `"documents"` | Module-level singleton |
| LTM | `memory/long_term.py` | `"long_term_memories"` | Rebuilt each time |

## Document Ingestion

```
nexus kb add <path>
    │
    ▼
load_document(path) → loader selected by type
    PDF (PyMuPDF + OCR fallback)
    Markdown (by heading level)
    HTML (h1-h6)
    DOCX (XML paragraphs → Heading grouping)
    XLSX (each worksheet = section)
    JSON (recursive rendering)
    TXT (entire file)
    │
    ▼
chunk_structured_document()
    Strategy: FIXED / RECURSIVE / SEMANTIC
    Size: default 512 chars, overlap 50
    │
    ▼
[Optional] Contextual retrieval enhancement (LLM-generated context)
    │
    ▼
Persistence: SQLite catalog + ChromaDB upsert
```

Chunk metadata: `block_type`, `has_code`, `has_list`, `heading_depth`, `section_id` — used for filtering and structure boost.

## Query Enhancement

| Stage | Behavior | Default |
|------|------|------|
| Query rewrite | Conversational→keyword | On |
| Multi-query | N variants each retrieved then RRF fused | On (N=3) |
| HyDE | Hypothetical document encoding search (weight 0.8) | Off |

## RAG Evaluation System

### Running Evaluations

```bash
# Quick mode: 4 representative configs, ~3 min
nexus eval run --quick --parallel --jobs 4 --verbose

# Full mode: 12 configs (3 strategies × 2 chunk sizes × 2 retrieval modes), ~15 min
nexus eval run --parallel --jobs 4 --verbose

# CI mode: exit(1) if thresholds not met
nexus eval run --quick --ci

# Export report
nexus eval run --quick --output report.json --format json

# Compare two evaluation runs
nexus eval compare --baseline old.json --candidate new.json

# View historical reports
nexus eval history

# Use custom dataset
nexus eval run --dataset my_eval.jsonl --parallel --jobs 4
```

### Evaluation Metrics

#### Generation Quality (Judge LLM scoring)

| Metric | Meaning | balanced threshold |
|--------|---------|-------------------|
| `faithfulness` | Answer grounded in retrieved context (no fabrication) | ≥ 0.80 |
| `answer_relevancy` | Answer addresses the question (no ground_truth needed) | ≥ 0.75 |
| `answer_correctness` | Answer matches ground_truth | ≥ 0.70 |
| `citation_precision` | Facts in answer mappable to retrieved contexts | ≥ 0.60 |

#### Retrieval Quality (text matching / embedding)

| Metric | Meaning | balanced threshold |
|--------|---------|-------------------|
| `hit_rate@k` | At least one reference context in top-k | ≥ 0.85 |
| `mrr@k` | Reciprocal rank of first hit (higher = better) | ≥ 0.70 |
| `context_precision` | Proportion of relevant chunks in results | ≥ 0.70 |
| `context_recall` | Reference contexts covered by retrieval | ≥ 0.70 |
| `context_relevancy` | Embedding similarity between query and results (not keyword overlap) | ≥ 0.60 |

#### Retriever vs Reranker Separation

| Metric | Target | Description |
|--------|--------|-------------|
| `retriever_recall@50` | Retriever (coarse rank) | Reference coverage in top-50 candidates |
| `reranker_mrr@10` | Reranker (fine rank) | Reciprocal rank of first hit in top-10 |

Separation enables precise diagnosis: is the problem in the Retriever or the Reranker?

#### Refusal & Hallucination (negative samples)

| Metric | Meaning | balanced threshold |
|--------|---------|-------------------|
| `rejection_rate` | Correct refusal rate on negative samples | ≥ 0.75 |
| `hallucination_rate` | Hallucination rate on negative samples | Lower is better |

Refusal detection uses Judge LLM with 3-class classification (REJECT / ANSWER / HALLUCINATE), not keyword matching.

#### End-to-End Success Rate

| Metric | Meaning | balanced threshold |
|--------|---------|-------------------|
| `task_success_rate` | Samples where faithfulness≥0.8 AND correctness≥0.7 AND relevancy≥0.75 | ≥ 0.65 |

A single number for product owners to judge user experience.

### Threshold Profiles

Three built-in profiles for different scenarios:

| Profile | Use case | faithfulness | task_success_rate |
|---------|----------|--------------|-------------------|
| `strict` | Enterprise KB, high compliance | 0.95 | 0.80 |
| `balanced` | General products (default) | 0.80 | 0.65 |
| `relaxed` | Customer FAQ, high error tolerance | 0.70 | 0.50 |

```python
from agentnexus.rag.evaluator import THRESHOLD_PROFILES
run.check_passed(thresholds=THRESHOLD_PROFILES["strict"])
```

### Evaluation Dataset

Built-in 60-question eval set (`eval_dataset.py`):

| Type | Count | Description |
|------|-------|-------------|
| Factual extraction | 22 | Single-document extraction |
| Multi-hop reasoning | 15 | Requires 2+ documents |
| Comparative analysis | 12 | Cross-document comparison |
| Negative samples | 11 | Answer not in knowledge base |

Supports custom JSONL datasets (`--dataset` flag).

### Performance Reference

| Scenario | Workers | Time |
|----------|---------|------|
| Quick mode (4 configs) | 4 parallel | ~3 min |
| Full mode (12 configs) | 4 parallel | ~10 min |
| Single config | Serial | ~2 min |

Bottleneck: 3 LLM calls per positive sample (generate + quality + citation), 2 per negative (generate + refusal). ~169 total LLM calls per config.
