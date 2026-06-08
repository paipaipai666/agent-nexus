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
    │     ├── PII masking
    │     ├── Pre-filter (_should_extract): signal words + length/pattern checks
    │     ├── LLM extraction → 3 memory categories (fact/preference/note)
    │     ├── Semantic dedup (cosine similarity ≥ 0.90 → skip)
    │     ├── Conflict detection (fact/preference: LLM detects contradiction → supersede old)
    │     └── Encode + save LTM (SQLite + ChromaDB)
    │
    └── run_reflection() — periodic reflection
          ├── Fetch recent note-category memories (≥ 5 required)
          ├── LLM distills higher-level patterns → save as fact/preference
          └── Mark original notes as superseded_by
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
score = cosine_similarity × 0.6 + importance × 0.2 + decay × 0.2
decay = 1.0 / (1.0 + age_hours / 168)   // 7-day half-life
```

**Eviction strategy** (exceeds `max_memories`):
1. `_compact_low_score()` — merge low-score entries by category
2. Delete by `importance ASC, created_at ASC` (sync delete from ChromaDB)
3. Clean TTL-expired entries (default 90 days)

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
