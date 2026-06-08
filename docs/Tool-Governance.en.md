> **[中文](Tool-Governance.md) | [English](Tool-Governance.en.md)**

# 🔧 Tool Governance System

All tool invocations pass through `ToolRegistry.invoke()`, executing 7 security gates sequentially.

## Seven Security Gates

### ① RBAC
`ToolMeta.allowed_agents` controls which callers are allowed. `"*"` is a wildcard. High-risk tools are restricted to whitelisted agents only.

### ② Schema Validation
JSON Schema validates parameter structure. Validator is compiled and cached at registration time.

### ③ Rate Limiting
Sliding window counter (60s). `python_execute` and `shell_exec` are not rate-limited.

### ④ Timeout Control
`ThreadPoolExecutor(max_workers=4)` + `future.result(timeout=N)`.

### ⑤ Risk Classification

| Level | Behavior |
| --- | --- |
| LOW | Pass through |
| MEDIUM | Requires HITL confirmation |
| HIGH | Requires confirmation + sandbox |

### ⑥ HITL Confirmation
`ConfirmBridge` is pluggable: TUI dialog / stdin / auto-approve.

### ⑦ Audit Logging
Each call records `AuditEntry{tool, caller, params(masked), duration, hitl, error}`.

## Tool Registration

`ToolProvider` protocol, 11 providers registered in order:

```text
MemoryToolProvider       → memory_search, memory_save
SearchToolProvider       → grep_search, web_search, web_fetch, kb_search
FilesystemToolProvider   → file_read, file_list, file_write
ExecutionToolProvider    → python_execute, shell_exec
SubagentToolProvider     → subagent_run
McpBridgeToolProvider    → MCP dynamic import
TodoToolProvider         → todo_add, todo_update, todo_list
CodeGraphToolProvider    → codegraph_search, codegraph_relations, codegraph_context
BrowserToolProvider      → browser_navigate, browser_snapshot, browser_click, browser_type,
                           browser_read, browser_screenshot, browser_evaluate, browser_wait,
                           browser_scroll, browser_scroll_to, browser_wait_navigation,
                           browser_dismiss_popup, browser_list_pages, browser_switch_page
ComputerUseToolProvider  → computer_snapshot, computer_list_windows, computer_switch_window,
                           computer_launch, computer_click, computer_type, computer_key,
                           computer_select, computer_toggle, computer_scroll
```

## Built-in Tool Parameters

| Tool | Parameters | Rate Limit | Risk |
| --- | --- | --- | --- |
| `memory_search` | `query`, `category?` | 10/min | LOW |
| `memory_save` | `content`, `category?`, `importance?` | 10/min | LOW |
| `grep_search` | `pattern`, `path?`, `glob?`, `max_results?`, `literal?` | 20/min | LOW |
| `web_search` | `query`, `max_results?`, `search_depth?`, `time_range?`, `topic?`, `include_answer?` | 10/min | LOW |
| `web_fetch` | `url`, `max_chars?`, `extract_mode?` | 10/min | LOW |
| `kb_search` | `query`, `namespace?`, `top_k?`, `view?`, 6 filters | 20/min | LOW |
| `file_read` | `path`, `offset?`, `limit?` | 30/min | LOW |
| `file_list` | `path?`, `pattern?` | 20/min | LOW |
| `file_write` | `path`, `content`, `mode?`, `expected_version?` | 20/min | MEDIUM |
| `python_execute` | `code` | Unlimited | HIGH |
| `shell_exec` | `command`, `cwd?`, `timeout?` | Unlimited | HIGH |
| `subagent_run` | `task`, `role?`, `allowed_tools?`, `max_steps?` | 10/min | LOW |
| `todo_add` | `description` | Unlimited | LOW |
| `todo_update` | `item_id`, `status` | Unlimited | LOW |
| `todo_list` | No parameters | Unlimited | LOW |
| `codegraph_search` | `query`, `kind?`, `limit?` | 20/min | LOW |
| `codegraph_relations` | `symbol`, `relation` | 20/min | LOW |
| `codegraph_context` | `symbol` | 20/min | LOW |
| `browser_navigate` | `url`, `wait_until?`, `task_id?` | 10/min | LOW |
| `browser_snapshot` | `scope?`, `mode?`, `include_offscreen?`, `task_id?` | 20/min | LOW |
| `browser_click` | `ref?`, `role?`, `name?`, `selector?`, `double_click?`, `pos?`, `task_id?` | 20/min | MEDIUM |
| `browser_type` | `ref?`, `role?`, `name?`, `selector?`, `text`, `clear?`, `press_enter?`, `pos?`, `task_id?` | 20/min | MEDIUM |
| `browser_read` | `selector?`, `ref?`, `max_chars?`, `task_id?` | 30/min | LOW |
| `browser_screenshot` | `path?`, `full_page?`, `task_id?` | 10/min | LOW |
| `browser_evaluate` | `expression`, `task_id?` | 10/min | HIGH |
| `browser_wait` | `role?`, `name?`, `ref?`, `text?`, `timeout?`, `task_id?` | 20/min | LOW |
| `browser_scroll` | `direction?`, `amount?`, `task_id?` | 20/min | LOW |
| `browser_scroll_to` | `landmark?`, `ref?`, `selector?`, `task_id?` | 20/min | LOW |
| `browser_wait_navigation` | `url_contains?`, `timeout?`, `task_id?` | 10/min | LOW |
| `browser_dismiss_popup` | No parameters | 10/min | LOW |
| `browser_list_pages` | No parameters | 30/min | LOW |
| `browser_switch_page` | `index` | 30/min | LOW |
| `computer_snapshot` | `app_name?`, `window_title?`, `mode?`, `task_id?` | 10/min | LOW |
| `computer_list_windows` | `task_id?` | 30/min | LOW |
| `computer_switch_window` | `window_index?`, `app_name?`, `window_title?`, `task_id?` | 30/min | LOW |
| `computer_launch` | `app_path`, `args?`, `task_id?` | 10/min | MEDIUM |
| `computer_click` | `element_id`, `button?`, `clicks?`, `role?`, `name?`, `task_id?` | 20/min | MEDIUM |
| `computer_type` | `element_id`, `text`, `clear?`, `role?`, `name?`, `task_id?` | 20/min | MEDIUM |
| `computer_key` | `keys`, `task_id?` | 30/min | MEDIUM |
| `computer_select` | `element_id`, `value`, `role?`, `name?`, `task_id?` | 20/min | MEDIUM |
| `computer_toggle` | `element_id`, `checked?`, `role?`, `name?`, `task_id?` | 20/min | MEDIUM |
| `computer_scroll` | `element_id?`, `direction?`, `amount?`, `task_id?` | 30/min | LOW |

> See [Code Execution](Code-Execution.en.md) for sandbox details, [MCP Integration](MCP-Integration.en.md) for external tool integration, [Browser Automation](Browser-Automation.en.md) for browser automation, [Computer Use](Computer-Use.en.md) for desktop automation.
