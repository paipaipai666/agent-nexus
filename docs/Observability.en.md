> **[中文](Observability.md) | [English](Observability.en.md)**

# 📈 Observability

AgentNexus's observability system is designed following the "Agent Harness Observability" framework, covering **seven observable objects**: goals, plans, context, tools, state, cost, and evaluation.

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────┐
│                 Observability 6-Layer Architecture               │
├─────────────────────────────────────────────────────────────────┤
│  Layer 6  Improvement Loop  │ Failure attribution → Eval samples │
│  Layer 5  Replay & Eval     │ Deterministic replay + attribution │
│  Layer 4  Visualization     │ CLI/API/GUI + AlertManager         │
│  Layer 3  Metrics           │ TokenStats + task success + drift  │
│  Layer 2  Event Storage     │ JSONL persistence + audit + redact │
│  Layer 1  Trace Collection  │ TraceManager + 5 span types        │
└─────────────────────────────────────────────────────────────────┘
```

## Trace System

- **Storage**: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl` (daily rotation, 100MB auto-split)
- **Retention**: `trace_retention_days` (default 30)
- **Crash Safety**: Each span flushed to disk immediately on end (`_flush_span`), `atexit` flush on exit
- **Lifecycle**: `start_trace(task, metadata)` → root span → component `span()` context manager → `end_trace()` flush

### Span Types

| Span Name | Source | Recorded Data |
|-----------|--------|---------------|
| `task` | Root span | user_goal, model_version, agent_id, max_steps |
| `plan_node` | Each Agent step | step_index, strategy, step_type |
| `llm` | Each LLM call | model, tokens, tool_calls, context_refs |
| `tool` | Each tool call | tool_name, params, risk_level, schema_validation |
| `final_answer` | Final answer | answer, subagent info |
| `hook_fire` | Hook trigger | hook_type, elapsed_ms |

### Metadata Fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `ok` / `error` |
| `model` | string | Model name used |
| `input_tokens` / `output_tokens` | int | Token consumption |
| `cache_hit_tokens` / `cache_miss_tokens` | int | Prompt cache metrics |
| `step_type` | string | `plan` / `tool` / `observe` / `summarize` |
| `context_refs` | list | Context source references |
| `risk_level` | string | Tool risk level (low / medium / high) |
| `schema_validation` | string | Param validation result (passed / failed) |
| `tool_selection_reason` | string | Why this tool was selected |
| `retry_count` | int | Retry count |

## Drift Detection

`DriftDetector` monitors goal deviation during agent execution, checking every 3 steps automatically.

### Five Drift Signals

| Signal Type | Detection Logic | Severity |
|-------------|-----------------|----------|
| `goal_drift` | Current goal keyword overlap with original below threshold | warning / critical |
| `repeated_steps` | 3+ consecutive identical tool calls, or high single-tool ratio | warning |
| `subtask_overrun` | Steps used exceed 50% of max_steps budget | warning |
| `goal_rewrite` | Goal rewritten, similarity with original below 0.3 | warning |
| `unused_evidence` | Tool returned anomaly signals but subsequent steps ignored them | warning |

When critical drift is detected, the system auto-injects a prompt to refocus the agent on the original goal.

## Tool Fault Attribution

`classify_tool_fault()` automatically classifies fault types based on tool call context:

| Fault Type | Description | Severity |
|------------|-------------|----------|
| `tool_selection` | Model selected the wrong tool | low |
| `param_generation` | Parameter format/values incorrect | medium |
| `permission` | Called an unauthorized operation | high |
| `tool_service` | Tool timeout/500/dependency down | medium |
| `result_understanding` | Tool returned correctly but model misinterpreted | low |

### 7-Layer Failure Attribution

`FailureAttributionEvaluator` automatically attributes failed tasks from traces:

1. **Goal Understanding** — task span exists, goal not rewritten
2. **Context** — LLM span context_refs complete
3. **Tool Selection** — tool calls are appropriate
4. **Tool Params** — schema_validation passed
5. **Permission** — no permission errors
6. **Cost** — token consumption within budget
7. **Evaluation** — final answer produced

## Cost Monitoring

`compute_stats()` scans JSONL and aggregates:

### Basic Metrics

| Metric | Description |
|--------|-------------|
| Tasks / Input Tokens / Output Tokens | Cumulative |
| Cost (CNY) | Built-in pricing table (DeepSeek/Qwen/GPT-4o/Claude/GLM) |
| P95/P99/Max Latency | Percentiles |
| Retry Count | plan_node count |
| Prompt Cache Hit Rate | cache_hit / (cache_hit + cache_miss) |

### Task-Level Metrics (New)

| Metric | Description |
|--------|-------------|
| Task Success Rate | Traces with final_answer as % of total |
| Tool Failure Rate | tool spans with status=error as % of total |
| Avg Context Length | Mean input_tokens across LLM calls |
| Max Context Length | Max input_tokens in a single LLM call |

