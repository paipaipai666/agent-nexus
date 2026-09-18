# Changelog

All notable changes to AgentNexus will be documented in this file.

## [Unreleased]

### Changed

- **Desktop: v2 UI 全面重设计（Linear 式精致暗色）** — 桌面端前端按 `designs/mockups/v2-ui.yaml` 规范重构。新应用外壳：36px 自定义 titlebar（breadcrumb / 搜索触发 / 主题切换 / 窗口控制）+ VS Code 式 48px Activity Bar（Chat、Skills、MCP、Memory、Knowledge、Wiki、Plugins、Stats、Health、Alerts、Audit、Eval、Settings 全部提升为一级导航）+ 随路由切换的 232px Context Sidebar（仅 Chat 区显示项目/会话）+ 24px mono 状态栏（含上下文用量条）。新增 Ctrl+K 全局命令面板（分组 Recent/Navigation/Actions、实时过滤、全键盘操作）与 Ctrl+B 侧边栏折叠（250ms 宽度动画）。双主题 token 体系重写（暗色近黑四级明度阶梯 #0a0b0d→#1e2127、1px 半透明边框、暗色卡片 inset 顶部高光、accent 微渐变主按钮；亮色完整对照；默认改为暗色，切换 200ms 动画）。圆角体系 8/12/999。字体：Anton 移除，Inter + Geist Mono，基础字号 13px 紧凑密度。旧 /settings/* 子路由全部提升为顶级路由（/stats、/skills 等），SettingsLayout 删除。Chat 页用户消息改为右对齐弱化气泡、工具卡/输入框/空状态按 v2 组件规格重制；Stats 页改 segmented 时间范围 + v2 KPI 卡；其余页面卡片统一切到 surface-2 + card-highlight。

### Bug Fixes

- **OpenAI 兼容端点模型名被误截断** — `OpenAIProvider` 对含 `/` 的模型名无条件 `split("/", 1)[1]`, 把 SiliconFlow 的 `deepseek-ai/DeepSeek-V4-Flash` 截成 `DeepSeek-V4-Flash` 发给端点 (400 Model does not exist)。改为白名单化剥离: 仅已知 litellm provider 前缀 (`deepseek/`, `openai/`, `zhipu/` 等) 才剥离; 命名空间式模型名 (SiliconFlow/Groq/OpenRouter 等) 保留全名。
- **流式 content 混入 think 标签残留** — SiliconFlow 部署的 DeepSeek-V4 在流式下 reasoning_content 正确分离, 但 delta.content 残留 `</think>` 片段 (如 `42</think>42`), 污染 ReAct JSON 解析。`OpenAIProvider` 累加 content 时剥离 think 标签。
- **Eval runner 工具集为空** — `ReActAgentRunner` 构造 `ToolRegistry()` 后未调 `register_all_tools`, eval 下 agent 拿到空工具集, tool_use 类 task 被系统性低估 (regression suite 实测 6/10 → 修复后工具真实执行)。现与 `AppRuntime` 对齐注册全部内置 provider 工具 (non_interactive)。
- **HumanEval 评分器 markdown 围栏 SyntaxError** — `HumanEvalEvaluator.evaluate()` 直接执行候选代码, 对 LLM 输出里几乎必然存在的 ` ```python ``` ` 围栏零容错, 真实模型成绩会被系统性压到 0。执行前现剥离围栏。官方 openai/human-eval 164 题 oracle 验证: gold 164/164 (100%), 语义破坏注入 24/24 检出、0 误报 (`experiments/run_humaneval_oracle.py`)

- **Eval runner 真实 transcript 时间轴** — `ReActAgentRunner` 此前在 eval 中直接调用 `agent.run()` 而没有建立 trace 上下文, agent 循环的 span 埋点全部不生效, `_collect_transcript` 只能用 `i * 2.0` 编造时间戳重建 transcript。现在 runner 会为每个 trial 建立真实 trace 上下文（已有上游 trace 时不劫持），transcript 直接来自真实 spans（墙钟时间、`latency_ms`、真实 input/output），并将 agent 埋点名 `plan_node` 映射为 grader 协议名 `llm`。无 trace 可用时的兜底重建不再编造时间戳，显式标注 `reconstructed: true`。trial metadata 新增 `trace_id` 供 replay/审计回溯。

### Testing

- 新增 `tests/unit/test_eval_runner_transcript.py`（5 个行为测试：真实时钟、grader 协议映射、trace_id 记录、不劫持上游 trace、兜底不编造时间）
- 新增 `experiments/e2e_eval_smoke.py` — mock 模型端到端验证 runner→trace→grader→report 全链路（含正反两个 grader 判定）

## [0.2.16] - 2026-09-10

### Changed

- **Desktop: Codex-style per-session projects** — the sidebar now groups chat sessions under project folders; a project is chosen when a chat is created and stays fixed for that chat's lifetime. Removed the global workspace switcher (`PUT /api/config/workspace` + `os.chdir`, `PUT /api/session/{id}/workspace` rebind) — sessions carry their own workspace folders, so switching projects never disturbs other sessions. Electron persists `{ projects, lastProject }` in `userData/workspace.json` (legacy single-workspace format is migrated).
### New Features

- **Desktop: multi-provider model switching** — configure multiple named LLM providers (name/model_id/base_url/api_key/timeout) in Settings → Model Providers, then switch the active model live from a picker in the chat input's HUD row. Switching hot-reconfigures the shared `AgentLLM` (no restart); the flat `llm_*` settings remain the default/fallback profile. New endpoints: `GET/PUT /api/config/llm/providers`, `POST /api/config/llm/active`. The model display moved from the status bar into the chat input area.

### Bug Fixes

- Fixed `/api/memory/short/history` resolving sessions against the process cwd — it now looks up the latest session across workspaces and adopts the session's stored workspace instead of re-registering it under the server cwd

## [0.2.15] - 2026-08-28

### New Features

- **Context-aware memory extraction and recall** — memory items carry an optional one-sentence context (scene/evidence); embeddings concatenate content+context; conflict check distinguishes same-scene contradictions from different-scene coexisting preferences; `memory_save` accepts `context`, `memory_search` renders it when present
- **Tool-selection keyword ordering** — more specific keywords match first (first-match-wins)

### Bug Fixes

- Fixed memory conflict detection — substring matching treated every "不矛盾" answer as a conflict, incorrectly superseding memories; now uses exact match
- Fixed stale security fragment assertions after prompt localization (`Security Fragment` → `安全原则`)

### Refactoring

- Split `ReActAgentRunner`/`TranscriptCollector` out of `graders.py` into `evaluation/runner.py`
- **Split `MemoryManager` monolith** (~620 → ~330 lines) — `compaction_engine.py` owns the 5-layer compaction pyramid and its state; `extraction_pipeline.py` owns the two-level extraction gate; manager is now a facade with attribute forwarding for backward compatibility
- Modernized typing annotations (`Optional[X]` → `X | None`) across fsm, react_types, tracer, runtime routes, eval CLI
- Removed pass-through `BM25Index` wrapper in `rag/retriever.py` — callers use `rag.ranking.BM25Index` directly
### Documentation

- **CLI help text standardized to Chinese** (CLI-018) — all `help=` strings and command docstrings across 19 CLI modules; proper nouns stay in English
- Backfilled CHANGELOG for v0.2.1–v0.2.14

### Tests

- Fixed pre-existing perf benchmark failures — fixtures predated CircuitBreaker; projection benchmarks now call the real `project_mild`/`project_aggressive` APIs

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
