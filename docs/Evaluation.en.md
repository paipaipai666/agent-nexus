> **[中文](Evaluation.md) | [English](Evaluation.en.md)**

# 📊 Evaluation

All evaluators read from JSONL Traces (except Coherence, which does not depend on LLM).

## Agent Layer

| Evaluator | Method | Target | Command |
|--------|------|------|------|
| **Agent** | Trace extraction: success rate/latency/truncation rate | answer>85%, tool>80%, trunc<10% | `eval agent` |
| **Trajectory** | 5 rule checks | score≥6/10 | `eval trajectory` |
| **Component** | Check by Agent role | score≥6.0 | `eval component` |
| **Hallucination** | Sentence split + keyword overlap | rate<2% | `eval hallucination` |
| **Coherence** | Independent Judge model scoring | score≥8.5/10 | `eval coherence` |
| **Tool Selection** | Keyword match vs actual | accuracy≥92% | `eval tool-selection` |
| **HumanEval** | Isolated subprocess test run | pass@1 | `eval humaneval` |
| **SWE-bench** | Patch application test | resolve_rate | `eval swe-bench` |

## RAG Layer

`eval run` tests 12 configurations (3 chunk strategies × 2 chunk sizes × 2 retrieval modes):

| Metric | Description |
|------|------|
| Faithfulness | Whether answer is faithful to context |
| AnsRelevancy | Answer relevance to question |
| AnsCorrectness | Answer consistency with ground truth |
| Precision | Retrieval precision |
| Recall | Retrieval recall |
| ContextRelevancy | Context relevance to question |
| HitRate | Hit rate @k |
| MRR | Mean reciprocal rank |

## Public Benchmarks (Benchmark Track)

Alongside the built-in 60-question track (chunked knowledge base + Judge LLM), `eval benchmark` provides a second **standard-IR track**: public corpora with qrels and exact doc-id matching, zero LLM calls — for leaderboard comparison and retriever regression detection.

