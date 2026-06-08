> **[中文](Wiki-System.md) | [English](Wiki-System.en.md)**

# Wiki System

Hybrid Wiki + RAG knowledge management system implementing Karpathy's LLM Wiki pattern. Uses mechanical verification to ensure wiki content consistency with source documents, and confidence-based routing to determine query response strategy.

## System Architecture

```
User queries / Document ingestion
    │
    ├── WikiService (orchestration layer)
    │     ├── ingest_source() ──── Ingest source document → WikiPage
    │     │     ├── LLM generates page (statements + canonical definitions)
    │     │     ├── MechanicalVerifier mechanically verifies all statements
    │     │     ├── ConfidenceRouter computes page confidence
    │     │     ├── Stores to SQLite (wiki_pages, wiki_statements, ...)
    │     │     └── Indexes to ChromaDB (semantic search)
    │     │
    │     ├── query() ──── Confidence-routed query
    │     │     ├── ChromaDB searches for matching wiki pages
    │     │     ├── ConfidenceRouter.route() decides response strategy
    │     │     └── Four decisions: use_wiki / with_sources / with_disclaimer / RAG fallback
    │     │
    │     ├── run_lint() ──── Health checks
    │     │     ├── ConsistencyChecker (definition conflict detection)
    │     │     ├── DriftDetector (semantic drift detection)
    │     │     └── CoverageChecker (coverage gap detection)
    │     │
    │     └── calibrate() ──── Threshold calibration
    │
    ├── MechanicalVerifier (verification layer)
    │     ├── jaccard_similarity() ──── String overlap (fast filter)
    │     ├── cosine_similarity() ──── Semantic similarity (embedding vectors)
    │     └── verify_statement() ──── Verify and correct LLM-assigned synthesis_level
    │
    ├── ConfidenceRouter (routing layer)
    │     ├── compute_page_confidence() ──── Rule-tree page confidence computation
    │     └── route() ──── Decide query routing based on confidence
    │
    ├── PropagationEngine (propagation layer)
    │     ├── propagate_degradation() ──── Degradation cascading (min inheritance)
    │     ├── propagate_recovery() ──── Recovery triggering (re-verify, no auto-recover)
    │     └── on_chunk_update() ──── RAG → Wiki reverse trigger
    │
    ├── WikiLinter (lint layer)
    │     ├── ConsistencyChecker ──── Definition conflict detection
    │     ├── DriftDetector ──── Semantic drift detection
    │     └── CoverageChecker ──── Coverage gap detection
    │
    └── WikiStore (storage layer) ─── SQLite (shared database with RAG catalog)
```

## Core Concepts

### WikiPage

A wiki page containing statements and canonical definitions.

| Field | Type | Description |
|-------|------|-------------|
| `page_id` | str | Unique identifier (`page_{uuid}`) |
| `title` | str | Page title |
| `page_type` | str | Type: `entity` / `concept` / `overview` / `source_summary` |
| `content` | str | Page body content |
| `statements` | list[WikiStatement] | List of statements in the page |
| `canonical_definitions` | dict[str, CanonicalDefinition] | Canonical definitions of terms |
| `confidence` | str | Page-level confidence: `high` / `medium` / `low` / `untrusted` |
| `flags` | list[str] | Propagation markers, e.g. `depends_on_degraded_page:xxx` |
| `source_namespace` | str | Bound RAG namespace |

### WikiStatement

A single claim/assertion within a wiki page.

| Field | Type | Description |
|-------|------|-------------|
| `statement_id` | str | Unique identifier (`stmt_{uuid}`) |
| `page_id` | str | Parent page ID |
| `text` | str | Statement text |
| `synthesis_level` | str | LLM-assigned synthesis level (pre-verification) |
| `source_chunk_ids` | list[str] | Associated RAG chunk IDs |
| `canonical_term` | str \| None | Associated canonical term |
| `verified_synthesis_level` | str \| None | Mechanically verified synthesis level (None = not yet verified) |

### CanonicalDefinition

Multi-source canonical definition of a term.

| Field | Type | Description |
|-------|------|-------------|
| `definitions` | list[DefinitionEntry] | List of definitions from different sources |
| `consensus` | str \| None | Consensus definition (None when divergence >= 0.2) |
| `divergence` | float | Divergence between definitions |
| `last_recalculated` | str | Last recalculation timestamp |

### SynthesisLevel

Describes the relationship between a wiki statement and its source chunks.

| Level | Trust Rank | Description |
|-------|-----------|-------------|
| `direct_quote` | 3 | High Jaccard overlap with a single source chunk (direct quote) |
| `paraphrase` | 2 | High cosine similarity with a single source chunk (paraphrase) |
| `cross_reference` | 1 | Multiple source chunks, each verified relevant |
| `synthesis` | 0 | No single source; cross-document conclusion |

### ConfidenceLevel

Page-level and statement-level confidence等级.

