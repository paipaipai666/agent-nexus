> **[中文](README.zh.md) | [English](README.md)**

# AgentNexus

**A production-grade, fully local AI agent with FSM-driven safety and 213 security tests.**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-00C853)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/AgentNexus/AgentNexus/ci.yml?label=CI&logo=github)](https://github.com/AgentNexus/AgentNexus/actions)
[![Security Tests](https://img.shields.io/badge/Security%20Tests-213%20passed-00C853)](wiki/Security.md)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)](https://github.com/AgentNexus/AgentNexus)

AgentNexus is a **ReAct (Thought→Action→Observe) single-agent** CLI tool that runs entirely on your machine. No cloud dependency. No data leakage. Your vectors, memory, and trace logs never leave your device.

Unlike prompt-only agents, AgentNexus uses a **formal finite state machine** (16 states, 25 transitions) to govern every reasoning loop — making agent behavior deterministic and auditable.

```
User → CLI/TUI/Desktop → ReAct Agent (FSM + 3-tier LLM Strategy)
       → Tool Gateway (7 Security Gates) → 17 Built-in Tools + MCP
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
| **Observability** | Full JSONL trace + token cost stats + audit log | Basic logging |
| **Evaluation** | 8 built-in evaluators (agent, trajectory, hallucination, RAG, code...) | Manual or none |
| **Interface** | TUI + Desktop (Electron) + API server | Single interface |

## Features

| Capability | Description |
|------|-----------|
| 🗣️ **Conversation & Tasks** | TUI interface with ReAct loop: plan→execute→observe |
| 🧠 **Local Memory** | STM compression pyramid + LTM (SQLite+ChromaDB, score-based eviction) |
| 📚 **Knowledge Base RAG** | Hybrid retrieval (dense+sparse+RRF+rerank), 8 file formats |
| 🔒 **Security Sandbox** | E2B cloud → native (bubblewrap/Seatbelt) → Docker → local fallback |
| 🛡️ **Tool Audit** | 7 security gates (RBAC/Schema/Rate-limit/Timeout/Risk/HITL/Audit) |
| 📈 **Observability** | JSONL Trace + Token cost statistics |
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
nexus stats --days 7             # View token cost statistics
nexus eval agent --days 1        # Run agent quality evaluation
nexus codegraph build            # Build code knowledge graph
```

## Documentation

| Document | Content |
|------|------|
| 🏠 [Wiki Home](wiki/Home.en.md) | Architecture diagram, core capabilities |
| 🤖 [ReAct Agent](wiki/ReAct-Agent.en.md) | FSM state machine, 3-tier LLM strategy, JSON fault tolerance |
| 🔧 [Tool Governance](wiki/Tool-Governance.en.md) | 7 security gates, 17 tool parameter tables |
| ⚡ [Code Execution](wiki/Code-Execution.en.md) | Sandbox degradation chain, shell blacklist, sub-agents |
| 🧠 [Memory System](wiki/Memory-System.en.md) | STM/LTM architecture, compression pyramid, score eviction |
| 📚 [RAG System](wiki/RAG-System.en.md) | Hybrid retrieval pipeline, dual ChromaDB clients |
| ⚙️ [Configuration](wiki/Configuration.en.md) | All configuration items reference |
| ⌨️ [Commands](wiki/Commands.en.md) | 40 commands reference |
| 📊 [Evaluation](wiki/Evaluation.en.md) | 8 evaluators, RAG metrics |
| 🔒 [Security](wiki/Security.md) | PII masking, sandbox escape protection |
| 🎯 [Skill System](wiki/Skill-System.en.md) | Skill discovery, routing, workflow execution |
| 🔌 [MCP Integration](wiki/MCP-Integration.en.md) | External tool import, governance fusion |
| 📈 [Observability](wiki/Observability.en.md) | Trace system, token statistics, audit logs |
| 📝 [Prompt System](wiki/Prompt-System.en.md) | Template categories, variable injection |
| 🛠 [Development](wiki/Development.en.md) | Environment setup, testing, CI pipeline |
| 🤝 [Contributing](wiki/Contributing.en.md) | Issue/PR guidelines, testing requirements |

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                      AgentNexus                              │
├─────────────┬─────────────┬──────────────┬─────────────────┤
│  CLI/TUI    │  Desktop    │  API Server  │  MCP Client     │
│  (Typer)    │  (Electron) │  (FastAPI)   │  (stdio/HTTP)   │
├─────────────┴─────────────┴──────────────┴─────────────────┤
│                   ReAct Agent Core                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ FSM      │  │ LLM Strategy │  │ Prompt Builder     │   │
│  │ (16→25)  │  │ (3-tier)     │  │ (templates)        │   │
│  └──────────┘  └──────────────┘  └────────────────────┘   │
├────────────────────────────────────────────────────────────┤
│              Tool Gateway (7 Security Gates)                │
│  RBAC → Schema → Rate-limit → Timeout → Risk → HITL → Log │
├────────────────────────────────────────────────────────────┤
│                    Tool Execution Layer                      │
│  code_executor · shell · file_ops · web_search · kb_search  │
│  memory_save · subagent · grep_search · web_fetch · ...     │
├──────────┬──────────────┬──────────────────────────────────┤
│ ChromaDB │   SQLite     │  JSONL Trace Logs                 │
│ (vectors)│  (relational)│  (observability)                  │
└──────────┴──────────────┴──────────────────────────────────┘
```

## Tech Stack

**Backend**: Python 3.11+ · litellm · Pydantic · Typer+Rich · FastAPI · ChromaDB · sentence-transformers

**Desktop**: Electron · React 19 · TypeScript · Vite · TailwindCSS · Zustand

**Testing**: pytest · 80+ unit tests · 213 security tests · 12 performance benchmarks

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. We welcome bug reports, feature requests, and pull requests.

## License

[MIT](LICENSE) © 2026 AgentNexus
