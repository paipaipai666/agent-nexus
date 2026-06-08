> **[中文](Server-Services-Detailed.md) | [English](Server-Services-Detailed.en.md)**

# 🌐 Server + Services 模块（详细版）

## 概述

- **Server** 模块：基于 FastAPI 的 HTTP/WebSocket API 服务器
- **Services** 模块：服务外观层，封装核心业务逻辑

## Server 模块

### 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                        │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Routes   │  │ Auth     │  │WebSocket │  │ Health   │   │
│  │ REST API │  │ 认证中间件│  │ 实时通信 │  │ 健康检查 │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### API 路由

| 路由前缀 | 模块 | 说明 |
| --- | --- | --- |
| `/api` | chat, alerts | 聊天接口、告警 |
| `/api/kb` | knowledge | 知识库管理 |
| `/api/memory` | memory | 记忆管理 |
| `/api/skills` | skills | Skill 管理 |
| `/api/stats` | stats | 统计信息 |
| `/api/config` | config | 配置管理 |
| `/api/audit` | audit | 审计日志 |
| `/api/codegraph` | codegraph | 代码知识图谱 |
| `/api/eval` | eval_routes | RAG 评估 |
| `/api/mcp` | mcp | MCP 工具管理 |
| `/api/version` | version | 版本信息 |
| `/api/runtime` | runtime | 运行时状态 |
| `/api/wiki` | wiki | 混合 Wiki + RAG 知识管理 |

### 核心文件

| 文件 | 职责 |
| --- | --- |
| `app.py` | FastAPI 应用创建和配置 |
| `auth.py` | JWT 认证中间件 |
| `health_checks.py` | 系统健康检查 |
| `routes/` | API 路由定义 |

## Services 模块

### 核心类：AppServices

```python
@dataclass
class AppServices:
    chat: ChatService
    skill: SkillService
    knowledge_base: KnowledgeBaseService
    eval: EvalService
    config: ConfigService
```

### 服务详情

| 服务 | 职责 |
| --- | --- |
| `ChatService` | 会话管理、消息发送、工具执行、记忆管理 |
| `SkillService` | 技能发现、路由、应用 |
| `KnowledgeBaseService` | 知识库 CRUD、搜索 |
| `EvalService` | 评估任务管理、运行、报告 |
| `ConfigService` | 配置读写、扩展管理 |

### ChatService

```python
class ChatService:
    def __init__(self, agent, memory, version,
                 skill_service, tool_executor, capability_runtime)
    def send_message(message) -> ChatResponse
    def get_history() -> list[Message]
    def clear_context()
    def compact()
```

## 模块依赖关系

```
FastAPI Server (server/)
    ├── Routes (server/routes/)
    │       ├── chat → ChatService
    │       ├── knowledge → KnowledgeBaseService
    │       ├── skills → SkillService
    │       ├── eval → EvalService
    │       └── config → ConfigService
    ├── Auth (server/auth.py)
    ├── Health (server/health_checks.py)
    └── AppRuntime (app/runtime.py)

AppServices (services/)
    ├── ChatService
    │       ├── ReActAgent (agents/)
    │       ├── MemoryManager (memory/)
    │       ├── ConversationVersionManager (memory/)
    │       ├── SkillService
    │       ├── ToolRegistry (tools/)
    │       └── CapabilityRuntime (capabilities/)
    ├── SkillService
    │       └── SkillRegistry (skills/)
    ├── KnowledgeBaseService
    │       └── RAG Retriever (rag/)
    ├── EvalService
    │       └── Evaluation (evaluation/)
    └── ConfigService
            └── ExtensionManager (extensions/)
```
