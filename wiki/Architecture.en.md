> **[中文](Architecture.md) | [English](Architecture.en.md)**

# 🏗 Overall Architecture

## Layered Architecture

```
┌───────────────────────────────────────────────────┐
│              CLI Layer (Typer + Rich)               │
│  6 top-level commands + 5 subcommand groups = 31    │
│  nexus init / config / tui / stats / audit / ver    │
│  nexus kb / memory / logs / eval / skill             │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│              Service Facade Layer                   │
│  ChatService  │  SkillService  │  AppServices       │
│  Session mgmt │  Skill routing │  Service composition│
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│           ReActAgent (FSM Driven)                   │
│  16 states × 25 transitions                         │
│  CallingStrategy 3-tier: Native → JSON → Prompt     │
│  AgentLLM (litellm streaming, 3x exponential backoff)│
│  Batch sequential execution, max_steps hard limit   │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│         ToolRegistry Governance Gateway             │
│         (7 Security Gates)                          │
│  RBAC → Schema → Rate-limit → Timeout              │
│  → Risk → HITL → Audit                              │
│  12 built-in tools + MCP dynamic import + sub-agent │
└──────────────────┬────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────┐
│              Local Infrastructure Layer              │
│  ChromaDB (Vectors)  SQLite (Relational)            │
│  JSONL (Traces)                                     │
│  SentenceTransformers + BM25 + BGE-Reranker         │
│  E2B / bubblewrap / Docker / Local sandbox          │
└───────────────────────────────────────────────────┘
```

## Project Structure

```
agentnexus/
├── __main__.py              ── python -m entry point
├── app/runtime.py            ── AppRuntime dependency assembly
├── cli/                      ── Typer CLI layer
├── agents/                   ── ReActAgent + FSM
├── core/                     ── Settings + LLM
├── evaluation/               ── 8 evaluators
├── extensions/               ── plugin system
├── memory/                   ── STM/LTM/version control
├── observability/            ── Trace + Token statistics
├── prompts/                  ── 17 .txt templates
├── rag/                      ── ChromaDB client/retrieval/chunking
├── services/                 ── service facade
├── skills/                   ── Skill discovery/routing/runtime
├── tools/                    ── registry/providers/MCP
└── tui/                      ── Textual interface
```

## Service Startup Sequence

`AppRuntime.build()` assembles all components in this order:

1. Load `Settings` (Pydantic lazy singleton)
2. Create `AgentLLM` + `ToolExecutor` + `ConfirmBridge`
3. Initialize `MCPToolManager` (if `mcp_enabled=True`)
4. Load `ExtensionManager`
5. `register_all_tools()` — register 6 providers + MCP
6. Create `MemoryManager` + `ConversationVersionManager`
7. Create `ReActAgent`
8. `SkillRegistry.discover()` — scan skill directories
9. Create `SkillService`
10. Configure trace output directory
11. Assemble `AppServices` (Chat/Config/Eval/KB/Skill)
12. Return `AppRuntime` instance

> See [ReAct Agent](ReAct-Agent.en.md) for FSM details, [Tool Governance](Tool-Governance.en.md) for the 7 security gates.
