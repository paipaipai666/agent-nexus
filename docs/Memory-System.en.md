> **[中文](Memory-System.md) | [English](Memory-System.en.md)**

# 🧠 Memory System

## Dual-Layer Architecture

```
User interaction → MemoryManager (throughout Agent lifecycle)
    │
    ├── init_session(question)
    │     ├── Encode → ChromaDB LTM search (top-5, min_similarity=0.5)
    │     ├── Format → inject into system prompt {memory_context}
    │     └── Snapshot write_counter
    │
    ├── append(role, content) — on each Agent message
    │     ├── Large results (>threshold) → offload.py writes to disk, returns short stub + preview
    │     ├── Append to STM
    │     └── maybe_compact() → 5-layer compression
    │
    ├── refresh_ltm_context()
    │
    ├── conclude(question, answer)
    │     ├── Two-level pre-filter
    │     │     ├── Level 1: Rule filter (0ms) — fast pass for strong signals, fast reject for useless formats
    │     │     └── Level 2: LLM gate (boundary cases only) — with circuit breaker protection
    │     ├── PII masking (input side)
    │     ├── LLM extraction → 3 categories (fact/preference/note), with PII source control
    │     ├── Five-segment fine-grained pipeline
    │     │     ├── Segment 1 (unlocked): LLM extraction
    │     │     ├── Segment 1.5 (unlocked): Embedding generation
    │     │     ├── Segment 2 (locked): Semantic dedup (cosine ≥ 0.90 → skip)
    │     │     ├── Segment 3 (unlocked): LLM conflict detection (all categories)
    │     │     └── Segment 4 (locked): Double-check + save LTM
    │     └── PII regex fallback (output side)
    │
    └── run_reflection() — periodic reflection
          ├── Fetch recent note-category memories (≥ 5 required)
          ├── LLM distills higher-level patterns → save as fact/preference
          └── Mark original notes as superseded_by (idempotent)
```

## STM Compression Pyramid

`maybe_compact()` triggers bottom-up:

| Layer | Trigger Condition | Operation |
|----|----------|------|
| 1 Circuit Breaker | 3 consecutive compression failures | Skip compression with exponential backoff (30s to 120s); half-open probe recovery |
| 2 Snip | Too many STM entries | Keep latest 10 |
| 3 Time Micro-Compact | >config interval since last API call | Clear recoverable tool results |
| 3b Message Micro-Compact | Immediately before LLM summary | Clear old recoverable tool results (keep latest 5); truncate assistant messages >2000 chars |
| 4 Read-Time Projection | Token usage >= 90% ctx | See "Read-Time Projection (projection.py)" section below |
| 5 LLM Summary | Insufficient buffer tokens | Write transcript -> drain high-importance messages to LTM -> LLM summary replacement |

**Circuit Breaker State Machine**: `closed -> open (backoff) -> half-open (probe) -> closed/open`

**LTM Drain**: Before LLM summarization, messages with importance >= 0.7 are saved to LTM to ensure critical information survives compaction.

## Read-Time Projection (projection.py)

Non-destructive read-time compression that automatically selects a strategy based on token usage before each LLM call:

| Usage | Strategy | Operation |
|------|------|------|
| < 90% | None | No processing |
| 90%-95% | `project_mild()` | Truncate assistant/tool messages >1000 chars (keep first 500 + last 500); protect latest 4 messages |
| >= 95% | `project_aggressive()` | Clear recoverable tool results, truncate assistant messages, insert projection boundary marker |

**Importance Protection**: Messages with importance >= 0.7 are exempt from truncation.

## LTM Scoring & Eviction

**Search scoring**:
```
score = cosine_similarity × 0.55 + effective_importance × 0.25 + time_decay × 0.20

effective_importance = min(1.0, base_importance + 0.1 × min(2.0, log(1 + access_count)))
time_decay = 2^(-age_hours / half_life)
  - fact/preference: half_life = None (no decay, permanent)
  - note: half_life = 48 hours
```

