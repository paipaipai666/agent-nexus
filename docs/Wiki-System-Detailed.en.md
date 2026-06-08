> **[中文](Wiki-System-Detailed.md) | [English](Wiki-System-Detailed.en.md)**

# 📚 Wiki Knowledge System (Detailed)

## Overview

The Wiki system implements **Karpathy's LLM Wiki pattern** — a hybrid knowledge management architecture that "compiles" raw RAG-retrieved documents into structured Wiki pages, verifies each statement's trustworthiness through **mechanical verification** (no LLM), and automatically routes queries based on confidence: high-confidence uses Wiki directly, low-confidence falls back to raw RAG retrieval.

**Core Innovations**:
- **Mechanical Verifier**: Uses Jaccard similarity + cosine similarity to verify LLM-assigned synthesis levels, fully deterministic and reproducible
- **Graph Propagation Engine**: Page dependencies form a directed graph, confidence changes propagate along the graph (degradation cascade + recovery re-verification)
- **Calibration System**: Human-labeled samples → automatic threshold adjustment → confusion matrix evaluation

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        WikiService                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Ingestion│→│ Verifier │→│ Router   │→│ Store    │        │
│  │ (LLM)   │  │ (Mech.)  │  │ (Rules)  │  │ (SQLite) │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
│       │              │             │             │                │
│       ▼              ▼             ▼             ▼                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Propagation│ │ Linter   │  │Calibration│ │ ChromaDB │        │
│  │ Engine    │  │ (3 checks)│ │ (Tuning)  │ │ (Vector) │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
         │                                              │
         ▼                                              ▼
┌──────────────────┐                    ┌──────────────────┐
│   RAG System     │◄──── fallback ────│  Query Router    │
│  (Raw Doc Search)│                    │  (Confidence)    │
└──────────────────┘                    └──────────────────┘
```

## Data Models

**File**: `agentnexus/wiki/models.py`

### Synthesis Level

| Level | Meaning | Verification Method |
|-------|---------|---------------------|
| `direct_quote` | Direct quote, high overlap with single source | Jaccard ≥ 0.6 |
| `paraphrase` | Rewritten, semantically similar to single source | Jaccard ≥ 0.4 and Cosine ≥ 0.7 |
| `cross_reference` | Multi-source cross-reference, each source verified | Each source Cosine ≥ 0.35 |
| `synthesis` | No single source, cross-document conclusion | No verification (keep level) |

### Confidence Level

| Level | Meaning | Query Routing |
|-------|---------|---------------|
| `high` | 80%+ statements are direct_quote/paraphrase | Use Wiki directly |
| `medium` | 50%+ are high-trust | Wiki + source chunks |
| `low` | Contains synthesis statements | Wiki + disclaimer |
| `untrusted` | Contains untrusted statements | Fall back to RAG |

## Mechanical Verifier

**File**: `agentnexus/wiki/verifier.py`

**Zero LLM calls** — all verification is fully deterministic and reproducible. Uses two similarity metrics:

1. **Jaccard Similarity** (string overlap): token set intersection/union ratio
2. **Cosine Similarity** (vector distance): semantic similarity via embedding model

### Verification Flow

```
Input: statement + chunk_texts
         │
         ▼
┌─────────────────────────────┐
│ 1. Check source_chunk_ids   │
│    No source → SYNTHESIS    │
└─────────────┬───────────────┘
              ▼
┌─────────────────────────────┐
│ 2. Branch by assigned level │
└──────┬──────┬──────┬────────┘
       ▼      ▼      ▼
   SINGLE  MULTI  SYNTHESIS
       │      │      │
       ▼      ▼      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Jaccard  │ │ Per-chunk│ │ Return   │
│ → Cosine │ │ Cosine   │ │ SYNTHESIS│
│ Verify   │ │ Verify   │ │          │
└──────────┘ └──────────┘ └──────────┘
```

### Chinese Tokenization

The verifier has a built-in mixed Chinese/English tokenizer:
- Chinese characters split into individual tokens
- English words kept together
- Punctuation stripped

## Confidence Router

**File**: `agentnexus/wiki/confidence.py`

**Rule tree, not formula** — each rule is individually auditable and modifiable.

### Rule Tree (Priority Order, First Match Wins)

```
Input: WikiPage
         │
         ▼
┌─────────────────────────────────────┐
│ Rule 1: Has UNTRUSTED statement?    │
│   Yes → UNTRUSTED → FALLBACK_TO_RAG│
└─────────────┬───────────────────────┘ No
              ▼
┌─────────────────────────────────────┐
│ Rule 2: 80%+ HIGH_TRUST?           │
│   Yes → HIGH → USE_WIKI            │
└─────────────┬───────────────────────┘ No
              ▼
┌─────────────────────────────────────┐
│ Rule 3: 50%+ HIGH_TRUST?           │
│   Yes → MEDIUM → USE_WIKI_WITH_SRC │
└─────────────┬───────────────────────┘ No
              ▼
┌─────────────────────────────────────┐
│ Rule 4: Has SYNTHESIS statement?    │
│   Yes → LOW → USE_WIKI_WITH_DISCLAIM│
└─────────────┬───────────────────────┘ No
              ▼
        MEDIUM (default)
```

## Graph Propagation Engine

**File**: `agentnexus/wiki/propagation.py`

- **Degradation cascade**: When page confidence drops, propagate along dependency graph (min inheritance)
- **Recovery re-verification**: When page confidence rises, don't auto-recover dependents, re-verify instead
- **Depth limit**: Propagation depth limited to 3 by default to prevent chain reactions

## Lint System

**File**: `agentnexus/wiki/lint.py`

| Checker | Check | Priority | SLA |
|---------|-------|----------|-----|
| `ConsistencyChecker` | Contradictions between pages' canonical definitions | P1 | Configurable |
| `DriftDetector` | Statements drifting from canonical definitions | P2 | Configurable |
| `CoverageChecker` | RAG chunks not referenced by any Wiki statement | P3 | Configurable |

## Calibration System

**File**: `agentnexus/wiki/calibration.py`

**One-time engineering calibration**, not training. Uses human-labeled samples to adjust verifier thresholds.

```
Human-labeled samples → Evaluate current thresholds → Analyze confusion matrix → Suggest adjustments → Re-evaluate
     │                                                                                                        │
     └──────────────────── Max 3 rounds, stop early if score < 0.1 ──────────────────────────────────────────┘
```

### Default Thresholds

```python
DEFAULT_THRESHOLDS = {
    "jaccard_direct_quote": 0.6,
    "jaccard_paraphrase": 0.4,
    "cosine_paraphrase": 0.7,
    "cosine_source": 0.35,
}
```

## Configuration

| Config | Default | Description |
|--------|---------|-------------|
| `wiki_enabled` | `False` | Enable Wiki system |
| `wiki_namespace` | `"wiki"` | ChromaDB namespace for Wiki |
| `wiki_jaccard_direct_quote` | `0.6` | Jaccard threshold for direct_quote |
| `wiki_jaccard_paraphrase` | `0.4` | Jaccard threshold for paraphrase |
| `wiki_cosine_paraphrase` | `0.7` | Cosine threshold for paraphrase |
| `wiki_cosine_source` | `0.35` | Cosine threshold for cross_reference sources |
| `wiki_drift_threshold` | `0.5` | Semantic drift detection threshold |
| `wiki_propagation_max_depth` | `3` | Max propagation depth |
| `wiki_calibration_retrigger_pct` | `0.5` | Wiki growth % to trigger recalibration |
