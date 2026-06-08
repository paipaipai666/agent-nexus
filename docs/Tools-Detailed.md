> **[中文](Tools-Detailed.md) | [English](Tools-Detailed.en.md)**

# 🔧 Tools 工具模块（详细版）

## 概述

`tools` 模块是 AgentNexus 的工具治理网关，提供统一的工具注册、验证、执行和审计能力。所有工具必须通过 `ToolRegistry` 注册后才能被代理调用。

## 7 道治理关卡

```
工具调用请求
    │
    ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ 1. RBAC  │→│ 2. Schema│→│ 3. 限流  │→│ 4. 超时  │
│ 权限检查 │  │ 参数验证 │  │ 速率限制 │  │ 超时控制 │
└──────────┘  └──────────┘  └──────────┘  └──────────┘
                                              │
    ┌─────────────────────────────────────────┘
    ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ 5. 风险  │→│ 6. HITL  │→│ 7. 审计  │
│ 风险评级 │  │ 人工确认 │  │ 审计日志 │
└──────────┘  └──────────┘  └──────────┘
```

## 核心类

### ToolMeta（工具元数据）

```python
@dataclass
class ToolMeta:
    name: str                            # 唯一标识
    description: str                     # 人类可读描述（展示给 LLM）
    param_schema: dict                   # JSON Schema 输入验证
    allowed_agents: list[str] = ["*"]    # RBAC 白名单
    risk_level: RiskLevel = RiskLevel.LOW  # low | medium | high
    require_hitl: bool = False           # 是否需要人工确认
    timeout_sec: int = 30                # 超时秒数
    rate_limit_per_min: int = 0          # 速率限制（0=不限）
    source_type: str = "unknown"         # 来源类型
    source_id: str = "unknown"           # 来源 ID
```

### RiskLevel（风险等级）

| 等级 | 说明 | 示例 |
| --- | --- | --- |
| `LOW` | 只读查询 | search, file_read |
| `MEDIUM` | 写操作、网络请求 | file_write, web_fetch |
| `HIGH` | 代码执行、数据库写入 | python_execute, shell_exec |

### ToolRegistry（工具注册表）

```python
class ToolRegistry:
    def register(meta: ToolMeta, func: Callable)    # 注册工具
    def invoke(name, params, caller, hitl_approver, tool_policy) -> str  # 调用工具
    def list_tools() -> list[str]                    # 列出所有工具
    def get_meta(name) -> ToolMeta                   # 获取工具元数据
```

## 工具提供者 (ToolProvider)

### ProviderSpec

```python
@dataclass(frozen=True)
class ProviderSpec:
    name: str
    version: str = "1.0"
    default_enabled: bool = True
    required_config: tuple[str, ...] = ()
    exposed_agents: tuple[str, ...] = ("*",)
```

### 11 个内置提供者

| 提供者 | 工具 | 说明 |
| --- | --- | --- |
| `MemoryToolProvider` | memory_search, memory_save | 长期记忆检索与保存 |
| `SearchToolProvider` | grep_search, web_search, web_fetch, kb_search | 搜索工具集 |
| `FilesystemToolProvider` | file_read, file_list, file_write | 文件操作 |
| `ExecutionToolProvider` | python_execute, shell_exec | 代码执行 (沙箱) |
| `SubagentToolProvider` | subagent_run | 子代理委派 |
| `McpBridgeToolProvider` | MCP 动态导入 | 外部工具集成 |
| `TodoToolProvider` | todo_add, todo_update, todo_list | 待办事项管理 |
| `CodeGraphToolProvider` | codegraph_search, codegraph_relations, codegraph_context | 代码知识图谱 |
| `BrowserToolProvider` | browser_navigate, browser_snapshot, browser_click, ... | 浏览器自动化 |
| `ComputerUseToolProvider` | computer_snapshot, computer_click, computer_type, ... | 桌面自动化 |

## MCP 集成

### MCPToolManager

管理 MCP (Model Context Protocol) 服务器连接和工具导入。

| 文件 | 职责 |
| --- | --- |
| `mcp_adapter.py` | MCP 管理器创建和配置 |
| `mcp_connection.py` | 连接管理（stdio/HTTP） |
| `mcp_call.py` | MCP 工具调用 |
| `mcp_health.py` | 健康检查和重连 |
| `mcp_lifecycle.py` | 生命周期管理 |
| `mcp_schema.py` | Schema 转换 |
| `mcp_capabilities.py` | 能力检测 |
| `mcp_descriptors.py` | 工具描述符 |
| `mcp_result.py` | 结果处理 |

## 浏览器自动化

**文件**：`tools/browser.py`

基于 Playwright 的浏览器自动化工具集：

| 工具 | 说明 |
| --- | --- |
| `browser_navigate` | 导航到 URL |
| `browser_snapshot` | 获取页面快照（无障碍树） |
| `browser_click` | 点击元素 |
| `browser_type` | 输入文本 |
| `browser_read` | 读取页面内容 |
| `browser_screenshot` | 截图 |
| `browser_evaluate` | 执行 JavaScript |
| `browser_wait` | 等待条件 |
| `browser_scroll` / `browser_scroll_to` | 滚动 |

## 桌面自动化

**目录**：`tools/computer_use/`

基于 OS 无障碍 API 的桌面自动化：

| 文件 | 职责 |
| --- | --- |
| `manager.py` | 计算机使用管理器 |
| `snapshot.py` | 桌面快照（无障碍树） |
| `element.py` | UI 元素抽象 |
| `tools.py` | 工具注册 |
| `backends/base.py` | 后端抽象基类 |
| `backends/windows_backend.py` | Windows 后端 |
| `backends/macos_backend.py` | macOS 后端 |
| `backends/linux_backend.py` | Linux 后端 |

## 模块依赖关系

```
ToolRegistry (registry.py)
    ├── ToolMeta
    ├── RiskLevel
    ├── ThreadSafeAuditLog (observability/audit_log.py)
    ├── TraceManager (observability/tracer.py)
    └── ToolProviderContext (providers.py)
         ├── MemoryToolProvider
         ├── SearchToolProvider
         ├── FilesystemToolProvider
         ├── ExecutionToolProvider
         ├── SubagentToolProvider
         ├── McpBridgeToolProvider
         ├── TodoToolProvider
         ├── CodeGraphToolProvider
         ├── BrowserToolProvider
         └── ComputerUseToolProvider
```
