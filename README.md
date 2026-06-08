> **[中文](README.zh.md) | [English](README.md)**

# AgentNexus

**A production-grade, fully local AI agent with FSM-driven safety, browser automation, desktop automation, and 283 test files.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00C853)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/AgentNexus/AgentNexus/ci.yml?label=CI&logo=github)](https://github.com/AgentNexus/AgentNexus/actions)
[![Tests](https://img.shields.io/badge/Tests-283%20files-00C853)](tests/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/AgentNexus/AgentNexus)

AgentNexus is a **ReAct (Thought→Action→Observe) single-agent** CLI tool that runs entirely on your machine. No cloud dependency. No data leakage. Your vectors, memory, and trace logs never leave your device.

Unlike prompt-only agents, AgentNexus uses a **formal finite state machine** (16 states, 25 transitions) to govern every reasoning loop — making agent behavior deterministic and auditable.

```text
User → CLI/TUI/Desktop → ReAct Agent (FSM + 3-tier LLM Strategy)
       → Tool Gateway (7 Security Gates) → 18 Built-in Tools + MCP
       → Local Storage: ChromaDB | SQLite | JSONL
```

## Why AgentNexus?

| Dimension | AgentNexus | Typical Agent Tools |
| --- | --- | --- |
| **Data Privacy** | 100% local — vectors, memory, traces on-device | Cloud-dependent, data leaves your machine |
| **Security Model** | 7-layer tool governance (RBAC, schema, rate-limit, timeout, risk, HITL, audit) | Basic or no tool-level security |
| **Agent Control** | FSM-driven loop — 16 states, 25 deterministic transitions | Prompt-driven, unpredictable behavior |
| **Code Sandbox** | 4-tier degradation: E2B → bubblewrap/Seatbelt → Docker → local | Single sandbox or none |
| **Security Tests** | 213 dedicated tests across 8 categories | Ad-hoc or no security testing |
| **Observability** | 6-layer system: trace + drift detection + fault attribution + alerting + health checks + improvement loop | Basic logging |
| **Evaluation** | 8 built-in evaluators (agent, trajectory, hallucination, RAG, code...) | Manual or none |
| **Interface** | TUI + Desktop (Electron) + API server | Single interface |

## Features

| Capability | Description |
| --- | --- |
| 🗣️ **Conversation & Tasks** | TUI interface with ReAct loop: plan→execute→observe |
| 🧠 **Local Memory** | STM compression pyramid + LTM (SQLite+ChromaDB, score-based eviction) |
| 📚 **Knowledge Base RAG** | Hybrid retrieval (dense+sparse+RRF+rerank), 8 file formats |
| 🌐 **Browser Automation** | Playwright-based browser control with accessibility tree, CDP support |
| 🖥️ **Desktop Automation** | OS-level accessibility API driven: snapshot, click, type, keyboard, window management |
| 📖 **Wiki System** | Hybrid Wiki + RAG knowledge management, Karpathy's LLM Wiki pattern, mechanical verification, confidence-based routing |
| 🔒 **Security Sandbox** | E2B cloud → native (bubblewrap/Seatbelt) → Docker → local fallback |
| 🛡️ **Tool Audit** | 7 security gates (RBAC/Schema/Rate-limit/Timeout/Risk/HITL/Audit) |
| 📈 **Observability** | 6-layer system: JSONL Trace + drift detection + tool fault attribution + alerting + health checks + improvement loop |
| 📊 **Evaluation** | 8 evaluators (Agent/Trajectory/Hallucination/RAG/Code, etc.) |
| 🎯 **Skill System** | Reusable workflow templates, TF-IDF + learned reranker routing |
| 🔌 **MCP Integration** | Import external tools via stdio/HTTP, full governance |
| 🤖 **Sub-agent Delegation** | Agent-in-Agent isolated subtask execution |
| 🕸️ **Code Knowledge Graph** | Semantic search, relationship queries, context retrieval |

## Quick Start

```bash
# Install
pip install -e ".[dev,eval]"

# Initialize (interactive setup)
nexus init

# Start chatting
nexus tui
```

That's it. No API keys required for local models — just configure your LLM provider during `nexus init`.

### More Commands

```bash
nexus kb add ./docs              # Add documents to knowledge base
nexus wiki init default          # Initialize wiki for a RAG namespace
nexus wiki ingest ./doc.md       # Ingest source document into wiki
nexus wiki query "what is X?"    # Query wiki with confidence-based routing
nexus serve                      # Start HTTP/WebSocket API server for desktop GUI
nexus stats --days 7             # View token cost & task metrics
nexus health                     # Run system health checks
nexus alerts --days 7            # View alert history
nexus audit --limit 20           # View tool audit log
nexus eval agent --days 1        # Run agent quality evaluation
nexus codegraph build            # Build code knowledge graph
```

## Documentation

| Document | Content |
| --- | --- |
| 🏠 [Wiki Home](docs/Home.en.md) | Architecture diagram, core capabilities |
| 🤖 [ReAct Agent](docs/ReAct-Agent.en.md) | FSM state machine, 3-tier LLM strategy, JSON fault tolerance |
| 🔧 [Tool Governance](docs/Tool-Governance.en.md) | 7 security gates, 18 tool parameter tables |
| 🌐 [Browser Automation](docs/Browser-Automation.en.md) | Playwright integration, CDP mode, accessibility tree |
| ⚡ [Code Execution](docs/Code-Execution.en.md) | Sandbox degradation chain, shell blacklist, sub-agents |
| 🧠 [Memory System](docs/Memory-System.en.md) | STM/LTM architecture, compression pyramid, score eviction |
| 📚 [RAG System](docs/RAG-System.en.md) | Hybrid retrieval pipeline, dual ChromaDB clients |
| 🖥️ [Desktop Automation](docs/Computer-Use.en.md) | OS-level accessibility automation, Windows/Linux/macOS |
| 📖 [Wiki System](docs/Wiki-System.en.md) | Hybrid Wiki + RAG, Karpathy pattern, confidence routing |
| ⚙️ [Configuration](docs/Configuration.en.md) | All configuration items reference |
| ⌨️ [Commands](docs/Commands.en.md) | 40+ commands reference |
| 📊 [Evaluation](docs/Evaluation.en.md) | 8 evaluators, RAG metrics |
| 🔒 [Security](docs/Security.en.md) | PII masking, sandbox escape protection |
| 🎯 [Skill System](docs/Skill-System.en.md) | Skill discovery, routing, workflow execution |
| 🔌 [MCP Integration](docs/MCP-Integration.en.md) | External tool import, governance fusion |
| 📈 [Observability](docs/Observability.en.md) | 6-layer observability: trace, drift detection, fault attribution, alerting, health checks, improvement loop |
| 📝 [Prompt System](docs/Prompt-System.en.md) | Template categories, variable injection |
| 🛠 [Development](docs/Development.en.md) | Environment setup, testing, CI pipeline |
| 🤝 [Contributing](docs/Contributing.en.md) | Issue/PR guidelines, testing requirements |

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        AgentNexus                                │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  CLI/TUI     │  Desktop     │  API Server  │  MCP Client        │
│  (Typer)     │  (Electron)  │  (FastAPI)   │  (stdio/HTTP)      │
├──────────────┴──────────────┴──────────────┴────────────────────┤
│                    ReAct Agent Core                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐        │
│  │ FSM      │  │ LLM Strategy │  │ Prompt Builder     │        │
│  │ (16→25)  │  │ (3-tier)     │  │ (templates)        │        │
│  └──────────┘  └──────────────┘  └────────────────────┘        │
├─────────────────────────────────────────────────────────────────┤
│               Tool Gateway (7 Security Gates)                   │
│  RBAC → Schema → Rate-limit → Timeout → Risk → HITL → Audit    │
├─────────────────────────────────────────────────────────────────┤
│                     Tool Execution Layer                         │
│  code_executor · shell · file_ops · web_search · kb_search      │
│  memory_save · subagent · grep_search · web_fetch · browser     │
│  computer_* · wiki · codegraph_* · todo · ...                   │
├──────────┬──────────────┬───────────────────────────────────────┤
│ ChromaDB │   SQLite     │  JSONL Trace Logs                     │
│ (vectors)│  (relational)│  (observability)                      │
└──────────┴──────────────┴───────────────────────────────────────┘
```

## Tech Stack

**Backend**: Python 3.11+ · litellm · Pydantic · Typer+Rich · FastAPI · ChromaDB · sentence-transformers · Playwright

**Desktop**: Electron · React 19 · TypeScript · Vite · TailwindCSS · Zustand

**Testing**: pytest · 194 unit tests · 17 security tests · 12 performance benchmarks · E2E integration tests

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. We welcome bug reports, feature requests, and pull requests.

## License

[MIT](LICENSE) © 2026 AgentNexus
