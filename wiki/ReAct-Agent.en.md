> **[中文](ReAct-Agent.md) | [English](ReAct-Agent.en.md)**

# 🤖 ReAct Agent Execution Engine

## FSM State Machine

The agent's execution loop is driven by a **16-state × 25-rule** FSM, not a simple while loop.

| State | Meaning | Entry Condition |
|------|------|----------|
| `INIT` | Initialization | User question received |
| `SELECT_STRATEGY` | Select LLM strategy | System prompt ready |
| `PREPARE_LLM_CALL` | Prepare LLM params | Strategy selected |
| `CALL_LLM` | Call LLM | Params prepared |
| `RECEIVE_RESPONSE` | Receive response | LLM returned |
| `CHECK_TOOL_CALLS` | Check tool calls | Native tool calling result |
| `EXECUTE_TOOL` | Execute tool | Tool call found |
| `CHECK_EMPTY` | Check empty response | JSON mode |
| `JSON_PARSE` | Parse JSON | Response non-empty |
| `CLASSIFY` | Classify result | Parse succeeded |
| `RETRY_GATE` | Retry gate | Failure |
| `DEGRADE` | Degrade strategy | Retries exhausted |
| `EMIT_ANSWER` | Output answer | Final answer received |
| `MAX_STEPS` | Steps exceeded | current_step >= max_steps |
| `ERROR_ABORT` | Unrecoverable error | LLM call completely failed |
| `DONE` | Done | Any terminal state |

## Execution Flow

```
User question → INIT → SELECT_STRATEGY → CALL_LLM
    │
    ├── Native Tool Calling:
    │   RECEIVE_RESPONSE → CHECK_TOOL_CALLS
    │     ├── Has tool_calls → EXECUTE_TOOL(one by one)
    │     │     ↕ loop → PREPARE_LLM_CALL(continue)
    │     └── None → EMIT_ANSWER
    │
    └── JSON / Prompt JSON:
        RECEIVE_RESPONSE → CHECK_EMPTY → JSON_PARSE → CLASSIFY
          ├── {"tool":...} → EXECUTE_TOOL
          ├── {"answer":...} → EMIT_ANSWER
          └── Parse failed → RETRY_GATE(retry 2x)
                ├── Has retries → PREPARE_LLM_CALL(add error hint)
                ├── Degrade → PREPARE_LLM_CALL(swap strategy)
                └── Fallback → Extract from raw text

EMIT_ANSWER → Save LTM → DONE
```

## LLM Strategy 3-Tier Degradation

Auto-detects model capabilities, degrades at runtime, persists across sessions:

| Tier | Strategy | Dependency |
|------|------|----------|
| 1 | **Native Tool Calling** | Model supports `tools` param + `tool_choice="auto"` |
| 2 | **JSON Mode** | Model supports `response_format={"type":"json_object"}` |
| 3 | **Prompt JSON** | System prompt embeds JSON format instructions |

Detection sources: Static registry (20+ models) → litellm API detection → user config override.

## JSON Parsing Fault Tolerance

`_robust_json_parse()` 4-stage pipeline:

1. **Markdown code block extraction**: regex extract ` ```json...``` `
2. **Direct json.loads**
3. **Trailing comma fix**: `re.sub(r',\s*([}\]])', ...)` + retry
4. **Chinese/English symbol normalization**: Chinese quotes/commas/colons → ASCII
5. **Bracket depth matching**: scan outermost `{...}` pair

## AgentLLM Design

- **Streaming**: Always `litellm.completion(stream=True)`
- **Retry**: Up to 3 times, exponential backoff 2^attempt × 2.0s, transient errors only
- **Truncation detection**: `finish_reason in ("length", "max_tokens")`
- **Thinking mode**: Auto-enabled when model supports `reasoning_effort`, `thinking_budget` configurable

> See [Tool Governance](Tool-Governance.en.md) for tool governance, [Memory System](Memory-System.en.md) for memory pipeline.
