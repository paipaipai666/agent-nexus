> **[中文](App-Runtime.md) | [English](App-Runtime.en.md)**

# 🧩 应用运行时 (App Runtime)

## 概述

`AppRuntime` 是 AgentNexus 的**统一组装层**，负责将所有子系统（LLM、工具、代理、记忆、技能、扩展、可观测性等）按照正确的依赖顺序组装成一个完整的应用实例。它是整个系统的**入口点和依赖注入容器**。

**设计哲学**：所有组件的创建和组装集中在一个地方，避免分散的初始化逻辑导致循环依赖或顺序错误。

## 架构设计

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

## 核心类：AppRuntime

**文件**：`agentnexus/app/runtime.py`

### 数据结构

```python
@dataclass
class AppRuntime:
    settings: Any                    # 全局配置 (Pydantic Settings)
    llm: Any                         # LLM 客户端 (AgentLLM)
    executor: Any                    # 工具注册表 (ToolRegistry)
    agent: Any                       # ReAct 代理
    memory_manager: Any              # 记忆管理器
    version_manager: Any             # 会话版本管理器
    mcp_manager: Any                 # MCP 工具管理器
    extension_manager: Any           # 扩展管理器
    capability_runtime: Any          # 能力运行时
    services: AppServices            # 服务外观层
    subagent_confirm: Any            # 子代理确认桥接
    session_id: str                  # 会话唯一标识
```

### 核心方法：build()

`AppRuntime.build()` 是一个**类方法**，按以下 12 步顺序组装所有组件：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 触发 `BEFORE_APP_BUILD` 钩子 | 允许扩展在组装前拦截 |
| 2 | 加载 `Settings` | Pydantic 懒加载单例 |
| 3 | 创建 `AgentLLM` | LLM 客户端，基于 litellm |
| 4 | 创建 `ToolRegistry` | 工具注册表 |
| 5 | 创建 `ConfirmBridge` | 子代理确认桥接 |
| 6 | 创建 `MCPToolManager` | MCP 动态工具管理 |
| 7 | 生成 `session_id` | 格式：`{profile}_{uuid12}` |
| 8 | 创建 `SessionTodoList` | SQLite 持久化的待办列表 |
| 9 | 发现并加载扩展 | `ExtensionManager.discover()` + `load_enabled()` |
| 10 | 注册所有工具 | 11 个工具提供者 + MCP |
| 11 | 创建记忆系统 | `MemoryManager` + `ConversationVersionManager` |
| 12 | 创建 ReAct 代理 | 配置对话模式、确认函数 |
| 13 | 发现技能并创建技能服务 | `SkillRegistry.discover()` + `SkillService` |
| 14 | 创建能力运行时 | `CapabilityRuntime` |
| 15 | 配置可观测性 | Trace 目录 + 告警管道 |
| 16 | 触发 `AFTER_APP_BUILD` 钩子 | 允许扩展在组装后修改 |
| 17 | 组装服务层 | `AppServices` 包含 5 个子服务 |

### 会话恢复

```python
@staticmethod
def _restore_memory_from_version(memory, version) -> None:
```

当 `restore_session=True` 时，从 `ConversationVersionManager` 获取最新的 STM 快照，恢复短期记忆的消息列表和摘要。

### 生命周期管理

```python
def close(self) -> None:     # 关闭 MCP 连接
def __enter__(self):          # 支持 with 语句
def __exit__(self, *exc):     # 自动调用 close()
```

## 依赖关系图

```
Settings ──────────────────────────────────────────────┐
    │                                                   │
    ├──→ AgentLLM ──→ ReActAgent                        │
    │        │                                          │
    │        └──→ MemoryManager                         │
    │                                                   │
    ├──→ ToolRegistry ──→ register_all_tools()          │
    │        │                                          │
    │        ├──→ MemoryToolProvider                    │
    │        ├──→ SearchToolProvider                    │
    │        ├──→ FilesystemToolProvider                │
    │        ├──→ ExecutionToolProvider                 │
    │        ├──→ SubagentToolProvider                  │
    │        ├──→ McpBridgeToolProvider                 │
    │        ├──→ TodoToolProvider                      │
    │        ├──→ CodeGraphToolProvider                 │
    │        ├──→ BrowserToolProvider                   │
    │        └──→ ComputerUseToolProvider               │
    │                                                   │
    ├──→ ExtensionManager                               │
    ├──→ MCPToolManager                                 │
    ├──→ SkillRegistry ──→ SkillService                 │
    ├──→ CapabilityRuntime                              │
    └──→ AppServices                                    │
         ├──→ ChatService                               │
         ├──→ SkillService                              │
         ├──→ KnowledgeBaseService                      │
         ├──→ EvalService                               │
         └──→ ConfigService                             │
```

## 设计模式

| 模式 | 应用 |
|------|------|
| **Builder** | `AppRuntime.build()` 是一个建造者方法，逐步组装复杂对象 |
| **Singleton** | `Settings` 使用懒加载单例 |
| **Facade** | `AppServices` 对外暴露统一的服务接口 |
| **Dependency Injection** | 所有组件通过构造函数注入依赖 |
| **Hook System** | `BEFORE_APP_BUILD` / `AFTER_APP_BUILD` 钩子支持扩展点 |
| **Context Manager** | 支持 `with AppRuntime.build() as runtime:` 用法 |

## 使用方式

### 基本用法

```python
from agentnexus.app import AppRuntime

# 组装并使用
runtime = AppRuntime.build(profile="cli")
result = runtime.services.chat.send_message("你好")
runtime.close()
```

### 带会话恢复

```python
with AppRuntime.build(
    profile="tui",
    session_id="my_session_123",
    restore_session=True,
) as runtime:
    # 之前的对话记忆已恢复
    result = runtime.services.chat.send_message("继续上次的对话")
```

### Server 模式

```python
runtime = AppRuntime.build(profile="server")
# LLM 设置为静默模式 (llm.silent = True)
# agent_output 设置为 lambda _: None
```

## 与其他模块的关系

| 模块 | 关系 |
|------|------|
| `core` | 使用 `Settings`、`AgentLLM` |
| `agents` | 创建 `ReActAgent` |
| `tools` | 调用 `register_all_tools()` 注册工具 |
| `memory` | 创建 `MemoryManager`、`ConversationVersionManager` |
| `skills` | 创建 `SkillRegistry`、`SkillService` |
| `extensions` | 创建 `ExtensionManager` |
| `capabilities` | 创建 `CapabilityRuntime` |
| `observability` | 配置 `trace_manager`、告警管道 |
| `services` | 组装 `AppServices` |
| `core/hooks` | 触发 `BEFORE_APP_BUILD` / `AFTER_APP_BUILD` 钩子 |
