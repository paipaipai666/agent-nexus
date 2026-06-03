> **[中文](Observability.md) | [English](Observability.en.md)**

# 📈 可观测性

AgentNexus 的可观测性系统基于文章《Harness的可观测性》框架设计，覆盖**七大可观测对象**：目标、计划、上下文、工具、状态、成本、评估。

## 架构总览

```text
┌─────────────────────────────────────────────────────────────────┐
│                    可观测性六层架构                                │
├─────────────────────────────────────────────────────────────────┤
│  第六层  改进闭环    │ 失败归因 → 评测样本 → 规则 → 监控           │
│  第五层  回放评测    │ 确定性回放 + 失败归因评估器                  │
│  第四层  可视化告警  │ CLI/API/GUI + AlertManager + 4 条内置规则    │
│  第三层  指标计算    │ TokenStats + 任务成功率 + 工具失败率          │
│  第二层  事件存储    │ JSONL 持久化 + 审计日志 + 参数脱敏            │
│  第一层  Trace 采集  │ TraceManager + 5 种 Span + 漂移检测          │
└─────────────────────────────────────────────────────────────────┘
```

## Trace 系统

- **存储**：`~/.agentnexus/traces/{YYYY-MM-DD}.jsonl`（每日轮转，100MB 上限自动切分）
- **留存**：`trace_retention_days`（默认 30）
- **崩溃安全**：每个 span 结束时立即写盘（`_flush_span`），`atexit` 注册退出 flush
- **生命周期**：`start_trace(task, metadata)` → 根 span → 各组件 `span()` 上下文管理器 → `end_trace()` flush

### Span 类型

| Span 名称 | 来源 | 记录内容 |
|-----------|------|---------|
| `task` | 根 span | user_goal、model_version、agent_id、max_steps |
| `plan_node` | Agent 每一步 | step_index、strategy、step_type |
| `llm` | 每次 LLM 调用 | model、tokens、tool_calls、context_refs（上下文来源引用） |
| `tool` | 每次工具调用 | tool_name、params、risk_level、schema_validation |
| `final_answer` | 最终答案 | answer、subagent 信息 |
| `hook_fire` | Hook 触发 | hook_type、elapsed_ms |

### 元数据字段（metadata）

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | string | `ok` / `error` |
| `model` | string | 使用的模型名 |
| `input_tokens` / `output_tokens` | int | Token 消耗 |
| `cache_hit_tokens` / `cache_miss_tokens` | int | Prompt cache 命中 |
| `step_type` | string | `plan` / `tool` / `observe` / `summarize` |
| `context_refs` | list | 上下文来源引用（system_prompt、tool_result:xxx） |
| `risk_level` | string | 工具风险等级（low / medium / high） |
| `schema_validation` | string | 参数校验结果（passed / failed） |
| `tool_selection_reason` | string | 工具选择原因 |
| `retry_count` | int | 重试次数 |

## 漂移检测

`DriftDetector` 在 Agent 执行过程中实时检测目标偏离，每 3 步自动检查一次。

### 五种漂移信号

| 信号类型 | 检测逻辑 | 严重度 |
|---------|---------|--------|
| `goal_drift` | 当前目标与原始目标关键词重叠率低于阈值 | warning / critical |
| `repeated_steps` | 连续 3 次相同工具调用，或同一工具占比过高 | warning |
| `subtask_overrun` | 已用步骤超过总步骤预算的 50% | warning |
| `goal_rewrite` | 目标被改写，与原始目标相似度低于 0.3 | warning |
| `unused_evidence` | 工具返回包含异常信号但后续步骤未引用 | warning |

检测到 critical 漂移时，系统自动注入提示让 Agent 重新聚焦原始目标。

## 工具故障归因

`classify_tool_fault()` 根据工具调用上下文自动分类故障类型：

| 故障类型 | 说明 | 严重度 |
|---------|------|--------|
| `tool_selection` | 模型选错了工具 | low |
| `param_generation` | 参数格式/值不正确 | medium |
| `permission` | 调用了不该调用的操作 | high |
| `tool_service` | 工具超时/500/依赖挂了 | medium |
| `result_understanding` | 工具返回正确但模型理解错了 | low |

### 七层失败归因

`FailureAttributionEvaluator` 从 trace 中自动归因失败任务：

1. **目标理解** — task span 是否存在，目标是否被改写
2. **上下文** — LLM span 的 context_refs 是否完整
3. **工具选择** — 工具调用是否合理
4. **工具参数** — schema_validation 是否通过
5. **权限** — 是否有权限错误
6. **成本** — Token 消耗是否超预算
7. **评估** — 是否产生最终答案

## 成本监控

`compute_stats()` 扫描 JSONL，聚合以下指标：

### 基础指标

| 指标 | 说明 |
|------|------|
| 任务数 / 输入 Token / 输出 Token | 累计 |
| 成本（CNY） | 内置定价表（DeepSeek/Qwen/GPT-4o/Claude/GLM） |
| P95/P99/最大延迟 | 百分位数 |
| 重试次数 | plan_node 计数 |
| Prompt cache 命中率 | cache_hit / (cache_hit + cache_miss) |

### 任务级指标（新增）

