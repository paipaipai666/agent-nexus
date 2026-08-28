# Changelog

All notable changes to AgentNexus will be documented in this file.

## [Unreleased]

### New Features

- **Context-aware memory extraction and recall** — memory items carry an optional one-sentence context (scene/evidence); embeddings concatenate content+context; conflict check distinguishes same-scene contradictions from different-scene coexisting preferences; `memory_save` accepts `context`, `memory_search` renders it when present
- **Tool-selection keyword ordering** — more specific keywords match first (first-match-wins)

### Bug Fixes

- Fixed memory conflict detection — substring matching treated every "不矛盾" answer as a conflict, incorrectly superseding memories; now uses exact match
- Fixed stale security fragment assertions after prompt localization (`Security Fragment` → `安全原则`)

### Refactoring

- Split `ReActAgentRunner`/`TranscriptCollector` out of `graders.py` into `evaluation/runner.py`
- Modernized typing annotations (`Optional[X]` → `X | None`) across fsm, react_types, tracer, runtime routes, eval CLI
- Removed pass-through `BM25Index` wrapper in `rag/retriever.py` — callers use `rag.ranking.BM25Index` directly

## [0.2.14] - 2026-06-21

### New Features

- **会话统计信息持久化** — session statistics persisted to database
- **会话级别运行时状态统计** — session-level runtime state stats support

### Bug Fixes

- Fixed missing `TOOL_DONE` events in batch tool execution (react_runtime)
- Fixed `_lookup_registry` mutating the static capabilities registry

### CI/CD

- Hardened CI/CD pipeline: caching, security audit, release safety, concurrency control, release version validation
- Removed pip-audit dependency audit job; skip editable installs in pip-audit; suppress chromadb CVE (no trust_remote_code)
- Fixed smoke test to use `version` command; UTF-8 encoding for pyproject.toml read on Windows

## [0.2.13] - 2026-06-20

### New Features

- **Tool system hardening** — structured errors, concurrent dispatch, and description boundaries

### Refactoring

- P2 structural improvements — split God Objects, extracted subpackages

### Bug Fixes

- Addressed 7 CRITICAL, 15 HIGH, 18 MEDIUM issues from code review, plus all LOW issues with added observability test coverage
- CORS now allows localhost on any port via regex

### Tests

- Added 160 tests for wiki and skills/router modules
- Adapted 7 integration/security tests to review fixes; mocked `_call_via_litellm` in stream fallback test

## [0.2.12] - 2026-06-18

### Refactoring

- 会话隔离的短时记忆管理 — session-isolated STM management in chat

### Bug Fixes

- Fixed tool cards stuck at "running" by bypassing journal parsing
- Fixed thinking content order — reset reasoning message ID on tool call

## [0.2.11] - 2026-06-18

### Bug Fixes

- Fixed TypeScript errors in desktop test files

## [0.2.10] - 2026-06-18

### New Features

- **Multi-session parallel execution** — concurrent agent runs across sessions
- **ChatService session isolation** — constructor supports per-session isolation

### Bug Fixes

- Embeddings prefer local cache to avoid HuggingFace timeouts
- Fixed history messages lost after session switch
- Fixed streaming output lost when switching sessions during an agent answer
- Fixed empty "New session" card appearing in sidebar on every startup
- Fixed streaming content loss after page navigation and sidebar not refreshing for new sessions
- Mocked `_recursive_split` in chunking tests to avoid langchain import timeout in CI

## [0.2.9] - 2026-06-15

### Performance

- **GPU embedding FP16 half-precision** — 2x throughput for RAG embedding

### New Features

- **STM 会话隔离与多会话并发支持** — session-isolated short-term memory
- **会话预览功能** — session preview with database migration
- **Per-session version manager** — independent conversation version manager per session
- **WebSocket 连接管理改进** — chat WebSocket connection management with acknowledgment bridging
- **`display_only` metadata** — agent display-only metadata support and final answer handling
- **Memory thread safety** — locks and atomic commits in memory system

### Bug Fixes