| Level | Routing Decision | Description |
|-------|-----------------|-------------|
| `high` | `use_wiki` | 80%+ statements are direct_quote or paraphrase |
| `medium` | `use_wiki_with_sources` | 50%+ statements are high-trust |
| `low` | `use_wiki_with_disclaimer` | Contains synthesis statements |
| `untrusted` | `fallback_to_rag` | Any statement is untrusted |

### QueryDecision

The confidence router's decision for a query.

| Decision | Behavior |
|----------|----------|
| `use_wiki` | Use wiki answer directly |
| `use_wiki_with_sources` | Use wiki answer + attach source chunk references |
| `use_wiki_with_disclaimer` | Use wiki answer + attach disclaimer |
| `fallback_to_rag` | Fall back to pure RAG search |

## Mechanical Verification Process

`MechanicalVerifier` uses deterministic checks to verify LLM-assigned synthesis_level. No LLM calls are involved.

```
WikiStatement (LLM-assigned)
    │
    ├── No source_chunk_ids → return synthesis
    │
    ├── direct_quote / paraphrase (single-source verification)
    │     ├── Jaccard >= jaccard_direct_quote (0.6) → direct_quote
    │     ├── Jaccard >= jaccard_paraphrase (0.4)
    │     │     └── cosine >= cosine_paraphrase (0.7) → paraphrase
    │     │     └── cosine < 0.7 → cross_reference (shared vocabulary, different meaning)
    │     ├── Jaccard < 0.4, cosine >= cosine_paraphrase → paraphrase
    │     └── Both low → synthesis
    │
    ├── cross_reference (multi-source verification)
    │     ├── Compute cosine similarity for each source chunk
    │     ├── cosine >= cosine_source (0.35) → keep chunk
    │     ├── 0 valid chunks → synthesis
    │     ├── 1 valid chunk → re-run single-source verification
    │     └── 2+ valid chunks → cross_reference
    │
    └── synthesis → return synthesis directly (no verification needed)
```

**Important**: Cosine similarity thresholds are calibrated against a specific embedding model (default: BAAI/bge-small-zh-v1.5). Changing the embedding model requires re-running calibration.

## Confidence Routing

`ConfidenceRouter` uses a rule tree (not a formula). Each rule is individually auditable.

### Page Confidence Computation Rules

Evaluated in order, first match wins:

1. Any statement is untrusted → page is untrusted
2. 80%+ statements are direct_quote or paraphrase → high
3. 50%+ statements are high-trust → medium
4. Has synthesis statements → low
5. Default → medium

### Query Routing Rules

| Page Confidence | Routing Decision |
|----------------|------------------|
| `untrusted` | `fallback_to_rag` |
| `high` | `use_wiki` |
| `medium` | `use_wiki_with_sources` |
| `low` | `use_wiki_with_disclaimer` |

### Disclaimer Generation

When routing decision is `use_wiki_with_disclaimer`, a disclaimer is auto-generated:

> "This answer is based on synthesized wiki content. X/Y statements are cross-document syntheses without direct source verification. Use 'nexus wiki query --rag-fallback' for source-grounded answers."

## Trust Propagation

`PropagationEngine` manages trust propagation through the wiki dependency graph. Propagation depth is limited to 3 levels.

### Degradation Propagation

When a page's confidence drops, cascades to dependent pages using min inheritance: `dependent_confidence = min(own, source)`.

```
Source page (degraded)
    ├── Dependent page A → min(conf_A, conf_source) → update confidence + add flag
    │     ├── A's dependents → recurse (depth < max_depth)
    │     └── ...
    └── Dependent page B → ...
```

### Recovery Propagation

When a page's confidence rises, triggers re-verification (no auto-recovery). Dependent pages may have accumulated their own issues while degraded.

### RAG → Wiki Reverse Trigger

When RAG chunks are updated, automatically re-verifies all wiki statements referencing those chunks.

```
RAG chunk update
    ├── find_statements_by_chunks() → find affected statements
    ├── Re-verify each statement
    ├── Detect degradation/recovery direction
    │     ├── Degradation → propagate_degradation()
    │     └── Recovery → propagate_recovery()
    └── Recompute confidence for affected pages
```

## Lint and Review Queue

WikiLinter runs three health checks and generates ReviewItems for the review queue.

### Check Types

| Check | Priority | SLA | Description |
|-------|----------|-----|-------------|
| Consistency | P1 | Configurable days | Detects definition conflicts for the same term across pages (cosine < 0.4) |
| Drift | P2 | Configurable days | Detects statements deviating from their canonical definition (cosine < threshold) |
| Coverage | P3 | Configurable days | Detects RAG chunks not referenced by any wiki statement |

### ReviewItem Lifecycle

```
Created (pending)
    ├── Manually resolved → resolved
    └── Overdue (past deadline) → auto_degraded
         ├── P1 (definition conflict) → page marked untrusted
         ├── P2 (semantic drift) → revert to canonical definition
         └── P3 (coverage gap) → archived
```

### Review Queue Queries

Review queue is sorted by priority (P1 > P2 > P3), with same priority sorted by creation time.

## Calibration Workflow

