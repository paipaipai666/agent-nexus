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


<claude-mem-context>
# Memory Context

# [AgentNexus] recent context, 2026-06-02 9:10am GMT+8

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (16,548t read) | 0t work

### May 27, 2026
S346 提出一版可落地的高内聚低耦合代码 diff 草案，用于让 file_write 返回结构化结果和 diff 预览 (May 27, 10:07 AM)
S347 Continue Router v2 design discussion with a concrete non-rule-heavy module architecture for AgentNexus (May 27, 10:10 AM)
S348 优化文件编写工具以展示 diff 内容，并讨论了结构化结果与摘要渲染方案 (May 27, 10:13 AM)
S349 Implement "治本版 Router 架构" — Transform skill router from hardcoded keyword rules to structured retrieval + learned scoring + confidence-calibrated decisions (May 27, 10:17 AM)
S350 Implement "治本版" router architecture redesign for AgentNexus skill routing — replace keyword-based routing with structured skill representation, candidate retrieval, LLM adjudication, and ambiguity-aware decision making (May 27, 11:27 AM)
S351 Implement router architecture redesign for AgentNexus skill routing — evolved from full LLM adjudication pipeline to simpler "recommend + inject into Agent prompt" approach (May 27, 2:27 PM)
S352 Implement "治本版" router architecture redesign for AgentNexus skill routing — evolved from full LLM adjudication to simplified "Router recommends, Agent decides" pattern (May 27, 2:30 PM)
S353 实现 TUI 消息队列：用户在 Agent 执行期间发送消息时，消息入队而非丢失，Agent 完成后自动处理排队消息 (May 27, 2:40 PM)
2949 3:00p 🔵 Long agent responses cause JSON parse failures in tool output
2950 " 🟣 Message queue fields added to ChatService constructor
2951 3:01p 🔵 JSON Parse Failures in Agent Responses with Long Chinese+Code Content
2952 " 🟣 ChatService message queue API implemented
2953 3:02p 🟣 Task #13 completed: ChatService message queue backend done
2954 " ✅ Task #14 started: TUI queue integration begins
2955 3:03p 🔵 Current TUI input handler reads on_input_bar_app_submit() for queue integration
2956 " 🔵 _run_agent() method structure for queue integration point
2957 3:04p 🔵 _run_agent() completion and error paths identified for queue drain integration
2958 3:06p 🔵 _run_agent() final cleanup block: exact queue drain insertion point confirmed
2959 3:07p 🟣 TUI input handler modified to queue messages when agent is busy
2960 3:10p ✅ _run_agent() cleanup block targeted for queue drain addition
2961 3:11p 🟣 _drain_message_queue() added to ChatScreen for auto-processing queued messages
2962 " 🔵 Agent tool JSON parse failures on long content generation
2964 3:12p 🔵 AgentNexus tool result size management architecture traced
2965 " 🔵 max_tokens never passed to LLM despite ModelCapabilities detection
2963 " 🟣 Queue drain added to all three _run_agent() exit paths
2966 " 🔵 JSON Parse Failures in Long Agent Responses
2967 3:15p 🟣 Message queue implementation passes all existing tests
2968 3:17p ✅ Lint fix: removed unnecessary f-string prefix in _drain_message_queue
S354 实现 TUI 消息队列并编写单元测试，确保用户在 Agent 执行期间发送的消息不丢失 (May 27, 3:17 PM)
2969 3:56p 🔵 JSON parse failures in agent responses correlate with long output content
2970 3:57p 🔵 AgentNexus test suite structure includes react, file, and json-related test modules
2971 " 🔵 AgentNexus file_ops tool tests cover path safety, fingerprinting, read, and write operations
2972 3:58p 🔵 ReActAgent JSON parsing tests reveal response classification logic but lack auto-fix coverage
2974 3:59p 🔵 AgentNexus file_write tool supports 4 modes, version conflict detection, and large-diff patch references
2975 4:00p 🟣 New TestJsonStringInternalFix test class added to cover JSON string corruption scenarios
2976 4:34p 🔵 AgentNexus chat input submission flow traced for debugging agent-start bug
2977 4:37p 🔵 _running flag lifecycle mapped across chat.py for agent-start bug investigation
2978 4:45p ⚖️ Router architecture redesign from rule-based to learned-reranker system
2979 4:46p 🔵 Existing chat screen skill routing infrastructure
2980 4:49p 🔵 Agent execution flow in chat screen uses Worker + Turn + CapabilityRuntime
### May 31, 2026
2981 8:53p 🔵 File Read/Write Tool Capabilities Questioned
2982 8:54p 🔵 AgentNexus Project Has Existing DOCX Utilities
2983 " ✅ Utils Package Init File Created
2984 " 🔵 AgentNexus DOCX Module Architecture Revealed
2985 " 🔴 Fixed Fragile ValidationIssue Import Pattern in DOCX Module
2986 " ✅ Task 4 Marked Complete in AgentNexus Project
2987 " ✅ Task 5 Started in AgentNexus Project
2988 8:55p 🟣 Document Formatting Skill Created for AgentNexus
2989 " 🟣 Doc-Format Skill Instructions Created with Constraint-Aware Editing Workflow
2990 " ✅ Doc-Format Skill Task Sequence: Task 5 Completed, Task 7 Started
2991 8:56p 🔵 AgentNexus Project Architecture and Dependencies Revealed
2992 " 🔴 python-docx Dependency Added to AgentNexus RAG Extras
2993 " ✅ Task 7 Completed, Task 6 Started in AgentNexus
2994 8:57p 🟣 Comprehensive Test Suite Created for DOCX Analyzer Module
2995 8:58p 🟣 Comprehensive Test Suite Created for DOCX Operations Module
2996 8:59p ✅ python-docx>=1.1 Installed for DOCX Test Suite
2997 9:00p 🔴 Fixed Test Assertion for python-docx Default Paper Size
2998 " 🟣 All 36 DOCX Unit Tests Passing
2999 " ✅ Task 6 Completed: DOCX Test Suite Validation
S355 Implement DOCX document formatting capabilities for AgentNexus: constraint-aware editing skill with analyzer, ops, and enforcer modules, plus comprehensive test suite (May 31, 9:01 PM)
**Investigated**: Examined agentnexus/utils/docx/ module structure (5 files: analyzer.py, ops.py, enforcer.py, constraints.py, __init__.py), pyproject.toml dependency tree, and existing project architecture (ReAct-based CLI agent tool with litellm, OpenAI, MCP support)

