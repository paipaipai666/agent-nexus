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
    │     ├── Large results (>10KB) → offload to disk
    │     ├── Append to STM
    │     └── maybe_compact() → 5-layer compression
    │
    ├── refresh_ltm_context()
    │
    └── conclude(question, answer)
          ├── PII masking
          ├── LLM extraction (memory_extract.txt)
          └── Encode + save LTM (SQLite + ChromaDB)
```

## STM Compression Pyramid

`maybe_compact()` triggers bottom-up:

| Layer | Trigger Condition | Operation |
|----|----------|------|
| 1 Circuit Breaker | 3 consecutive compression failures | Skip compression for 60s |
| 2 Snip | Too many STM entries | Keep latest 10 |
| 3 Time Micro-Compact | >300s since last call | Clear recoverable tool results |
| 4 Message Truncation | Assistant message >2000 chars | Truncate long messages |
| 5 LLM Summary | Insufficient buffer tokens | LLM summary replacement (backup snapshot first) |

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

**Importance categories**:

| Category | Weight | Description |
|------|------|------|
| `user_preference` | 0.9 | User preferences |
| `entity_fact` | 0.7 | Entity facts |
| `conclusion` | 0.8 | Conclusions |
| `conversation` | 0.5 | General conversation |
| `task_progress` | 0.7 | Task progress |
| `error_pattern` | 0.8 | Error patterns |
| `tool_preference` | 0.6 | Tool preferences |

## Conversation Version Control

`ConversationVersionManager` provides Git-like checkpoints:
- Auto `commit()` on each user turn
- SQLite 3 tables: `checkpoints` (DAG), `checkpoint_ltm_refs`, `branches`
- Supports `undo()` / `redo()` / `branch(name)`
