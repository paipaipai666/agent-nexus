> **[中文](Architecture.md) | [English](Architecture.en.md)**

# 🏗 整体架构

## 分层架构

```
┌───────────────────────────────────────────────────────────┐
│              CLI 层 (Typer + Rich)                          │
│  6 顶层命令 + 7 子命令组 = 40+ 入口                         │
│  nexus init / config / tui / stats / audit / ver           │
│  nexus kb / wiki / memory / logs / eval / skill / codegraph│
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│              服务外观层 (Services)                           │
│  ChatService  │  SkillService  │  AppServices               │
│  会话管理/事件  │  Skill 路由/前置 │  组合各类服务             │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│           ReActAgent (FSM 驱动)                             │
│  16 状态 × 25 转移规则                                      │
│  CallingStrategy 三级: Native → JSON → Prompt JSON          │
│  AgentLLM (litellm 流式, 3 次指数退避)                       │
│  工具 batch 顺序执行, max_steps 硬终止                        │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│         ToolRegistry 治理网关 (7 道关卡)                     │
│  RBAC → Schema → 限流 → 超时 → 风险 → HITL → 审计           │
│  18 内置工具 + MCP 动态导入 + 子代理隔离                      │
└──────────────────┬────────────────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────────────────┐
│              本地基础设施层                                  │
│  ChromaDB(向量)  SQLite(关系型)  JSONL(追踪)                │
│  SentenceTransformers + BM25 + BGE-Reranker                │
│  E2B / bubblewrap / Docker / 本地沙箱                       │
│  Playwright (浏览器自动化)  OS 无障碍 API (桌面自动化)       │
└───────────────────────────────────────────────────────────┘
```

## 项目结构

```
agentnexus/
├── __main__.py              ── python -m 入口
├── app/runtime.py            ── AppRuntime 依赖组装
├── cli/                      ── Typer CLI 层
├── agents/                   ── ReActAgent + FSM
├── core/                     ── Settings + LLM
├── codegraph/                ── 代码知识图谱 (AST 解析/语义搜索)
├── evaluation/               ── 8 个评估器
├── extensions/               ── 插件系统
├── memory/                   ── STM/LTM/版本控制/压缩/反射/卸载/投影/提取
├── observability/            ── Trace + Token 统计
├── prompts/                  ── 提示词模板
├── rag/                      ── ChromaDB 客户端/检索/分块
├── server/                   ── FastAPI API 服务器
├── services/                 ── 服务外观层
├── skills/                   ── Skill 发现/路由/运行时
├── storage/                  ── 存储抽象层
├── tools/                    ── 注册表/提供者/MCP/浏览器
│   └── computer_use/         ── 桌面自动化 (OS 无障碍 API)
├── tui/                      ── Textual 界面
└── wiki/                     ── 混合 Wiki + RAG 知识管理
```

## 工具提供者 (Tool Providers)

系统使用 `ToolProvider` 协议，11 个提供者按顺序注册：

