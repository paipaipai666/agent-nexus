# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick reference

```bash
# Install from repo root (pyproject.toml lives here)
pip install -e ".[dev,eval]"

# Lint
ruff check agentnexus/ tests/

# Run all tests
python -m pytest tests/ -v

# PyInstaller build
pyinstaller agentnexus.spec --noconfirm
```

## Architecture

**ReActAgent** (`agentnexus/agents/re_act_agent.py`) — Thought→Action→Observation loop. Used by both `nexus run` and `nexus chat`. Max steps configurable via `max_agent_steps`. Three-layer XML parsing with legacy text fallback.

**LLM calls** (`agentnexus/core/llm.py`) — litellm streaming with 3x exponential backoff. Auto-detects `finish_reason=="length"` → `self.last_truncated = True`. Transient errors retried; non-transient return empty.

**Prompts** — `agentnexus/prompts/*.txt`, rendered with `str.format()` (NOT Jinja2). `format_prompt(name, **kwargs)` auto-injects `{date}`.

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

Thread-safe `TraceManager` singleton (`agentnexus/observability/tracer.py`). Each `nexus run` creates a TraceContext. Spans flushed only at `end_trace()` — crashes lose unflushed data. Output: `~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`. I/O truncated at 1000 chars.

## PyInstaller

New dynamic imports must be added to `agentnexus.spec` `hiddenimports` list, or the bundled binary will miss dependencies.

## Removed

Multi-agent LangGraph orchestrator (`agentnexus/agents/multi_agent/`) and its sub-agents (`coder_agent.py`, `research_agent.py`, `executor_agent.py`, `critic_agent.py`, `critic_rules.py`, `analyst_agent.py`, `schema.py`) were removed in a cleanup. `nexus run` now uses ReActAgent directly.