`calibration.py` implements threshold calibration for adjusting MechanicalVerifier threshold parameters.

### Calibration Process

1. Collect human-labeled samples (`CalibrationSample`)
2. Run evaluation (`evaluate_thresholds`) to generate confusion matrix
3. Analyze confusion matrix, suggest threshold adjustments
4. Repeat for up to 3 rounds until score < 0.1 or max rounds reached
5. Save best thresholds and confusion matrix to database

### Calibration Metrics

| Metric | Description |
|--------|-------------|
| `false_degradation_rate` | Rate of statements incorrectly downgraded (high trust downgraded) |
| `miss_rate` | Rate of statements that should have been downgraded but were not |

### Re-calibration Trigger

Recommended when wiki page count exceeds the configured percentage growth since last calibration sample count.

## RAG Integration

The Wiki system integrates deeply with the RAG system:

| Integration Point | Direction | Description |
|-------------------|-----------|-------------|
| Source docs → Wiki | RAG → Wiki | RAG document ingestion triggers wiki page generation |
| Wiki query → RAG | Wiki → RAG | Insufficient confidence falls back to pure RAG search |
| RAG chunk update → Wiki | RAG → Wiki | Chunk updates trigger related wiki statement re-verification |
| ChromaDB shared | Bidirectional | Wiki pages indexed to ChromaDB `wiki` namespace |

## Database Tables

Wiki system uses the same SQLite database as `KnowledgeBaseCatalog` (`rag_catalog.db`), adding tables via schema migration v2:

| Table | Purpose |
|-------|---------|
| `wiki_pages` | Wiki pages |
| `wiki_statements` | Wiki statements (FK to wiki_pages) |
| `wiki_canonical_definitions` | Canonical definitions (composite PK: page_id + term) |
| `wiki_dependency_graph` | Page dependency graph |
| `wiki_review_queue` | Review queue |
| `wiki_calibration` | Calibration history |

## CLI Commands Reference

### wiki init

Initialize wiki and bind to a RAG namespace.

```bash
nexus wiki init <namespace>
```

### wiki ingest

Ingest a source document into the wiki.

```bash
nexus wiki ingest <source_path> --namespace <ns> --type <page_type>
```

- `--namespace` / `-n`: RAG namespace (default: `default`)
- `--type` / `-t`: Page type (`entity` / `concept` / `overview` / `source_summary`)

### wiki query

Query the wiki with confidence-based routing.

```bash
nexus wiki query "<question>" --namespace <ns> --rag-fallback
```

- `--namespace` / `-n`: RAG namespace
- `--rag-fallback` / `-r`: Force RAG fallback
- `--top-k` / `-k`: Number of results (default: 5)

### wiki lint

Run wiki health checks.

```bash
nexus wiki lint --namespace <ns> --enqueue/--no-enqueue
```

- `--enqueue`: Add issues to review queue (default: on)

### wiki review list

List review queue items.

```bash
nexus wiki review list --status pending --limit 20
```

### wiki review resolve

Resolve a review item.

```bash
nexus wiki review resolve <item_id>
```

### wiki review process

Process overdue review items (auto-degradation).

```bash
nexus wiki review process
```

### wiki stats

Show wiki health statistics.

```bash
nexus wiki stats --namespace <ns>
```

### wiki calibrate

Run threshold calibration with human-labeled samples.

```bash
nexus wiki calibrate <sample_file.json>
```

Sample file format:
```json
[
  {
    "statement_id": "stmt_001",
    "text": "Statement text...",
    "source_chunk_ids": ["chunk_001"],
    "source_texts": ["Source chunk text..."],
    "human_label": "direct_quote"
  }
]
```

### wiki full-check

Run full health check (stats + lint).

```bash
nexus wiki full-check --namespace <ns>
```

## API Endpoints Reference

Base path: `/api/wiki`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats?namespace=<ns>` | Get wiki health statistics |
| `GET` | `/pages?namespace=<ns>&limit=100` | List all wiki pages |
| `GET` | `/pages/{page_id}` | Get single page detail (with statements) |
| `DELETE` | `/pages/{page_id}` | Delete a wiki page |
| `POST` | `/query` | Query wiki (body: `{question, namespace, force_rag}`) |
| `POST` | `/ingest` | Ingest text into wiki (body: `{source_text, source_uri, namespace, page_type}`) |
| `POST` | `/ingest/file` | Upload file for wiki ingestion (multipart form) |
| `POST` | `/lint?namespace=<ns>` | Run health checks |
| `GET` | `/review?status=<status>&limit=50` | List review queue |
| `POST` | `/review/resolve` | Resolve a review item (body: `{item_id}`) |
| `POST` | `/review/process` | Process overdue review items |
| `GET` | `/calibration` | Get latest calibration status |

## Related Documentation

- [RAG System](RAG-System.md) - Underlying RAG infrastructure for the Wiki system
- [Memory System](Memory-System.md) - Conversation memory management
- [Configuration](Configuration.md) - Wiki-related configuration options