- Fixed thought content lost after session switch (GUI)
- Fixed streaming content lost on page switch (session)
- Fixed infinite repeated API refresh on new chat page
- Sidebar session card timestamp now only updates on user questions
- Fixed tool execution argument mapping in ReAct agent
- Fixed import order in memory version manager; fixed reranker loading in RAG retriever tests

### Refactoring

- Reworked `kb_search` tool to optimize retriever initialization

## [0.2.8] - 2026-06-11

### New Features

- **Wiki 回填命令** — CLI wiki backfill command

### Security

- Hardened path traversal defense: restored symlink detection, reliable normpath-based detection, consistent `resolve(strict=False)` for Windows 8.3 name compatibility, type-safe `Path.home` mock; skipped symlink test on Windows

### Tests

- Fixed all 149 pre-existing unit test failures
- Fixed integration and security test failures, async tests, trace_manager isolation, DoS test timeout, HuggingFace timeout
- Made cwd resilient across all modules and tests; fixed cwd isolation and Windows path normalization

### CI/CD

- Install rag/server/tui optional dependencies for test collection; fixed ruff lint and import sorting

### Documentation

- Updated README documentation links and test statistics

## [0.2.7] - 2026-06-09

### CI/CD

- Use glob pattern for chmod on renamed backend binaries

## [0.2.6] - 2026-06-09

### CI/CD

- Rename backend binaries with platform suffix to avoid release upload conflict

## [0.2.5] - 2026-06-09

### Bug Fixes

- Added homepage, author, description to desktop package for deb packaging

## [0.2.4] - 2026-06-09

### CI/CD

- Split build steps, per-platform electron-builder, chmod staged binary

## [0.2.3] - 2026-06-09

### CI/CD

- Stage backend binary to `desktop/backend/` before electron-builder
- Split build steps with diagnostics and fail-fast disabled

## [0.2.2] - 2026-06-09

### CI/CD

- Use bash shell for ls command on Windows runner

## [0.2.1] - 2026-06-09

### CI/CD

- Moved extraResources to platform blocks; fixed .github gitignore

## [0.2.0] - 2026-06-04

### 🧪 Evaluation Framework Overhaul — Anthropic Methodology Compliance

全面升级评估体系，使其符合 Anthropic "Demystifying evals for AI agents" 方法论框架。

### New Features

- **Unified Task/Trial/Grader abstractions** — `EvalTask`, `TrialResult`, `GraderConfig` dataclasses for standardized evaluation inputs
- **Evaluation Harness** — end-to-end `EvalHarness` that runs tasks concurrently, records all steps, grades outputs, and aggregates results
- **pass@k / pass^k statistics** — precise binomial estimator for multi-trial evaluation metrics
- **Composite Graders** — weighted, binary, and hybrid scoring modes combining multiple graders per task
- **8 built-in grader types** — transcript, tool_calls, state_check, static_analysis, llm_rubric, trajectory, hallucination, coherence
- **Capability vs Regression eval separation** — `EvalSuite` with `eval_type` field and `SuiteThresholds`
- **Baseline management** — save, load, compare baselines for regression detection
- **YAML task dataset format** — declarative task definitions with graders, reference solutions, and metadata
- **Eval dataset management** — `EvalDataset` class with load, filter, validate, stats
- **CLI eval task commands** — `nexus eval task list/show/validate/run`, `nexus eval suite list/run/show`
- **CLI baseline commands** — `nexus eval suite baseline list/save/compare`
- **Server API endpoints** — REST API for tasks, suites, baselines at `/api/eval/`
- **Desktop GUI Eval page** — full evaluation dashboard with tasks, suites, results, and baselines tabs
- **CI/CD eval gate** — `eval-gate` job in CI pipeline with dataset validation
- **Bootstrap confidence intervals** — for all LLM-judged metrics
- **Eval saturation monitoring** — automatic suggestions when evals are saturated

### Bug Fixes

