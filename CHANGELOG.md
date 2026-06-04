# Changelog

All notable changes to AgentNexus will be documented in this file.

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

### Files Added

- `agentnexus/evaluation/task.py` — Task/Suite/GraderConfig models + YAML loader
- `agentnexus/evaluation/trial.py` — TrialResult/TaskReport/GraderScore models
- `agentnexus/evaluation/graders.py` — Grader interface hierarchy (8 types + composite)
- `agentnexus/evaluation/harness.py` — EvalHarness + SuiteReport
- `agentnexus/evaluation/statistics.py` — pass@k, pass^k, bootstrap CI, consistency, saturation
- `agentnexus/evaluation/baseline.py` — BaselineManager + RegressionReport
- `agentnexus/evaluation/dataset.py` — EvalDataset + JSONL migration
- `agentnexus/eval_tasks/` — YAML task definitions (5 tasks across 5 categories + regression)
- `agentnexus/cli/eval/task.py` — CLI commands for task/suite/baseline management
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