| 指标 | 说明 |
|------|------|
| 任务成功率 | 有 final_answer 的 trace 占比 |
| 工具失败率 | status=error 的 tool span 占比 |
| 平均上下文长度 | LLM 调用的 input_tokens 平均值 |
| 最大上下文长度 | 单次 LLM 调用的最大 input_tokens |

### 预算分层

在 `config.yaml` 中配置：

```yaml
budget_simple_max_tokens: 5000      # 简单任务预算
budget_complex_max_tokens: 50000    # 复杂任务预算
budget_high_value_max_tokens: 200000 # 高价值任务预算
budget_exceed_strategy: compress     # 超限策略: compress|downgrade|stop_subtask|degrade
```

## 告警系统

`AlertManager` 提供统一的告警管理，支持规则引擎和多通道通知。

### 内置规则

| 规则 | 触发条件 | 严重度 |
|------|---------|--------|
| `CostExceedRule` | Token 成本超过 10 CNY | warning |
| `ToolFailureSpikeRule` | 工具失败率超过 30%（≥5 次调用） | warning |
| `TaskSuccessRateRule` | 任务成功率低于 70%（≥3 个任务） | critical |
| `DriftCriticalRule` | 检测到 critical 漂移信号 | critical |

### 通知通道

| 通道 | 说明 |
|------|------|
| `ConsoleAlertChannel` | 打印到控制台（CLI 模式） |
| `LogAlertChannel` | 写入 `~/.agentnexus/alerts/alerts_{date}.jsonl` |

### API

```
GET /api/alerts?days=7&severity=critical   # 告警历史
GET /api/alerts/rules                       # 活跃规则列表
```

## 健康检查

`/health` 端点返回各子系统的 readiness 状态：

```json
{
  "status": "ok",
  "checks": {
    "llm": { "status": "ok", "model": "deepseek-v4-flash" },
    "mcp": { "status": "ok", "total": 3, "healthy": 3 },
    "memory": { "status": "ok", "memory_count": 42 },
    "traces_dir": { "status": "ok", "path": "~/.agentnexus/traces" },
    "disk_space": { "status": "ok", "free_gb": 120.5, "used_pct": 45.2 }
  },
  "uptime_seconds": 3600
}
```

任一子系统异常时，整体状态降级为 `degraded`，HTTP 返回 503。

## 审计日志

`ToolRegistry` 每次工具调用生成 `AuditEntry`，包含：

| 字段 | 说明 |
|------|------|
| `tool_name` / `caller` | 工具名和调用者 |
| `params` | 参数（已脱敏） |
| `result_summary` | 结果摘要（截断） |
| `duration_ms` | 执行耗时 |
| `hitl_triggered` | 是否触发人工确认 |
| `error` | 错误信息 |
| `risk_level` | 风险等级 |
| `schema_validation` | 参数校验结果 |
| `retry_count` | 重试次数 |
| `tool_selection_reason` | 工具选择原因 |

审计日志自动持久化到 `~/.agentnexus/audit/audit_{date}.jsonl`。

## 观测入口

### CLI 命令

```bash
nexus stats --days 7        # Token 成本统计 + 任务级指标
nexus audit --limit 20      # 审计日志
nexus logs list --days 7    # Trace 列表
nexus logs view --trace-id X  # Trace 详情（span 树）
nexus health                # 健康检查
nexus alerts --days 7       # 告警历史
```

### API 端点

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查（readiness probe） |
| `GET /api/stats?days=7` | Token 统计 |
| `GET /api/logs?days=7` | Trace 列表 |
| `GET /api/logs/{trace_id}` | Trace 详情 |
| `GET /api/audit?limit=50` | 审计日志 |
| `GET /api/alerts?days=7` | 告警历史 |
| `GET /api/alerts/rules` | 告警规则 |
| `GET /api/runtime/status` | 运行时状态 |

### 桌面端 GUI

| 页面 | 路由 | 功能 |
|------|------|------|
| Stats | `/stats` | 统计卡片 + Token 图表 + Trace 详情展开 |
| Health | `/health` | 健康检查仪表盘（5 项子系统） |
| Alerts | `/alerts` | 告警历史 + 规则列表 |
| Audit | `/audit` | 审计日志查看器（搜索/过滤） |
| StatusBar | 底部常驻 | 连接状态、上下文窗口、Token I/O |
| MCP Page | `/mcp` | MCP 服务器健康仪表盘 |

### 典型排查流程

```
1. nexus stats → 发现任务成功率下降
2. nexus logs list → 找到失败的 trace_id
3. nexus logs view --trace-id X → 查看 span 树，定位哪一步出错
4. 检查 tool span 的 metadata.status == "error"
5. 查看 metadata.schema_validation 和 metadata.risk_level
6. nexus audit → 查看审计日志中的参数脱敏和 HITL 记录
7. nexus alerts → 检查是否有相关告警
8. nexus health → 确认各子系统状态正常
```

## 模型定价（CNY/百万 token）

| 模型 | 输入 | 输出 |
|------|------|------|
| deepseek-v4-flash | ¥0.6 | ¥1.2 |
| deepseek-v4-pro | ¥1.0 | ¥4.0 |
| deepseek-r1 | ¥4.0 | ¥16.0 |
| qwen-max | ¥2.5 | ¥10.0 |
| gpt-4o | ¥17.5 | ¥70.0 |
| gpt-4o-mini | ¥1.0 | ¥4.0 |