### Budget Tiers

Configure in `config.yaml`:

```yaml
budget_simple_max_tokens: 5000       # Simple task budget
budget_complex_max_tokens: 50000     # Complex task budget
budget_high_value_max_tokens: 200000 # High-value task budget
budget_exceed_strategy: compress     # Strategy: compress|downgrade|stop_subtask|degrade
```

## Alerting System

`AlertManager` provides unified alert management with rule engine and multi-channel notifications.

### Built-in Rules

| Rule | Trigger Condition | Severity |
|------|-------------------|----------|
| `CostExceedRule` | Token cost exceeds 10 CNY | warning |
| `ToolFailureSpikeRule` | Tool failure rate > 30% (≥5 calls) | warning |
| `TaskSuccessRateRule` | Task success rate < 70% (≥3 tasks) | critical |
| `DriftCriticalRule` | Critical drift signal detected | critical |

### Notification Channels

| Channel | Description |
|---------|-------------|
| `ConsoleAlertChannel` | Print to console (CLI mode) |
| `LogAlertChannel` | Write to `~/.agentnexus/alerts/alerts_{date}.jsonl` |

### API

```text
GET /api/alerts?days=7&severity=critical   # Alert history
GET /api/alerts/rules                       # Active rules list
```

## Health Checks

The `/health` endpoint returns readiness status for each subsystem:

```json
{
  "status": "ok",
  "checks": {
    "llm": { "status": "ok", "model": "deepseek-v4-flash" },
    "mcp": { "status": "ok", "total": 3, "healthy": 3 },
    "memory": { "status": "ok", "memory_count": 42 },
    "traces_dir": { "status": "ok", "path": "~/.agentnexus/traces" },
    "disk_space": { "status": "ok", "free_gb": 120.5, "used_pct": 45.2 }
  },
  "uptime_seconds": 3600
}
```

When any subsystem is unhealthy, overall status degrades to `degraded` with HTTP 503.

## Audit Log

`ToolRegistry` generates an `AuditEntry` for every tool call:

| Field | Description |
|-------|-------------|
| `tool_name` / `caller` | Tool name and caller |
| `params` | Parameters (redacted) |
| `result_summary` | Result summary (truncated) |
| `duration_ms` | Execution time |
| `hitl_triggered` | Whether HITL confirmation was triggered |
| `error` | Error message |
| `risk_level` | Risk level |
| `schema_validation` | Parameter validation result |
| `retry_count` | Retry count |
| `tool_selection_reason` | Why this tool was selected |

Audit logs auto-persist to `~/.agentnexus/audit/audit_{date}.jsonl`.

## Observation Entry Points

### CLI Commands

```bash
nexus stats --days 7        # Token cost stats + task-level metrics
nexus audit --limit 20      # Audit log
nexus logs list --days 7    # Trace list
nexus logs view --trace-id X  # Trace detail (span tree)
nexus health                # Health checks
nexus alerts --days 7       # Alert history
```

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check (readiness probe) |
| `GET /api/stats?days=7` | Token statistics |
| `GET /api/logs?days=7` | Trace list |
| `GET /api/logs/{trace_id}` | Trace detail |
| `GET /api/audit?limit=50` | Audit log |
| `GET /api/alerts?days=7` | Alert history |
| `GET /api/alerts/rules` | Alert rules |
| `GET /api/runtime/status` | Runtime status |

### Desktop GUI

| Page | Route | Function |
|------|-------|----------|
| Stats | `/stats` | Stat cards + token chart + trace detail expand |
| Health | `/health` | Health check dashboard (5 subsystems) |
| Alerts | `/alerts` | Alert history + rule list |
| Audit | `/audit` | Audit log viewer (search/filter) |
| StatusBar | Bottom bar | Connection, context window, token I/O |
| MCP Page | `/mcp` | MCP server health dashboard |

### Typical Troubleshooting Flow

```text
1. nexus stats → spot declining task success rate
2. nexus logs list → find failed trace_id
3. nexus logs view --trace-id X → inspect span tree, locate error step
4. Check tool span metadata.status == "error"
5. Check metadata.schema_validation and metadata.risk_level
6. nexus audit → review parameter redaction and HITL records
7. nexus alerts → check for related alerts
8. nexus health → confirm all subsystems are healthy
```

## Model Pricing (CNY / Million Tokens)

| Model | Input | Output |
|-------|-------|--------|
| deepseek-v4-flash | ¥0.6 | ¥1.2 |
| deepseek-v4-pro | ¥1.0 | ¥4.0 |
| deepseek-r1 | ¥4.0 | ¥16.0 |
| qwen-max | ¥2.5 | ¥10.0 |
| gpt-4o | ¥17.5 | ¥70.0 |
| gpt-4o-mini | ¥1.0 | ¥4.0 |