- Fixed `TrajectoryGraderAdapter` — was calling non-existent `evaluate_transcript()`, now calls `_evaluate_one()` directly
- Fixed `HallucinationGraderAdapter` — was calling non-existent `detect()`, now calls `_evaluate_one()` directly
- Fixed `CoherenceGraderAdapter` — was calling non-existent `evaluate()`, now calls `_evaluate_one()` directly
- Implemented `CodeExecutionGrader` — runs test assertions in isolated subprocess
- Implemented `ReActAgentRunner` — real agent runner that integrates with ReActAgent
- Implemented `TranscriptCollector` — collects spans from TraceManager
- Added environment isolation (setup/teardown) to `EvalHarness`
- Fixed broken `eval_cmd.py` import
- Fixed syntax error in `graders.py` (Chinese text with unescaped braces)

### Files Added

- `agentnexus/evaluation/task.py` — Task/Suite/GraderConfig models + YAML loader
- `agentnexus/evaluation/trial.py` — TrialResult/TaskReport/GraderScore models
- `agentnexus/evaluation/graders.py` — Grader interface hierarchy (9 types + composite + agent runner + transcript collector)
- `agentnexus/evaluation/harness.py` — EvalHarness + SuiteReport + environment isolation
- `agentnexus/evaluation/statistics.py` — pass@k, pass^k, bootstrap CI, consistency, saturation
- `agentnexus/evaluation/baseline.py` — BaselineManager + RegressionReport
- `agentnexus/evaluation/dataset.py` — EvalDataset + JSONL migration
- `agentnexus/eval_tasks/` — 65 YAML task definitions across 6 suites (coding, tool_use, reasoning, conversation, rag, regression)
- `agentnexus/cli/eval/task.py` — CLI commands for task/suite/baseline management
- `agentnexus/cli/eval/transcript.py` — CLI commands for transcript viewing and analysis
- `scripts/migrate_eval_tasks.py` — Migration script for JSONL → YAML conversion
- `desktop/src/pages/EvalPage.tsx` — Desktop GUI evaluation page

### Files Modified

- `agentnexus/evaluation/__init__.py` — exports all new public API
- `agentnexus/cli/eval_cmd.py` — fixed broken import
- `agentnexus/cli/eval/__init__.py` — added task module registration
- `agentnexus/services/eval.py` — complete rewrite with task/suite/baseline support
- `agentnexus/server/routes/eval_routes.py` — 12 new REST endpoints
- `desktop/src/services/api.ts` — eval API client methods
- `desktop/src/App.tsx` — added /eval route
- `desktop/src/components/layout/Sidebar.tsx` — added Eval nav item
- `.github/workflows/ci.yml` — added eval-gate job

## [0.1.0] - 2026-06-02

### 🎉 Initial Release

First public release of AgentNexus — a production-grade, fully local ReAct single-agent CLI tool.

### Highlights

- **FSM-driven safety loop** — 16 states, 25 deterministic transitions govern the agent reasoning cycle
- **7-layer tool governance** — RBAC, schema validation, rate limiting, timeout, risk assessment, HITL, audit logging
- **213 security tests** — covering code execution, injection, sandbox escape, privilege escalation, and more
- **Fully local storage** — ChromaDB (vectors) + SQLite (relational) + JSONL (traces), nothing leaves your device
- **4-tier sandbox degradation** — E2B → bubblewrap/Seatbelt → Docker → local fallback

### Core Features

- ReAct agent with 3-tier LLM strategy degradation
- 17 built-in tools with full governance
- MCP integration (stdio/HTTP) with governance fusion
- Short-term memory (STM compression pyramid) + long-term memory (SQLite + ChromaDB)
- Knowledge base RAG with hybrid retrieval (dense + sparse + RBF + rerank)
- Code knowledge graph with semantic search
- Skill system with TF-IDF + learned reranker routing
- Sub-agent delegation for isolated task execution
- Observability: JSONL trace, token cost statistics, audit logs
- 8 built-in evaluators (agent, trajectory, hallucination, RAG, code, coherence, tool selection)

### Interfaces

- **CLI/TUI** — Terminal UI built with Typer + Rich + Textual
- **Desktop** — Electron + React 19 + TypeScript application
- **API Server** — FastAPI server with auth and rate limiting

### Platform Support

- Python 3.11, 3.12, 3.13
- Windows, Linux, macOS
- CI matrix: Ubuntu + Windows × Python 3.11/3.12/3.13
- Cross-platform binaries via PyInstaller
