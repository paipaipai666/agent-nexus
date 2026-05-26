# 📈 可观测性

## Trace 系统

- **存储**：`~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`（每日轮转）
- **留存**：`trace_retention_days`（默认 30）
- **Span 结构**：`{trace_id, span_id, parent_span_id, name, latency_ms, input/output(截断5000), metadata{status, tokens, model}}`
- **生命周期**：`start_trace()` → 根 span → 各组件 `span()` 上下文管理器 → `end_trace()` 一次性 flush

> ⚠ `end_trace()` 时写盘，`atexit` 注册退出 flush。异常崩溃丢未 flush 数据。

**Trace 来源**：

| Span 名称 | 来源 |
|-----------|------|
| `task: <desc>` | 根 span |
| `plan_node` | Agent 每一步 |
| `tool:<name>` | 每次工具调用 |
| `subagent` / `subagent_attempt` | 子代理 |

## Token 统计

`compute_stats()` 扫描 JSONL，按 trace_id 分组统计：

| 统计项 | 来源 |
|--------|------|
| 任务数/输入 token/输出 token | cumsum |
| 成本（CNY） | 内置定价表 |
| P50/P95/P99 延迟 | 百分位数 |
| 重试次数 | trace 计数 |

模型定价（人民币/百万 token）：

| 模型 | 输入 | 输出 |
|------|------|------|
| deepseek-v4-flash | ¥0.6 | ¥1.2 |
| deepseek-v4-pro | ¥1.0 | ¥4.0 |
| deepseek-r1 | ¥4.0 | ¥16.0 |
| qwen-max | ¥2.5 | ¥10.0 |
| gpt-4o | ¥17.5 | ¥70.0 |
| gpt-4o-mini | ¥1.0 | ¥4.0 |

## 审计日志

`ToolRegistry` 内存中维护 `AuditEntry` 列表，`nexus audit` 命令查看。