| 提供者 | 工具 | 说明 |
| --- | --- | --- |
| `MemoryToolProvider` | `memory_search`, `memory_save` | 长期记忆检索与保存 |
| `SearchToolProvider` | `grep_search`, `web_search`, `web_fetch`, `kb_search` | 搜索工具集 |
| `FilesystemToolProvider` | `file_read`, `file_list`, `file_write` | 文件操作 |
| `ExecutionToolProvider` | `python_execute`, `shell_exec` | 代码执行 (沙箱) |
| `SubagentToolProvider` | `subagent_run` | 子代理委派 |
| `McpBridgeToolProvider` | MCP 动态导入 | 外部工具集成 |
| `TodoToolProvider` | `todo_add`, `todo_update`, `todo_list` | 待办事项管理 |
| `CodeGraphToolProvider` | `codegraph_search`, `codegraph_relations`, `codegraph_context` | 代码知识图谱 |
| `BrowserToolProvider` | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_read`, `browser_screenshot`, `browser_evaluate`, `browser_wait`, `browser_scroll`, `browser_scroll_to` | 浏览器自动化 |
| `ComputerUseToolProvider` | `computer_snapshot`, `computer_list_windows`, `computer_switch_window`, `computer_launch`, `computer_click`, `computer_type`, `computer_key`, `computer_select`, `computer_toggle`, `computer_scroll` | 桌面自动化 (OS 无障碍 API) |

## 服务启动顺序

`AppRuntime.build()` 按以下顺序组装所有组件：

1. 加载 `Settings`（Pydantic 懒加载单例）
2. 创建 `AgentLLM` + `ToolExecutor` + `ConfirmBridge`
3. 初始化 `MCPToolManager`（若 `mcp_enabled=True`）
4. 加载 `ExtensionManager`
5. `register_all_tools()` — 注册 11 个提供者 + MCP
6. 创建 `MemoryManager` + `ConversationVersionManager`
7. 创建 `ReActAgent`
8. `SkillRegistry.discover()` — 扫描 skill 目录
9. 创建 `SkillService`
10. 配置 Trace 输出目录
11. 组装 `AppServices`（Chat/Config/Eval/KB/Skill）
12. 返回 `AppRuntime` 实例

> 见 [ReAct Agent](ReAct-Agent.md) 了解 FSM 细节，[工具治理](Tool-Governance.md) 了解 7 道关卡，[浏览器自动化](Browser-Automation.md) 了解 Playwright 集成，[应用运行时](App-Runtime.md) 了解完整组装流程。

## 模块详细文档

| 模块 | 文档 | 说明 |
| --- | --- | --- |
| Core 核心 | [Core-Detailed.md](Core-Detailed.md) | 配置、LLM、能力检测、钩子、Provider |
| App Runtime | [App-Runtime.md](App-Runtime.md) | 统一组装层、依赖注入、生命周期管理 |
| Agents 代理 | [Agents-Detailed.md](Agents-Detailed.md) | FSM 状态机、16 状态 × 25 转移、四级策略 |
| Tools 工具 | [Tools-Detailed.md](Tools-Detailed.md) | 7 道关卡、11 个提供者、MCP、浏览器、桌面 |
| Skills 技能 | [Skills-Detailed.md](Skills-Detailed.md) | 发现、路由、运行时、SKILL.md 格式 |
| Memory + RAG | [Memory-RAG-Detailed.md](Memory-RAG-Detailed.md) | STM/LTM/版本/压缩 + RAG 检索/重排 |
| Wiki 系统 | [Wiki-System-Detailed.md](Wiki-System-Detailed.md) | 混合 Wiki+RAG、机械验证、图传播、校准 |
| Prompts 提示词 | [Prompts-Detailed.md](Prompts-Detailed.md) | 模板加载、片段组合 |
| Evaluation 评估 | [Evaluation-Detailed.md](Evaluation-Detailed.md) | 8 个评估器、任务系统 |
| Observability | [Observability-Detailed.md](Observability-Detailed.md) | Trace/Token 统计/审计/告警 |
| Extensions 扩展 | [Extensions-Detailed.md](Extensions-Detailed.md) | 插件系统、能力运行时 |
| Server + Services | [Server-Services-Detailed.md](Server-Services-Detailed.md) | FastAPI 服务器、服务外观层 |
| Storage + CodeGraph | [Storage-Codegraph-Detailed.md](Storage-Codegraph-Detailed.md) | ChromaDB、代码知识图谱 |
| CLI + TUI | [CLI-TUI-Detailed.md](CLI-TUI-Detailed.md) | 40+ CLI 命令、Textual TUI |
| 浏览器自动化 | [Browser-Automation.md](Browser-Automation.md) | Playwright 集成 |
| 桌面自动化 | [Computer-Use.md](Computer-Use.md) | OS 无障碍 API |
| MCP 集成 | [MCP-Integration.md](MCP-Integration.md) | 动态工具导入 |

## 架构图

| 图 | 文件 | 说明 |
| --- | --- | --- |
| 整体架构 | [diagrams/overall-architecture.drawio](diagrams/overall-architecture.drawio) | 分层架构 + 模块组 |
| Wiki 系统 | [diagrams/wiki-architecture.drawio](diagrams/wiki-architecture.drawio) | Wiki 内部组件交互 |
| AppRuntime 构建 | [diagrams/app-runtime-build.drawio](diagrams/app-runtime-build.drawio) | 17 步组装流程 |

## API 路由

服务器提供以下 REST 路由（FastAPI，前缀 `/api`）：

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