**Learned**: AgentNexus is a "ReAct 单智能体任务协同 CLI 工具" (ReAct single-agent task coordination CLI tool) with a three-layer docx module: analyzer (stdlib OpenXML parsing, zero dependencies), ops (python-docx document operations with constraint checking), and enforcer (format validation). The analyzer uses Letter paper size by default (215.9mm × 279.4mm), NOT A4. All ops functions return structured dicts with status/message/warnings fields. The doc-format skill uses constraint-aware editing workflow: analyze → understand rules → edit via ops → validate results. Python-docx was missing from dependencies and was added to [rag] extras.

**Completed**: All 7 tasks completed with 36/36 tests passing:
- Fixed fragile ValidationIssue import in agentnexus/utils/docx/__init__.py (replaced hasattr conditional with direct import)
- Created doc-format skill: agentnexus/skills/doc_format/skill.yaml (triggers: /doc-format, 编辑word文档, 编辑docx, 文档排版, 论文格式, 公文格式)
- Created comprehensive instructions.md with 4-step constraint-aware workflow, table/image/paragraph rules, ops API documentation, and special scenarios (academic papers, official documents 公文)
- Added python-docx>=1.1 to pyproject.toml [rag] extras
- Created tests/unit/test_docx_analyzer.py (13 tests: basic analysis, page constraints, table analysis, style analysis, summary output, paragraph reading)
- Created tests/unit/test_docx_ops.py (21 tests: read, replace, table edit, insert, page settings, save, validation)
- Fixed test assertion bug: python-docx defaults to US Letter, not A4 (height assertion 290→270mm)
- Installed python-docx dependency for test execution

**Next Steps**: Task 6 was the last completed task. All planned work for the doc-format skill appears complete. Potential next steps could include: testing the skill end-to-end with real documents, adding the skill to a skill registry, or working on other AgentNexus features.
</claude-mem-context>