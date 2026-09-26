> **[中文](ReAct-Agent.md) | [English](ReAct-Agent.en.md)**

# 🤖 ReAct Agent Execution Engine

## FSM State Machine

The agent's execution loop is driven by a **6-state × 13-rule** FSM, not a simple while loop.
(Redesigned 2026-09-24; the previous form was 15 states × 33 rules, and most of those
states had a single exit — they were pipeline steps, not decision points. See
`docs/fsm-redesign-proposal.md`.)

| State | Meaning | Entry Condition |
|------|------|----------|
| `INIT` | Build context, pick protocol tier | User question received |
| `AWAIT_MODEL` | One model round-trip + interpret the output | Loop body (`event=None` auto-advance self-loop) |
| `EXECUTE_TOOL` | Run the tool batch | Model requested tools (native tool_calls or protocol JSON) |
| `RECOVER` | Retry / degrade / salvage / abort | This round's output is unusable (FAULT) |
| `ANSWER` | Deliver the final answer | Final answer ready (AGENT_STOP hook may veto it) |
| `DONE` | Done | Unconditional landing / ABORT |

The protocol tier (native tools / JSON mode / prompt JSON) is no longer a set of
states — it is an attribute of `AWAIT_MODEL`.

## Execution Flow

```
User question → INIT → AWAIT_MODEL (per round: call LLM + interpret)
    │
    ├── TOOLS_REQUESTED → EXECUTE_TOOL (whole batch, read/write partitioned)
    │     ├── TOOLS_DONE → AWAIT_MODEL (continue)
    │     └── ANSWER_READY → ANSWER (bookkeeping-only fast path)
    │
    ├── ANSWER_READY → ANSWER → (stop hook veto) ANSWER_VETOED → AWAIT_MODEL
    │
    └── FAULT → RECOVER
          ├── ROUND_READY (retry with a hint / swap protocol tier) → AWAIT_MODEL
          ├── ANSWER_READY (salvage from raw text) → ANSWER
          └── ABORT → DONE

ANSWER → Save LTM → DONE
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
- **Thinking mode**: Auto-enabled when the model supports it; depth via `model_thinking_effort` (none/low/medium/high)

> See [Tool Governance](Tool-Governance.en.md) for tool governance, [Memory System](Memory-System.en.md) for memory pipeline.