| Command | Description |
|------|------|
| `eval benchmark list` | List suites and datasets |
| `eval benchmark run -s beir-lite` | Run BEIR-lite (NFCorpus / SciFact / ArguAna) |
| `eval benchmark run --mode hybrid` | RRF(dense+BM25), this project's own stack |
| `eval benchmark run -e <model>` | Temporarily switch the embedding model |
| `eval benchmark run --ci` | Write report to `traces/evals/benchmark-*.json` |
| `eval benchmark run-rgb --lang zh --task noise` | RGB end-to-end (retrieve+generate+judge); `-t rejection` for negative-rejection |
| `eval benchmark run-rgb -m agnes/agnes-3.0-flash -n 50` | Pick generator model, 50-query smoke run |
| `eval benchmark run-rgb -j <report.json>` | Skip generation; re-judge stored answers (off-peak / cross-judge agreement) |
| `eval benchmark run-rgb -g` | Generate-only; defer judging to a later `-j` pass (e.g. deepseek off-peak window) |
| `eval benchmark run -e BAAI/bge-small-en-v1.5` | Per-task embedding switch (English tasks want an English model) |
| `eval benchmark run -s multihop` | MultiHop-RAG multi-hop retrieval (609-doc corpus / 2556 queries; retrieval genuinely matters; dense vs hybrid quantifies the production stack's lift) |
| `eval benchmark gate -s multihop --min 0.66` | CI gate: exit 1 when the latest report's metric falls below the floor (hand-captured baselines auto-excluded) |

API: `GET /api/eval/benchmark/suites` / `POST /api/eval/benchmark/run` / `GET /api/eval/benchmark/reports`.

Protocol notes:

- `dense` mode = official BEIR protocol (document-level indexing, single dense retriever, NDCG@10 primary) — comparable with leaderboard.mteb.org
- `hybrid` mode = this project's RRF stack, reported alongside, never mixed into the official number
- Default embedding is the Chinese model; use `-e BAAI/bge-small-en-v1.5` for meaningful English-benchmark scores
- Data cached under `~/.cache/agentnexus/benchmarks/`, `--offline` supported
- RGB protocol: 5 docs per query (positive+negative sampled by noise rate, official passage_num=5), official instruction template; noise tasks are LLM-judged (>=0.5 = correct), rejection tasks use the official keyword rules — no judge LLM needed
- All LLM calls serialize through one rate limiter (`--rpm`, default 18) with exponential backoff on 429; keep <=18 for free tiers (Agnes RPM 20)

## Memory Layer (Memory Track)

| Command | Description |
|------|------|
| `eval memory [--judge]` | Deterministic probes (`agentnexus/evaluation/memory_eval.py`): recall, freshness, forgetting, isolation, write integrity, etc. across 10 dimensions — offline, zero LLM; `--judge` adds LLM quality probes |
| `eval memory-bench list` | List the built-in conversational memory suite (3 conversations × 5 questions) |
| `eval memory-bench run -b nexus` | End-to-end conversational memory QA: replay dialogue → answer → char-F1 (optional `--judge`); report to `traces/evals/memory-bench-*.json` |
| `eval memory-bench run -b naive` | Full-context baseline (no memory, whole history in prompt) — the reference point for the nexus backend |
| `eval memory-bench run -d suite.jsonl` | Custom suite (LOCOMO/LongMemEval-shaped JSONL); `--min-f1` works as a CI gate |
| `eval memory-bench convert locomo -o suite.jsonl` | Download + convert the public LoCoMo10 benchmark (CC BY-NC 4.0, research/internal use only) |
| `eval memory-bench convert longmemeval-s -o suite.jsonl` | Download + convert LongMemEval-S (500 questions, ICLR 2025) |

Data format: one conversation per line — `{"id", "turns": [{"role","content"}], "questions": [{"id","question","answer","qa_type"}]}` with `qa_type ∈ single_hop / multi_session / knowledge_update / temporal / distractor_filter / abstention / open_domain`.

Protocol notes:

- The `nexus` backend drives the production write path (`append` → `conclude` admission + extraction → LTM); each conversation runs in an isolated MemorySandbox (temp AGENTNEXUS_HOME + real SQLite/Chroma)
- Primary metric: char-F1 (punctuation/case-insensitive character-multiset F1, fair for Chinese); abstention gold is "无法确定"
- **Retrieval-level metric (model-free)**: `retrieval_hit` = whether the gold appears in the memory context the backend actually injected (gold-coverage / LongMemEval answerability check). Not applicable for abstention/open_domain (recorded as None). This is the fair cross-model / cross-vendor comparison axis — QA F1 = memory × generator, retrieval rate measures the memory layer alone
- `retrieval_hit` drill-down: per-question ✓/✗ (Ctx column). Write-side failure (admission rejected the fact, LTM never stored it) and read-side failure (retrieval missed) look identical on ✗ — separating them needs provenance; LoCoMo `evidence` dia_ids already flow into the suite, and true evidence-Recall@k becomes computable once LTM writes carry provenance
- `temporal` / `knowledge_update` cover short-term state tracking and fact supersession — the WorkMemEval/StateMemBench axes our research identified as missing from the in-house suite
- Public-benchmark relation: the `convert` subcommand downloads/caches official data (via hf-mirror, under `~/.cache/agentnexus/benchmarks/memory/`) and converts it to suite JSONL; LoCoMo category mapping: single-hop→single_hop, multi-hop→multi_session, temporal→temporal, adversarial→abstention (gold canonicalized to "无法确定"), open-domain→open_domain

## Production Layer

| Command | Description |
|------|------|
| `eval ci [-d N]` | CI mode, exit(1) on failure |
| `eval calibrate` | Judge calibration, compute Spearman/Pearson agreement |

## Built-in Datasets

`tests/evals/` contains 9 JSONL datasets: `agent_eval`, `tool_selection`, `hallucination`, `coherence`, `trajectory`, `humaneval`, `swebench`, `code_generation`, `code_retrieval`.
