> **[中文](MCP-Integration.md) | [English](MCP-Integration.en.md)**

# 🔌 MCP 集成

MCP（Model Context Protocol）支持从外部服务导入工具、资源和提示词。

## 传输方式

| 类型 | 配置 | 场景 |
|------|------|------|
| `stdio` | `command` + `args` | 本地子进程 |
| `streamable_http` | `url` + `headers` | 远程 HTTP |

## 导入范围

| 类别 | 配置开关 | 行为 |
|------|----------|------|
| `tools` | `import_tools` | 注册为 `mcp_{server}__{tool}` |
| `resources` | `import_resources` | 转为 list/read 工具 |
| `prompts` | `import_prompts` | 转为 list/get 工具 |

## 治理融合

MCP 工具自动获得 ToolRegistry 全部 7 道关卡。

## 重要配置项

| MCPServerConfig 字段 | 默认值 | 说明 |
|----------------------|--------|------|
| `transport` | `"stdio"` | stdio / streamable_http |
| `risk_level` | `"medium"` | 关联工具治理风险分级 |
| `require_hitl` | `false` | |
| `rate_limit_per_min` | `10` | |
| `timeout_sec` | `60` | |
| `allowed_agents` | `[react_agent, subagent_explorer, subagent_executor]` | |
| `health_check_interval_sec` | `30` | |
| `reconnect_max_attempts` | `0` | 0=无限 |
| `max_concurrency_per_server` | `4` | |

## 配置示例

```yaml
mcp_enabled: true
mcp_servers:
  - name: local-docs
    transport: stdio
    command: python
    args: [mcp_server.py]
    risk_level: medium
```
