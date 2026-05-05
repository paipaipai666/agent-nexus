# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick reference

```bash
# Install (must be in agentnexus/ dir, not repo root)
pip install -e ".[dev,eval]"

# Lint
ruff check agentnexus/ tests/

# Run all tests
python -m pytest tests/ -v

# PyInstaller build
pyinstaller agentnexus.spec --noconfirm
```

## Architecture

**LangGraph FSM** (`agents/multi_agent/orchestrator.py`) — 5 nodes, 4 conditional routers:

```
START → plan → research → code → execute(+HITL) → analyst → END
         ↑        │          │        │               │
         └─ analyst score < 7.0   └─ failure → code   │
                                    ModuleNotFoundError → research
```

HITL: first code gen asks user confirmation before execute; retries skip confirmation.

**AgentState** (`agents/multi_agent/state.py`) — TypedDict with 34 fields. `messages` uses `Annotated[list, operator.add]` (LangGraph append reducer). Fields accessed defensively via `state.get()`.

**LLM calls** (`core/llm.py`) — litellm streaming with 3x exponential backoff. Auto-detects `finish_reason=="length"` → `self.last_truncated = True`. Transient errors retried; non-transient return empty.

**Prompts** — `prompts/*.txt`, rendered with `str.format()` (NOT Jinja2). `format_prompt(name, **kwargs)` auto-injects `{date}`.

**Error handling** — 9 ErrorTypes in `agents/schema.py`: `MISSING_CODE`, `RUNTIME_ERROR`, `HALLUCINATION`, `TOOL_FAILURE`, `SCHEMA_VIOLATION`, `NO_OUTPUT`, `EMPTY_RESULT`, `LOGIC_ERROR`, `TRUNCATION`. Each maps to a retry strategy. Max 3 retries.

**`__main__` auto-append** — `_ensure_main_block()` AST-parses generated code and appends `if __name__ == '__main__':` calling top-level functions if missing.

**Config priority**: YAML (`~/.agentnexus/config.yaml`) → env (`AGENTNEXUS_*`) → Pydantic defaults. Key envs: `AGENTNEXUS_HOME`, `AGENTNEXUS_LLM_API_KEY`, `AGENTNEXUS_LLM_MODEL_ID`.

## ChromaDB warning

RAG and long-term memory each create independent `PersistentClient` instances pointing to the same `chroma_persist_dir`. RAG uses collection `"documents"` (singleton-cached client); LTM uses `"long_term_memories"` (new client per operation). BM25 index is in-memory only, rebuilt each session.

## Tests

- `tests/unit/` — class-based
- `tests/integration/` — function-based
- `tests/regression/` — full-workflow regression (former `test_all.py`)
- `conftest.py` provides `temp_agentnexus_home` (isolated data dir) and `mock_llm` fixtures
- CLI tests use `typer.testing.CliRunner` + `isolated_filesystem()`
- Tests requiring ChromaDB/SQLite isolation use `temp_agentnexus_home`

## Trace observability

Thread-safe `TraceManager` singleton (`observability/tracer.py`). Each `nexus run` creates a TraceContext. Spans flushed only at `end_trace()` — crashes lose unflushed data. Output: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`. I/O truncated at 1000 chars.

## PyInstaller

New dynamic imports must be added to `agentnexus.spec` `hiddenimports` list, or the bundled binary will miss dependencies.

## Deprecated

`agents/retry_manager.py` — truly deprecated, no runtime imports. `critic_agent.py` and `critic_rules.py` are still active.
