# AGENTS.md

## Project Overview

AgentNexus is a Python 3.11+ ReAct single-agent CLI tool with FSM-driven safety loop, 12 built-in tools, and three-layer storage (ChromaDB/SQLite/JSONL). Entry point: `nexus` CLI via `agentnexus.cli:main`.

## Essential Commands

```bash
# Install (editable with dev+eval deps)
pip install -e ".[dev,eval]"

# Lint (must be zero warnings before PR)
ruff check agentnexus/ tests/

# Run all tests
python -m pytest tests/ -v

# Run specific test categories
python -m pytest tests/unit/ -v                    # Unit tests
python -m pytest tests/integration/ -v             # Integration tests
python -m pytest tests/security/ -v                # Security tests
python -m pytest tests/perf/ -v                    # Performance tests (benchmark)
python -m pytest tests/regression/ -v              # Regression/E2E tests

# Run tests with specific markers
python -m pytest -m perf -v                        # Only perf tests
python -m pytest -m e2e --run-e2e -v               # E2E tests (requires real LLM API key)

# Run single test file
python -m pytest tests/unit/test_config.py -v

# Run single test function
python -m pytest tests/unit/test_config.py::test_function_name -v

# Coverage
python -m pytest tests/ --cov=agentnexus --cov-report=term-missing

# Build binary (requires pyinstaller)
pyinstaller agentnexus.spec --noconfirm

# CLI commands
nexus init                    # Interactive config
nexus tui                     # TUI chat
nexus kb add ./docs           # Add to knowledge base
nexus stats --days 7          # Token cost stats
nexus eval agent --days 1     # Agent quality eval
nexus eval ci -d 7            # CI evaluation
```

## Architecture

```
agentnexus/
├── cli/           # Typer CLI commands (audit, config, eval, kb, logs, memory, serve, skill, stats, tui)
├── agents/        # ReAct agent, FSM, LLM strategy, prompt builder, tool runner
├── core/          # Config, LLM client, capabilities, hooks, PII masking
├── tools/         # 12 built-in tools + MCP adapter + tool registry/executor
├── memory/        # STM/LTM management, compaction, versioned conversations
├── rag/           # RAG pipeline, ChromaDB clients, embeddings, ranking
├── services/      # Business logic layer (chat, config, eval, knowledge, skill)
├── evaluation/    # 8 evaluators (agent, trajectory, hallucination, RAG, code, etc.)
├── skills/        # Skill workflow engine, router, runtime
├── storage/       # Storage abstractions
├── observability/ # Tracing, audit logs, stats
├── server/        # FastAPI server for API access
├── tui/           # Textual TUI screens and widgets
└── prompts/       # Prompt templates (use str.format(), NOT Jinja2)
```

## Testing Conventions

- **Fixtures**: Use `temp_agentnexus_home` for isolated `.agentnexus` directory (auto-cleanup)
- **Mock LLM**: Use `mock_llm` fixture (mocks `AgentLLM.think()`)
- **CLI tests**: Use `typer.testing.CliRunner` + `isolated_filesystem()`
- **External services**: Always mock Tavily, E2B, third-party APIs
- **ChromaDB/SQLite tests**: Use `temp_agentnexus_home` fixture
- **E2E tests**: Mark with `@pytest.mark.e2e`, require `--run-e2e` flag and `AGENTNEXUS_LLM_API_KEY`
- **Perf tests**: Mark with `@pytest.mark.perf`, use `pytest-benchmark`
- **Security tests**: Required for sandbox/code execution changes

## Configuration

- Config via `~/.agentnexus/config.yaml` or `AGENTNEXUS_*` env vars
- Settings class: `agentnexus.core.config.Settings` (pydantic-settings)
- API keys: `SecretStr` type, auto-masked in logs
- Key env vars: `AGENTNEXUS_HOME`, `AGENTNEXUS_LLM_API_KEY`, `AGENTNEXUS_TAVILY_API_KEY`, `AGENTNEXUS_E2B_API_KEY`

## Code Style

- Line length: 120 chars
- Linter: ruff (select: E, F, I, W)
- Type hints: Required on all new function parameters
- Strings: f-strings preferred, avoid `%` and `.format()` (except prompts/)
- Imports: stdlib → third-party → project internal (separated by blank lines)
- Prompt templates: Use `str.format()` in `agentnexus/prompts/`, NOT Jinja2

## PR Checklist

```bash
ruff check agentnexus/ tests/    # Must pass with zero warnings
python -m pytest tests/ -v       # Must pass
```

- PR title format: `<type>: <description>` (feat, fix, docs, refactor, perf, test, security)
- Coverage should not significantly decrease
- Sync checklist: New CLI commands → docs/commands.md, new config → docs/configuration.md, new tools → docs/architecture.md

## Important Constraints

- Never add methods to escape code sandbox
- Never access LLM inside `ToolExecutor` (tools must be stateless)
- Never write PII directly to `MemoryManager` (use `conclude()` which masks PII)
- Don't import `Settings` outside service layer (use env vars or pass params)
- Secrets in config: `SecretStr` type, never log plaintext

## Desktop App (Electron/React/TypeScript)

Located in `desktop/` directory:
```bash
cd desktop
npm install
npm run dev          # Vite dev server
npm run test         # Vitest
npm run build        # TypeScript + Vite + Electron builder
```

## Release Process

1. Update version in `pyproject.toml` + `agentnexus/__init__.py`
2. Update CHANGELOG
3. `git tag v0.x.x && git push origin v0.x.x`
4. CI auto-builds cross-platform binaries via `agentnexus.spec`