**Write behavior**:
- Same content+category already exists: importance boosted by 0.05 (capped at 1.0), refreshes `last_accessed_at` (does NOT modify `created_at`)
- `mark_superseded()` is idempotent: already-superseded memories are not overwritten

**Eviction strategy** (exceeds `max_memories`):
1. `_compact_low_score()` — merge low-score entries by category (importance 0.3-0.6, merge when >5 entries)
2. Delete by `(importance + access_boost) × 0.6 + decay × 0.4 ASC` (sync delete from ChromaDB)
3. Clean TTL-expired entries (note default 90 days, fact/preference never expire)

**Importance categories** (3-category system):

| Category | Default Weight | Description |
|------|------|------|
| `fact` | 0.85 | Facts: entity facts + conclusions (permanent, high importance) |
| `preference` | 0.9 | Preferences: user preferences + tool preferences (permanent, high importance) |
| `note` | 0.7 | Notes: task progress + error patterns + conversation context (temporary, medium importance) |

> **Migration mapping**: `entity_fact`/`conclusion` -> `fact`, `user_preference`/`tool_preference` -> `preference`, `task_progress`/`error_pattern`/`conversation` -> `note`

LLM extraction can return custom importance (0.0-1.0) per memory item, which takes priority over the category default.

## Conversation Version Control

`ConversationVersionManager` (`versioned.py`) implements a linear checkpoint system:

- Auto `commit()` on each user turn, recording question/answer/STM snapshot
- SQLite 3 tables: `conversation_checkpoints` (linear chain), `conversation_sessions` (workspace sessions), `conversation_messages` (message journal)
- Workspace session management: `register_session(workspace_path, profile)` associates sessions with workspaces
- Message journal: `append_message(role, content)` records full conversation history
- Supports `undo()` / `redo()` (redo stack cleared on new commit)

## Large Result Offloading (offload.py)

When a tool result exceeds the configured threshold (`large_result_threshold`), the full content is written to disk and a short stub is returned:

```text
[Tool result cached] File: /path/to/offload/{session_id}_{timestamp}.txt
Preview (first 500 chars): {preview}
```

- Auto-cleanup: stale offload files older than 24 hours are deleted on each offload
- Triggered automatically by `MemoryManager.append()` at Layer 1

## Periodic Reflection (reflection.py)

`run_reflection()` distills higher-level patterns from recent note-category memories:

1. Fetches recent note-category memories from the last N days (excluding already-reflected), minimum 5 required
2. LLM identifies recurring patterns or preferences, saved as `fact` or `preference` category
3. Original notes marked as `superseded_by -> new pattern memory`
4. Semantic dedup: skips if cosine similarity >= 0.90 with existing memory

Reflected memories are saved with a `[Reflection]` prefix, importance range 0.7-0.95.

## Structured Monitoring (metrics.py)

`MemoryMetrics` singleton provides thread-safe counters for monitoring pipeline health:

| Metric | Meaning |
|--------|---------|
| `writes_total` | Total memories successfully written to LTM |
| `writes_skipped_dedup` | Skipped by semantic dedup |
| `writes_skipped_gate` | Intercepted by LLM gate |
| `writes_skipped_gate_error` | Gate network/API exceptions |
| `writes_skipped_gate_format_error` | Gate output format anomalies (model degradation signal) |
| `pii_masked_count` | PII regex fallback catches (source control failure signal) |
| `conflicts_detected` | Conflict detection hits |
| `superseded_count` | Memories superseded |
| `deletions_expired` | TTL-expired deletions |
| `deletions_evicted` | Eviction deletions |
| `searches_total` / `searches_hit` | Total LTM searches / hits |
| `extraction_attempts` / `extraction_successes` | Extraction attempts / successes |

**Key alerts**:
- `writes_skipped_gate_format_error` growing → model degradation or prompt change causing format anomalies
- `pii_masked_count > 0` → LLM source control failing, check prompt or model version
- `conflict_rate > 0.3` → memory writes too aggressive, consider tightening gate

Access via `get_metrics().report()` for dict snapshot, compatible with Prometheus or FastAPI `/metrics` endpoint.
