> **[中文](Observability.md) | [English](Observability.en.md)**

# 📈 Observability

## Trace System

- **Storage**: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl` (daily rotation)
- **Retention**: `trace_retention_days` (default 30)
- **Span Structure**: `{trace_id, span_id, parent_span_id, name, latency_ms, input/output(truncated 5000), metadata{status, tokens, model}}`
- **Lifecycle**: `start_trace()` → root span → each component `span()` context manager → `end_trace()` flush once

> ⚠ Writes to disk on `end_trace()`, `atexit` registers flush on exit. Crash loses unflushed data.

**Trace Sources**:

| Span Name | Source |
|-----------|------|
| `task: <desc>` | Root span |
| `plan_node` | Each Agent step |
| `tool:<name>` | Each tool call |
| `subagent` / `subagent_attempt` | Sub-agent |

## Token Statistics

`compute_stats()` scans JSONL, groups by trace_id:

| Statistic | Source |
|--------|------|
| Task count / Input tokens / Output tokens | cumsum |
| Cost (CNY) | Built-in pricing table |
| P50/P95/P99 latency | Percentiles |
| Retry count | Trace count |

Model pricing (CNY per million tokens):

| Model | Input | Output |
|------|------|------|
| deepseek-v4-flash | ¥0.6 | ¥1.2 |
| deepseek-v4-pro | ¥1.0 | ¥4.0 |
| deepseek-r1 | ¥4.0 | ¥16.0 |
| qwen-max | ¥2.5 | ¥10.0 |
| gpt-4o | ¥17.5 | ¥70.0 |
| gpt-4o-mini | ¥1.0 | ¥4.0 |

## Audit Log

`ToolRegistry` maintains an in-memory `AuditEntry` list, viewable via `nexus audit` command.
