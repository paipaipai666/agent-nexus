> **[中文](Observability-Detailed.md) | [English](Observability-Detailed.en.md)**

# 📊 Observability 可观测性模块（详细版）

## 概述

`observability` 模块提供 AgentNexus 的全面可观测性能力，包括结构化追踪、Token 统计、审计日志和告警系统。

## 模块结构

| 文件 | 职责 |
| --- | --- |
| `tracer.py` | 结构化 Trace 系统（Span 树） |
| `stats.py` | Token 使用统计和成本计算 |
| `audit_log.py` | 工具调用审计日志 |
| `alerting.py` | 告警系统 |
| `drift_detector.py` | 漂移检测器 |

## Trace 系统

### 核心类

```python
@dataclass
class TraceSpan:
    span_id: str                      # Span 唯一标识
    parent_span_id: str = ""          # 父 Span ID
    name: str = ""                    # Span 名称
    start_time: float = 0.0           # 开始时间
    end_time: float = 0.0             # 结束时间
    input: dict[str, Any] = {}        # 输入数据
    output: dict[str, Any] = {}       # 输出数据
    metadata: dict[str, Any] = {}     # 元数据

    @property
    def latency_ms(self) -> float     # 延迟毫秒数
    @property
    def status(self) -> str           # 状态 (ok/error)

class TraceContext:
    def start_span(name, input_data) -> TraceSpan   # 开始 Span
    def end_span(span, output_data, metadata)        # 结束 Span

class TraceManager:
    def configure(output_dir)          # 配置输出目录
    def start_trace(trace_id) -> TraceContext  # 开始追踪
    def flush()                        # 刷新到磁盘
```

### Trace 输出格式

每个 Trace 写入 `~/.agentnexus/traces/{trace_id}.jsonl`，每行一个 Span：

```json
{
  "trace_id": "abc123",
  "span_id": "def456",
  "parent_span_id": "",
  "name": "agent_run",
  "start_time": 1234567890.0,
  "end_time": 1234567891.5,
  "input": {"question": "Hello"},
  "output": {"answer": "Hi there!"},
  "metadata": {"status": "ok", "tokens": 150}
}
```

### Span 树结构

```
trace_id: abc123
├── agent_run (1500ms)
│   ├── llm_call_1 (800ms)
│   │   └── tool_call: file_read (50ms)
│   ├── llm_call_2 (600ms)
│   │   └── tool_call: python_execute (200ms)
│   └── emit_answer (100ms)
```

## Token 统计

### compute_stats()

```python
def compute_stats(days: int = 7) -> dict:
    """计算 Token 使用统计"""
    return {
        "total_input_tokens": int,
        "total_output_tokens": int,
        "total_cost_cny": float,
        "by_model": dict[str, ModelStats],
        "daily": list[DailyStats],
    }
```

### 统计维度

| 维度 | 说明 |
| --- | --- |
| 按模型 | 每个模型的 Token 使用量和成本 |
| 按日期 | 每日 Token 使用趋势 |
| 按会话 | 每个会话的 Token 消耗 |

## 审计日志

### ThreadSafeAuditLog

```python
class ThreadSafeAuditLog:
    def log(tool_name, params, result, caller, duration_ms, risk_level)
    def get_recent(limit, tool_filter) -> list[AuditEntry]
```

### 审计记录

```python
@dataclass
class AuditEntry:
    timestamp: str
    tool_name: str
    params: dict          # 敏感参数已脱敏
    result_summary: str
    caller: str
    duration_ms: float
    risk_level: str
```

## 告警系统

### AlertManager

```python
class AlertManager:
    def check(trace_id, spans) -> list[Alert]
    def get_history(days, severity) -> list[Alert]
```

### 告警规则

| 规则 | 严重级别 | 说明 |
| --- | --- | --- |
| 高延迟 | WARNING | 单次 LLM 调用 > 30s |
| Token 超限 | WARNING | 单次请求 Token > 阈值 |
| 工具失败 | ERROR | 工具调用连续失败 3 次 |
| 漂移检测 | INFO | 模型输出质量下降 |

## 漂移检测器

### DriftDetector

```python
class DriftDetector:
    def check(response_text, context) -> DriftResult
```

检测代理输出中的潜在问题：
- 幻觉（与上下文不符）
- 重复（重复相同内容）
- 格式异常（输出格式不符合预期）

## 模块依赖关系

```
TraceManager (tracer.py)
    └── TraceContext
         └── TraceSpan

Stats (stats.py)
    ├── TraceManager (读取 trace 文件)
    └── Pricing (core/pricing.py)

AuditLog (audit_log.py)
    └── PII (core/pii.py)  # 敏感参数脱敏

AlertManager (alerting.py)
    ├── TraceManager
    └── DriftDetector (drift_detector.py)
```
