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

# [AgentNexus] recent context, 2026-06-02 8:34pm GMT+8

Legend: 🎯session 🔴bugfix 🟣feature 🔄refactor ✅change 🔵discovery ⚖️decision 🚨security_alert 🔐security_note
Format: ID TIME TYPE TITLE
Fetch details: get_observations([IDs]) | Search: mem-search skill

Stats: 50 obs (17,750t read) | 0t work

### May 27, 2026
S348 优化文件编写工具以展示 diff 内容，并讨论了结构化结果与摘要渲染方案 (May 27, 10:13 AM)
S349 Implement "治本版 Router 架构" — Transform skill router from hardcoded keyword rules to structured retrieval + learned scoring + confidence-calibrated decisions (May 27, 10:17 AM)
S350 Implement "治本版" router architecture redesign for AgentNexus skill routing — replace keyword-based routing with structured skill representation, candidate retrieval, LLM adjudication, and ambiguity-aware decision making (May 27, 11:27 AM)
S351 Implement router architecture redesign for AgentNexus skill routing — evolved from full LLM adjudication pipeline to simpler "recommend + inject into Agent prompt" approach (May 27, 2:27 PM)
S352 Implement "治本版" router architecture redesign for AgentNexus skill routing — evolved from full LLM adjudication to simplified "Router recommends, Agent decides" pattern (May 27, 2:30 PM)
S353 实现 TUI 消息队列：用户在 Agent 执行期间发送消息时，消息入队而非丢失，Agent 完成后自动处理排队消息 (May 27, 2:40 PM)
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
S355 Implement DOCX document formatting capabilities for AgentNexus: constraint-aware editing skill with analyzer, ops, and enforcer modules, plus comprehensive test suite (May 27, 3:20 PM)
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
S356 用户询问如何提升 AgentNexus 项目在 GitHub 上的知名度，助手深入探索项目后给出了分优先级的推广策略建议 (May 31, 9:01 PM)
### Jun 2, 2026
3000 8:14p ⚖️ GitHub Project Visibility Strategy Discussion
3001 " 🔵 AgentNexus Project State Assessment for GitHub Visibility
3002 8:15p 🔵 AgentNexus Full Project Architecture and State Assessment
3003 8:16p 🔵 AgentNexus README.md Current State Baseline
3004 8:17p 🔵 Release Workflow Pipeline Details and Bilingual README Structure
3005 " ✅ README.md Rewritten for GitHub Visibility Optimization
3006 " ✅ README.zh.md Rewritten to Match English README Overhaul
3007 8:20p 🟣 CHANGELOG.md Created and Badge Links Fixed for GitHub Release Readiness
3008 " ✅ README and CHANGELOG Changes Committed to Git
3009 " 🟣 AgentNexus v0.1.0 Tag Created and Pushed to GitHub
S357 提升 AgentNexus 项目 GitHub 知名度 — 完成 P0 优先级任务：README 全面改版和 v0.1.0 首次正式发布 (Jun 2, 8:21 PM)
**Investigated**: 助手深入探索了 AgentNexus 项目的完整架构：README、pyproject.toml、CI/CD workflows、CONTRIBUTING.md、AGENTS.md、wiki 文档、CLI 入口、工具注册表、FSM 引擎、技能系统，以及 git 历史（20+ commits，无 tag，单 main 分支）。Explore agent 完成了 45 次工具调用的全面项目评估。

**Learned**: AgentNexus 是 v0.1.0 alpha 的本地优先 ReAct AI Agent CLI，核心差异化在于：FSM 驱动安全循环（16 状态 × 25 规则）、7 层工具治理网关、213 个安全测试全通过、4 级沙箱降级链。项目有完整 CI/CD 基础设施但从未发布过正式版本。Release workflow 在 v* tag 时自动构建 PyInstaller 跨平台二进制包。README 原本只有 2 个 badge，缺少对比表和视觉演示。

**Completed**: 两个 P0 任务全部完成：(1) README.md 和 README.zh.md 全面改版 — 新增一句话 hook、5 个 badge（Python/License/CI/Security Tests/Platform）、8 维度竞品对比表（vs 典型 Agent 工具）、ASCII 架构图、emoji 特性表、简化 3 步 Quick Start、Tech Stack 和 Contributing 板块；(2) 创建 CHANGELOG.md 记录 v0.1.0 所有特性，创建 v0.1.0 annotated tag 并推送至 GitHub 触发 release workflow 自动生成跨平台二进制包和 GitHub Release。Git commit 包含 261 insertions 和 86 deletions。

**Next Steps**: Release workflow 正在 GitHub Actions 运行中，完成后 GitHub Release 页面将自动生成。下一个 P1 优先级动作是发布到 PyPI（pip install agentnexus）和撰写技术深度文章发布 Show HN。用户尚未选择下一步具体行动。
</claude-mem-context>