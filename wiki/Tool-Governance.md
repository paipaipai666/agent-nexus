> **[中文](Tool-Governance.md) | [English](Tool-Governance.en.md)**

# 🔧 工具治理系统

所有工具调用必经 `ToolRegistry.invoke()`，顺序执行 7 道安全关卡。

## 七道关卡

### ① RBAC
`ToolMeta.allowed_agents` 控制调用方。`"*"` 通配。高风险工具仅白名单 Agent 可用。

### ② Schema 校验
JSON Schema 校验参数结构，注册时自动编译缓存校验器。

### ③ 速率限制
滑动窗口计数器（60 秒），`python_execute` 和 `shell_exec` 无限速。

### ④ 超时控制
`ThreadPoolExecutor(max_workers=4)` + `future.result(timeout=N)`。

### ⑤ 风险分级
| 级别 | 行为 |
|------|------|
| LOW | 直接放行 |
| MEDIUM | 需 HITL 确认 |
| HIGH | 需确认 + 安全沙箱 |

### ⑥ HITL 确认
`ConfirmBridge` 可插拔：TUI 窗口 / 标准输入 / 自动批准。

### ⑦ 审计日志
每次调用记 `AuditEntry{tool, caller, params(脱敏), duration, hitl, error}`。

## 工具注册

`ToolProvider` 协议，6 个提供者按顺序注册：

```python
MemoryToolProvider    → memory_search, memory_save
SearchToolProvider    → grep_search, web_search, kb_search
FilesystemToolProvider → file_read, file_list, file_write
ExecutionToolProvider  → python_execute, shell_exec
SubagentToolProvider   → subagent_run
McpBridgeToolProvider  → MCP 动态导入
```

## 内置工具参数

| 工具 | 参数 | 限流 | 风险 |
|------|------|------|------|
| `memory_search` | `query`, `category?` | 10/min | LOW |
| `memory_save` | `content`, `category?`, `importance?` | 10/min | LOW |
| `grep_search` | `pattern`, `path?`, `glob?`, `max_results?`, `literal?` | 20/min | LOW |
| `web_search` | `query`, `max_results?`, `search_depth?`, `time_range?`, `topic?`, `include_answer?` | 10/min | LOW |
| `kb_search` | `query`, `namespace?`, `top_k?`, `view?`, 6 种过滤 | 20/min | LOW |
| `file_read` | `path`, `offset?`, `limit?` | 30/min | LOW |
| `file_list` | `path?`, `pattern?` | 20/min | LOW |
| `file_write` | `path`, `content`, `mode?`, `expected_version?` | 20/min | MEDIUM |
| `python_execute` | `code` | 无限 | HIGH |
| `shell_exec` | `command`, `cwd?`, `timeout?` | 无限 | HIGH |
| `subagent_run` | `task`, `role?`, `allowed_tools?`, `max_steps?` | 10/min | LOW |

> 见 [Code-Execution](Code-Execution.md) 了解沙箱细节，[MCP-Integration](MCP-Integration.md) 了解外部工具集成。

## grep_search glob 模式

| 模式 | 说明 |
|------|------|
| `*.py` | 匹配所有 .py 文件（包括子目录） |
| `**/*.py` | 等同于 `*.py` |
| `test_*` | 匹配所有以 test_ 开头的文件（包括子目录） |
| `**/test_*` | 等同于 `test_*` |
| `[abc].py` | 匹配 a.py, b.py, c.py |
| `[!abc].py` | 匹配非 a.py, b.py, c.py 的文件 |
