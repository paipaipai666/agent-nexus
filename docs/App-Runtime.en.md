> **[中文](App-Runtime.md) | [English](App-Runtime.en.md)**

# 🧩 App Runtime

## Overview

`AppRuntime` is the **unified assembly layer** of AgentNexus, responsible for assembling all subsystems (LLM, tools, agents, memory, skills, extensions, observability, etc.) into a complete application instance in the correct dependency order. It serves as the **entry point and dependency injection container** for the entire system.

**Design Philosophy**: All component creation and assembly is centralized in one place to avoid scattered initialization logic that could cause circular dependencies or ordering errors.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      AppRuntime.build()                       │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ Settings │→│ AgentLLM │→│ ToolReg  │→│ MCPMgr   │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
│       │              │             │             │             │
│       ▼              ▼             ▼             ▼             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │ Extension│→│ MemoryMgr│→│ ReActAgent│→│ SkillReg │     │
│  │ Manager  │  │ VersionMgr│  │          │  │          │     │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘     │
│       │              │             │             │             │
│       ▼              ▼             ▼             ▼             │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              CapabilityRuntime                        │    │
│  └──────────────────────────────────────────────────────┘    │
│       │                                                       │
│       ▼                                                       │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              AppServices                               │    │
│  │  ChatService │ SkillService │ KnowledgeBaseService    │    │
│  │  EvalService │ ConfigService                          │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Core Class: AppRuntime

**File**: `agentnexus/app/runtime.py`

### Data Structure

```python
@dataclass
class AppRuntime:
    settings: Any                    # Global config (Pydantic Settings)
    llm: Any                         # LLM client (AgentLLM)
    executor: Any                    # Tool registry (ToolRegistry)
    agent: Any                       # ReAct Agent
    memory_manager: Any              # Memory manager
    version_manager: Any             # Session version manager
    mcp_manager: Any                 # MCP tool manager
    extension_manager: Any           # Extension manager
    capability_runtime: Any          # Capability runtime
    services: AppServices            # Service facade layer
    subagent_confirm: Any            # Subagent confirmation bridge
    session_id: str                  # Unique session identifier
```

### Core Method: build()

`AppRuntime.build()` is a **classmethod** that assembles all components in 17 steps:

| Step | Operation | Description |
|------|-----------|-------------|
| 1 | Fire `BEFORE_APP_BUILD` hook | Allow extensions to intercept before assembly |
| 2 | Load `Settings` | Pydantic lazy singleton |
| 3 | Create `AgentLLM` | LLM client based on litellm |
| 4 | Create `ToolRegistry` | Tool registry |
| 5 | Create `ConfirmBridge` | Subagent confirmation bridge |
| 6 | Create `MCPToolManager` | MCP dynamic tool management |
| 7 | Generate `session_id` | Format: `{profile}_{uuid12}` |
| 8 | Create `SessionTodoList` | SQLite-persisted todo list |
| 9 | Discover and load extensions | `ExtensionManager.discover()` + `load_enabled()` |
| 10 | Register all tools | 11 tool providers + MCP |
| 11 | Create memory system | `MemoryManager` + `ConversationVersionManager` |
| 12 | Create ReAct agent | Configure conversation mode, confirmation function |
| 13 | Discover skills and create skill service | `SkillRegistry.discover()` + `SkillService` |
| 14 | Create capability runtime | `CapabilityRuntime` |
| 15 | Configure observability | Trace directory + alerting pipeline |
| 16 | Fire `AFTER_APP_BUILD` hook | Allow extensions to modify after assembly |
| 17 | Assemble service layer | `AppServices` with 5 sub-services |

### Session Restoration

```python
@staticmethod
def _restore_memory_from_version(memory, version) -> None:
```

When `restore_session=True`, retrieves the latest STM snapshot from `ConversationVersionManager` to restore short-term memory messages and summary.

### Lifecycle Management

```python
def close(self) -> None:     # Close MCP connections
def __enter__(self):          # Support with statement
def __exit__(self, *exc):     # Auto-call close()
```

## Design Patterns

| Pattern | Application |
|---------|-------------|
| **Builder** | `AppRuntime.build()` is a builder method that step-by-step assembles complex objects |
| **Singleton** | `Settings` uses lazy singleton |
| **Facade** | `AppServices` exposes a unified service interface |
| **Dependency Injection** | All components receive dependencies via constructor |
| **Hook System** | `BEFORE_APP_BUILD` / `AFTER_APP_BUILD` hooks support extension points |
| **Context Manager** | Supports `with AppRuntime.build() as runtime:` usage |

## Usage

### Basic Usage

```python
from agentnexus.app import AppRuntime

runtime = AppRuntime.build(profile="cli")
result = runtime.services.chat.send_message("Hello")
runtime.close()
```

### With Session Restoration

```python
with AppRuntime.build(
    profile="tui",
    session_id="my_session_123",
    restore_session=True,
) as runtime:
    # Previous conversation memory has been restored
    result = runtime.services.chat.send_message("Continue our conversation")
```

### Server Mode

```python
runtime = AppRuntime.build(profile="server")
# LLM set to silent mode (llm.silent = True)
# agent_output set to lambda _: None
```

## Module Relationships

| Module | Relationship |
|--------|-------------|
| `core` | Uses `Settings`, `AgentLLM` |
| `agents` | Creates `ReActAgent` |
| `tools` | Calls `register_all_tools()` to register tools |
| `memory` | Creates `MemoryManager`, `ConversationVersionManager` |
| `skills` | Creates `SkillRegistry`, `SkillService` |
| `extensions` | Creates `ExtensionManager` |
| `capabilities` | Creates `CapabilityRuntime` |
| `observability` | Configures `trace_manager`, alerting pipeline |
| `services` | Assembles `AppServices` |
| `core/hooks` | Fires `BEFORE_APP_BUILD` / `AFTER_APP_BUILD` hooks |
